from pydantic import BaseModel


class ExplorationRunRequest(BaseModel):
    project_id: int
    base_url: str
    max_pages: int = 10
    max_depth: int = 2
    username: str | None = None
    password: str | None = None
    transaction_number: str | None = None
    form_values: dict[str, str] | None = None
    headless: bool = True


class ExplorationRunResponse(BaseModel):
    pages: list[dict]
    notes: list[str]
    workflows: list[dict]


class CoverageRunRequest(BaseModel):
    project_id: int


class BusinessRuleRunRequest(BaseModel):
    generation_id: int
