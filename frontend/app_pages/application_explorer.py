import pandas as pd
import streamlit as st

from services import api_client

st.title("Application Explorer")
st.caption(
    "Crawls a real web application with a real headless browser and records each screen's form fields, then "
    "asks the LLM to infer likely multi-step workflows from what it found. There is no live NBC application "
    "in this environment — point this at any reachable app (including a local one) to see it work for real."
)

try:
    projects = api_client.list_projects()
except Exception as exc:  # noqa: BLE001
    st.error(f"Failed to load projects: {exc}")
    st.stop()

if not projects:
    st.warning("Create a project first on the Projects page.")
    st.stop()

project_labels = {f"#{p['id']} — {p['name']}": p["id"] for p in projects}
selected_project_label = st.selectbox("Project", list(project_labels.keys()))
project_id = project_labels[selected_project_label]

base_url = st.text_input("Base URL to explore", placeholder="http://127.0.0.1:8000/docs")
col1, col2, col3 = st.columns(3)
with col1:
    max_pages = st.number_input("Max pages to crawl", min_value=1, max_value=50, value=10)
with col2:
    max_depth = st.number_input("Max link depth", min_value=1, max_value=5, value=2)
with col3:
    headless = not st.checkbox("Show browser (debug)", value=False)

with st.expander("Login credentials (optional)"):
    st.caption(
        "If the target app starts at a login screen, the agent finds the password field (always "
        "`type=password`) and the nearest text field by label/name heuristics, fills both, and submits."
    )
    login_col1, login_col2 = st.columns(2)
    with login_col1:
        username = st.text_input("Username", key="explorer_username")
    with login_col2:
        password = st.text_input("Password", type="password", key="explorer_password")

with st.expander("Find a transaction (optional)"):
    st.caption(
        "If provided, the agent looks for a search/transaction-number field on the current page after "
        "login, fills it in, and submits before crawling onward from the result."
    )
    transaction_number = st.text_input("Transaction number", key="explorer_transaction_number")

with st.expander("Fill form fields to move forward (optional)"):
    st.caption(
        "Field Name is matched against each input's label/name/id/placeholder (case-insensitive "
        "substring match) — it doesn't need to be exact. After filling whatever matches on a page, the "
        "agent looks for a Submit/Next/Continue control and clicks it, repeating on the resulting page "
        "until nothing more matches or max pages is reached."
    )
    default_rows = pd.DataFrame([{"Field Name": "", "Value": ""}])
    form_values_df = st.data_editor(
        default_rows, num_rows="dynamic", use_container_width=True, key="explorer_form_values",
    )

if st.button("Run Exploration", type="primary") and base_url:
    form_values = {
        str(row["Field Name"]).strip(): str(row["Value"])
        for _, row in form_values_df.iterrows()
        if str(row["Field Name"]).strip()
    }
    with st.spinner("Crawling with a real headless browser..."):
        try:
            result = api_client.run_exploration(
                project_id, base_url, int(max_pages), int(max_depth),
                username=username or None, password=password or None,
                transaction_number=transaction_number or None,
                form_values=form_values or None, headless=headless,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Exploration failed: {exc}")
            st.stop()
    st.session_state["last_exploration"] = result

result = st.session_state.get("last_exploration")
if result:
    if result.get("notes"):
        for note in result["notes"]:
            st.info(note)

    st.subheader(f"Discovered Screens ({len(result['pages'])})")
    for page in result["pages"]:
        with st.container(border=True):
            st.markdown(f"**{page.get('screen_name', page['url'])}**")
            st.caption(page["url"])
            if page.get("fields"):
                for field in page["fields"]:
                    label = field.get("label") or field.get("name") or field.get("id") or "(unlabeled)"
                    st.write(f"- {field['tag']} `{field.get('type') or ''}` — {label}")
            elif page.get("error"):
                st.caption(f"Could not load: {page['error']}")

    if result.get("workflows"):
        st.subheader("Discovered Workflows")
        for workflow in result["workflows"]:
            with st.container(border=True):
                st.markdown(f"**{workflow['workflow_name']}**")
                for i, step in enumerate(workflow.get("steps", []), 1):
                    st.write(f"{i}. {step}")
