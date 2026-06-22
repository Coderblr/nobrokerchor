import functools
import http.server
import threading

import pytest

from app.agents.application_explorer import ApplicationExplorerAgent
from app.agents.base import AgentContext
from app.agents.workflow_discovery import WorkflowDiscoveryAgent
from app.llm.base import BaseLLMClient
from app.models.misc import ApplicationInventory, WorkflowInventory
from app.models.project import Project
from app.models.user import User

STATIC_APP_DIR = __file__.replace("\\", "/").rsplit("/backend/", 1)[0] + "/storage/samples/static-app"
WORKFLOW_APP_DIR = __file__.replace("\\", "/").rsplit("/backend/", 1)[0] + "/storage/samples/static-app-workflow"


class FakeLLMClient(BaseLLMClient):
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        response = self._responses[self.calls]
        self.calls += 1
        return response


@pytest.fixture(scope="module")
def static_app_server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=STATIC_APP_DIR)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/index.html"
    server.shutdown()


@pytest.fixture(scope="module")
def workflow_app_server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WORKFLOW_APP_DIR)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/login.html"
    server.shutdown()


def test_application_explorer_crawls_real_static_site_with_real_browser(db_session, static_app_server):
    """Real headless Chrome, real navigation, real DOM field extraction against a tiny local
    2-page static site (login -> deposit) - there is no live NBC application available in this
    environment, so this is the closest honest substitute: genuine browser automation mechanics,
    just not a live banking app."""

    user = db_session.query(User).filter(User.username == "testadmin").first()
    project = Project(name="Explorer Pilot", description="explorer test", created_by=user.id)
    db_session.add(project)
    db_session.commit()

    context = AgentContext(
        db=db_session, project_id=project.id, generation_id=None, llm=None, user_id=user.id,
        state={"base_url": static_app_server, "max_pages": 5, "max_depth": 2},
    )

    result = ApplicationExplorerAgent().run(context)
    assert result.success, result.error
    assert len(result.output["pages"]) == 2

    screen_names = {p["screen_name"] for p in result.output["pages"]}
    assert screen_names == {"NBC Demo - Login", "NBC Demo - Cash Deposit"}

    deposit_page = next(p for p in result.output["pages"] if p["screen_name"] == "NBC Demo - Cash Deposit")
    field_labels = {f["label"] for f in deposit_page["fields"] if f["label"]}
    assert "Account Number" in field_labels
    assert "Deposit Amount" in field_labels

    inventory_rows = db_session.query(ApplicationInventory).filter(ApplicationInventory.project_id == project.id).all()
    assert len(inventory_rows) == 2

    context.state["ApplicationExplorerAgent"] = result.output
    context.llm = FakeLLMClient([
        {
            "workflows": [
                {"workflow_name": "Login then Cash Deposit", "steps": ["Log in as teller", "Navigate to Cash Deposit", "Submit deposit"]}
            ]
        }
    ])

    workflow_result = WorkflowDiscoveryAgent().run(context)
    assert workflow_result.success, workflow_result.error
    assert len(workflow_result.output["workflows"]) == 1

    workflow_rows = db_session.query(WorkflowInventory).filter(WorkflowInventory.project_id == project.id).all()
    assert len(workflow_rows) == 1
    assert workflow_rows[0].workflow_name == "Login then Cash Deposit"


def test_exploration_service_keeps_crawl_results_when_workflow_discovery_fails(db_session, static_app_server, monkeypatch):
    """If WorkflowDiscoveryAgent fails (e.g. no DEEPSEEK_API_KEY configured), the real crawl
    results from ApplicationExplorerAgent must still be returned, not discarded."""
    from app.llm.base import LLMError
    from app.models.project import Project
    from app.models.user import User
    from app.services import exploration_service

    user = db_session.query(User).filter(User.username == "testadmin").first()
    project = Project(name="Explorer Resilience Pilot", description="test", created_by=user.id)
    db_session.add(project)
    db_session.commit()

    class FailingLLMClient(FakeLLMClient):
        def complete_json(self, system_prompt, user_prompt):
            raise LLMError("DEEPSEEK_API_KEY is not configured.")

    monkeypatch.setattr(exploration_service, "get_llm_client", lambda db=None: FailingLLMClient([]))

    result = exploration_service.run_exploration(db_session, project.id, static_app_server, user.id)

    assert len(result["pages"]) == 2
    assert result["workflows"] == []


def test_application_explorer_logs_in_searches_and_walks_a_form_workflow(db_session, workflow_app_server):
    """Exercises the full interactive flow against a 4-page static fixture
    (login -> dashboard -> deposit-form -> confirmation): fill username/password and submit,
    fill a transaction-number search field and submit, then fill the deposit form's fields and
    submit to reach the confirmation page - all via heuristic field/button matching, no fixed
    selectors. This proves the interaction mechanics work; it is not a live banking app."""

    user = db_session.query(User).filter(User.username == "testadmin").first()
    project = Project(name="Explorer Workflow Pilot", description="workflow test", created_by=user.id)
    db_session.add(project)
    db_session.commit()

    context = AgentContext(
        db=db_session, project_id=project.id, generation_id=None, llm=None, user_id=user.id,
        state={
            "base_url": workflow_app_server, "max_pages": 10, "max_depth": 2,
            "username": "teller1", "password": "Secret123",
            "transaction_number": "TXN-001",
            "form_values": {"Account Number": "1234567890", "Deposit Amount": "5000"},
        },
    )

    result = ApplicationExplorerAgent().run(context)
    assert result.success, result.error

    screen_names = [p["screen_name"] for p in result.output["pages"]]
    assert "NBC Demo Workflow - Login" in screen_names
    assert "NBC Demo Workflow - Dashboard" in screen_names
    assert "NBC Demo Workflow - Cash Deposit" in screen_names
    assert "NBC Demo Workflow - Confirmation" in screen_names

    notes_text = " ".join(result.output["notes"])
    assert "Login form submitted" in notes_text
    assert "Transaction search submitted for 'TXN-001'" in notes_text

    deposit_page = next(p for p in result.output["pages"] if p["screen_name"] == "NBC Demo Workflow - Cash Deposit")
    assert any(f["label"] == "Account Number" for f in deposit_page["fields"])
