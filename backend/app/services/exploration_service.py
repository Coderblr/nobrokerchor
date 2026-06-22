from sqlalchemy.orm import Session

from app.agents.base import AgentContext
from app.agents.orchestrator import PipelineFailedError, orchestrator
from app.llm.factory import get_llm_client


def run_exploration(
    db: Session, project_id: int, base_url: str, user_id: int,
    max_pages: int = 10, max_depth: int = 2,
    username: str | None = None, password: str | None = None,
    transaction_number: str | None = None, form_values: dict[str, str] | None = None,
    headless: bool = True,
) -> dict:
    context = AgentContext(
        db=db, project_id=project_id, generation_id=None, llm=get_llm_client(db), user_id=user_id,
        state={
            "base_url": base_url, "max_pages": max_pages, "max_depth": max_depth,
            "username": username, "password": password, "transaction_number": transaction_number,
            "form_values": form_values or {}, "headless": headless,
        },
    )
    try:
        orchestrator.run_pipeline("exploration_pipeline", context)
    except PipelineFailedError as exc:
        # The crawl itself (ApplicationExplorerAgent) is independently valuable even if the
        # LLM-dependent workflow inference step that runs after it fails (e.g. no API key
        # configured yet) - don't discard real crawl results just because that later step failed.
        if exc.agent_name != "WorkflowDiscoveryAgent" or "ApplicationExplorerAgent" not in context.state:
            raise ValueError(str(exc)) from exc

    return {
        "pages": context.state["ApplicationExplorerAgent"]["pages"],
        "notes": context.state["ApplicationExplorerAgent"].get("notes", []),
        "workflows": context.state.get("WorkflowDiscoveryAgent", {}).get("workflows", []),
    }
