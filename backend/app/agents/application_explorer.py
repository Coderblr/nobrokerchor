import json
import time
from datetime import datetime, timezone
from urllib.parse import urldefrag, urljoin, urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

from app.agents.base import AgentContext, AgentResult, BaseAgent
from app.models.misc import ApplicationInventory

FIELD_EXTRACTION_JS = """
const fields = [];
document.querySelectorAll('input, select, textarea, button').forEach(el => {
    let label = '';
    if (el.id) {
        const labelEl = document.querySelector(`label[for="${el.id}"]`);
        if (labelEl) label = labelEl.textContent.trim();
    }
    if (!label && el.closest('label')) {
        label = el.closest('label').textContent.trim();
    }
    fields.push({
        tag: el.tagName.toLowerCase(),
        type: el.type || null,
        name: el.name || null,
        id: el.id || null,
        label: label || null,
        text: el.tagName.toLowerCase() === 'button' ? el.textContent.trim() : null,
    });
});
return fields;
"""

LABEL_LOOKUP_JS = """
const el = arguments[0];
if (el.id) {
    const l = document.querySelector(`label[for="${el.id}"]`);
    if (l) return l.textContent;
}
const parent = el.closest('label');
return parent ? parent.textContent : '';
"""

LINK_EXTRACTION_JS = "return Array.from(document.querySelectorAll('a[href]')).map(a => a.href);"

DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_DEPTH = 2
NAVIGATION_SETTLE_SECONDS = 2

LOGIN_BUTTON_HINTS = ["log in", "login", "sign in", "submit"]
SEARCH_FIELD_HINTS = ["transaction", "txn", "reference", "search"]
SEARCH_BUTTON_HINTS = ["search", "find", "go", "submit"]
FORWARD_BUTTON_HINTS = ["submit", "next", "continue", "proceed", "confirm", "search", "ok"]
USERNAME_FIELD_HINTS = ["username", "user name", "login id", "user id", "email", "login"]


def _build_driver(headless: bool = True) -> webdriver.Chrome:
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1400,1000")
    return webdriver.Chrome(options=options)


def _element_hint_text(driver: webdriver.Chrome, element) -> str:
    parts = [
        element.get_attribute("name") or "",
        element.get_attribute("id") or "",
        element.get_attribute("placeholder") or "",
        element.get_attribute("aria-label") or "",
    ]
    try:
        parts.append(driver.execute_script(LABEL_LOOKUP_JS, element) or "")
    except Exception:  # noqa: BLE001 - label lookup is best-effort
        pass
    return " ".join(parts).lower()


def _find_input_by_hints(driver: webdriver.Chrome, hints: list[str], input_type: str | None = None):
    for element in driver.find_elements(By.CSS_SELECTOR, "input, textarea"):
        if not element.is_displayed():
            continue
        if input_type and element.get_attribute("type") != input_type:
            continue
        hint_text = _element_hint_text(driver, element)
        if any(hint.lower() in hint_text for hint in hints):
            return element
    return None


def _find_clickable_by_hints(driver: webdriver.Chrome, hints: list[str]):
    for element in driver.find_elements(By.CSS_SELECTOR, "button, input[type='submit'], input[type='button'], a"):
        if not element.is_displayed():
            continue
        text = (element.text or "").lower()
        value = (element.get_attribute("value") or "").lower()
        combined = f"{text} {value}"
        if any(hint.lower() in combined for hint in hints):
            return element
    return None


def _attempt_login(driver: webdriver.Chrome, username: str, password: str) -> bool:
    """Best-effort: finds a password field (reliable - always type='password') and a username
    field (by label/name/placeholder hints, falling back to the first visible text input), fills
    both, and submits. Returns whether a login form was found and submitted - never raises, since
    a failed heuristic match shouldn't abort the rest of the exploration."""

    password_fields = [el for el in driver.find_elements(By.CSS_SELECTOR, "input[type='password']") if el.is_displayed()]
    if not password_fields:
        return False
    password_field = password_fields[0]

    username_field = _find_input_by_hints(driver, USERNAME_FIELD_HINTS)
    if username_field is None:
        text_inputs = [
            el for el in driver.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='email'], input:not([type])")
            if el.is_displayed()
        ]
        username_field = text_inputs[0] if text_inputs else None
    if username_field is None:
        return False

    username_field.clear()
    username_field.send_keys(username)
    password_field.clear()
    password_field.send_keys(password)

    submit = _find_clickable_by_hints(driver, LOGIN_BUTTON_HINTS)
    if submit is not None:
        submit.click()
    else:
        password_field.send_keys(Keys.RETURN)
    time.sleep(NAVIGATION_SETTLE_SECONDS)
    return True


def _attempt_transaction_search(driver: webdriver.Chrome, transaction_number: str) -> bool:
    search_field = _find_input_by_hints(driver, SEARCH_FIELD_HINTS)
    if search_field is None:
        return False

    search_field.clear()
    search_field.send_keys(transaction_number)
    submit = _find_clickable_by_hints(driver, SEARCH_BUTTON_HINTS)
    if submit is not None:
        submit.click()
    else:
        search_field.send_keys(Keys.RETURN)
    time.sleep(NAVIGATION_SETTLE_SECONDS)
    return True


def _fill_form_fields(driver: webdriver.Chrome, form_values: dict[str, str]) -> bool:
    """Matches each provided field_name against every visible input/select/textarea's hint text
    (name/id/placeholder/aria-label/associated label) and fills the first match. Returns whether
    anything was filled, so the caller knows whether attempting to move forward makes sense."""

    filled_any = False
    for element in driver.find_elements(By.CSS_SELECTOR, "input, textarea, select"):
        if not element.is_displayed() or not element.is_enabled():
            continue
        hint_text = _element_hint_text(driver, element)
        for field_name, value in form_values.items():
            if field_name.lower() not in hint_text:
                continue
            try:
                tag = element.tag_name.lower()
                if tag == "select":
                    Select(element).select_by_visible_text(str(value))
                else:
                    element.clear()
                    element.send_keys(str(value))
                filled_any = True
            except Exception:  # noqa: BLE001 - a single field mismatch shouldn't abort the fill pass
                pass
            break
    return filled_any


class ApplicationExplorerAgent(BaseAgent):
    """Crawls a real web application with a real headless browser (Python Selenium, separate
    from the generated Java/TS frameworks). Beyond passive link-following, it can also log in,
    search for a transaction by number, and walk forward through a multi-step form workflow by
    filling matched fields and submitting - all heuristic and best-effort, since there is no live
    NBC application in this environment to validate the heuristics against a real banking UI."""

    name = "ApplicationExplorerAgent"

    def run(self, context: AgentContext) -> AgentResult:
        base_url = context.state.get("base_url")
        if not base_url:
            return AgentResult(success=False, error="No base_url provided to explore")

        username = context.state.get("username")
        password = context.state.get("password")
        transaction_number = context.state.get("transaction_number")
        form_values: dict[str, str] = context.state.get("form_values") or {}
        max_pages = context.state.get("max_pages", DEFAULT_MAX_PAGES)
        max_depth = context.state.get("max_depth", DEFAULT_MAX_DEPTH)
        headless = context.state.get("headless", True)

        origin = urlparse(base_url)._replace(path="", query="", fragment="").geturl()
        visited: set[str] = set()
        pages_recorded: list[dict] = []
        notes: list[str] = []
        queue: list[tuple[str, int]] = []

        def record_and_queue_links(depth: int) -> None:
            """Records the current page (fields + DB row) and, unless at max depth, extracts its
            links into the BFS queue. Used for every page transition - the initial load, after
            login, after a transaction search, after each form-walk step, and during the BFS
            crawl itself - so no page's outbound links are silently dropped."""
            url = urldefrag(driver.current_url)[0]
            visited.add(url)
            fields = driver.execute_script(FIELD_EXTRACTION_JS)
            screen_name = driver.title or url
            context.db.add(ApplicationInventory(
                project_id=context.project_id, screen_name=screen_name,
                fields_json=json.dumps(fields), discovered_at=datetime.now(timezone.utc),
            ))
            context.db.commit()
            pages_recorded.append({"url": url, "screen_name": screen_name, "fields": fields})

            if depth >= max_depth:
                return
            try:
                links = driver.execute_script(LINK_EXTRACTION_JS)
            except Exception:  # noqa: BLE001
                links = []
            for link in links:
                absolute = urldefrag(urljoin(url, link))[0]
                if absolute.startswith(origin) and absolute not in visited:
                    queue.append((absolute, depth + 1))

        driver = _build_driver(headless=headless)
        try:
            try:
                driver.get(base_url)
                time.sleep(1.5)
            except Exception as exc:  # noqa: BLE001
                return AgentResult(success=False, error=f"Could not load base_url: {exc}")

            record_and_queue_links(depth=0)

            if username and password:
                logged_in = _attempt_login(driver, username, password)
                notes.append("Login form submitted" if logged_in else "No login form detected on the start page")
                if logged_in and len(pages_recorded) < max_pages:
                    record_and_queue_links(depth=0)

            if transaction_number:
                searched = _attempt_transaction_search(driver, transaction_number)
                notes.append(
                    f"Transaction search submitted for '{transaction_number}'" if searched
                    else "No transaction/search field detected on the current page"
                )
                if searched and len(pages_recorded) < max_pages:
                    record_and_queue_links(depth=0)

            if form_values:
                walked = 0
                while len(pages_recorded) < max_pages and walked < max_pages:
                    walked += 1
                    filled = _fill_form_fields(driver, form_values)
                    if not filled:
                        if walked == 1:
                            notes.append("No provided form field values matched any field on the current page")
                        break
                    forward = _find_clickable_by_hints(driver, FORWARD_BUTTON_HINTS)
                    if forward is None:
                        notes.append("Filled form field(s) but found no submit/next control to move forward")
                        break
                    try:
                        forward.click()
                    except Exception as exc:  # noqa: BLE001
                        notes.append(f"Failed to click forward control: {exc}")
                        break
                    time.sleep(NAVIGATION_SETTLE_SECONDS)
                    record_and_queue_links(depth=0)

            while queue and len(pages_recorded) < max_pages:
                url, depth = queue.pop(0)
                if url in visited:
                    continue
                try:
                    driver.get(url)
                except Exception as exc:  # noqa: BLE001 - a single bad page shouldn't abort the crawl
                    visited.add(url)
                    pages_recorded.append({"url": url, "error": str(exc)})
                    continue
                record_and_queue_links(depth)
        finally:
            driver.quit()

        summary = f"Explored {len(pages_recorded)} page(s) starting from {base_url}"
        if notes:
            summary += " (" + "; ".join(notes) + ")"

        return AgentResult(
            success=True,
            output={"pages": pages_recorded, "base_url": base_url, "notes": notes},
            output_summary=summary,
        )
