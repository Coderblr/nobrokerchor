from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.intelligence import BusinessRuleRunRequest, CoverageRunRequest, ExplorationRunRequest
from app.services.business_rule_service import run_business_rule_analysis
from app.services.coverage_service import run_coverage
from app.services.exploration_service import run_exploration

router = APIRouter(tags=["intelligence"])


@router.post("/exploration/run")
def run_exploration_route(
    payload: ExplorationRunRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        return run_exploration(
            db, payload.project_id, payload.base_url, current_user.id, payload.max_pages, payload.max_depth,
            username=payload.username, password=payload.password, transaction_number=payload.transaction_number,
            form_values=payload.form_values, headless=payload.headless,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/coverage/run")
def run_coverage_route(
    payload: CoverageRunRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        return run_coverage(db, payload.project_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/business-rules/run")
def run_business_rules_route(
    payload: BusinessRuleRunRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    try:
        return run_business_rule_analysis(db, payload.generation_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
