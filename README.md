# NBC Agentic Test Automation Platform

AI-powered test automation platform for NBC (New Branch Channel) and similar banking applications.
All 5 build phases are implemented. See `C:\Users\PUSHKAR\.claude\plans\peppy-waddling-garden.md` for the
phase-by-phase build history and design rationale.

## What's implemented

**Mode 1 — Generate New Framework**: Login → Create Project → Upload Requirement Doc → AI Pipeline
(Requirement Analyzer → Test Planner → Test Data Agent → Feature Generator → Page Object Generator → Step
Definition Generator → Framework Builder) → download a compilable **Selenium/Java/Cucumber/TestNG/Allure**
project, or a **Playwright/TypeScript/Cucumber.js** project.

**Mode 2 — Use Existing Framework**: ingest an existing automation framework (ZIP upload / Git URL / local
folder) → Framework Analyzer (static detection of framework type, base classes, existing page
objects/steps/features) → the same generation agents, reusing the detected package structure → Code Merge
Agent (new/duplicate/conflict detection) → Framework Upgrade Agent (overlays only new/non-conflicting files
onto a copy of the original, never overwriting existing code) → download the upgraded project.

**Execution & Reporting**: Execution Agent actually runs `mvn test` or `npm install && npx cucumber-js`
against an assembled framework on this machine, parses the real Cucumber JSON report, and the Reporting
Agent generates a real Allure HTML report (Selenium) or serves the Cucumber HTML report (Playwright).
Failure Analysis Agent classifies failures via LLM; Self-Healing Agent proposes alternative locators for
locator-not-found failures (never auto-applied).

**Application Intelligence**: Application Explorer Agent crawls a real web app with a real headless browser
and records each screen's form fields. It can also log in (finds the password field + nearest username field
by heuristics, fills both, submits), search for a transaction by number (finds a search/transaction field,
fills it, submits), and walk forward through a multi-step form workflow (fill provided field values, find
and click a Submit/Next/Continue control, record the resulting screen, repeat) — all heuristic field/button
matching, not fixed selectors. Workflow Discovery Agent infers multi-step workflows from the crawl; Coverage
Agent cross-references business rules against generated test scenarios; Business Rule Agent formalizes raw
rules into categorized, testable assertions.

**Platform**: Version Management (compare/restore/reuse-as-Mode-2-base), Knowledge Base browser (raw
ChromaDB collection viewer), Admin Panel (user/role CRUD, Admin-only).

## Stack

- Backend: FastAPI + SQLAlchemy + SQLite, `backend/`
- Frontend: Streamlit, `frontend/`
- Vector DB: ChromaDB (local persistent client), `storage/chroma/`
- LLM: Azure OpenAI (default) or DeepSeek, selected via `LLM_PROVIDER` — see `app/llm/factory.py`
- Selenium track tooling: JDK 17 + Maven 3.9 (no admin rights on this machine, so installed as plain zip
  extracts under `tools/` rather than system-wide — see `tools/env.sh`)
- Playwright track tooling: Node.js (already present on this machine)

## First-time setup

```bash
python -m venv .venv
.venv/Scripts/activate        # or .venv\Scripts\Activate.ps1 on PowerShell
pip install -r backend/requirements.txt
pip install -r frontend/requirements.txt

cp backend/.env.example backend/.env
# then edit backend/.env and set AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_DEPLOYMENT
# (from your Azure AI Foundry / Azure OpenAI Studio resource), or set LLM_PROVIDER=deepseek and
# DEEPSEEK_API_KEY instead (get one from https://platform.deepseek.com)

python scripts/seed_admin.py                    # creates admin / Admin@123
python scripts/generate_sample_requirement.py   # optional: sample Cash Deposit requirement doc
```

For the Selenium track's `mvn test`/Allure report generation to work, either install JDK 17+ and Maven 3.9+
system-wide, or extract them under `tools/jdk-*` and `tools/apache-maven-*` (matching what's already there)
— `app/services/execution_env.py` auto-detects them from that folder with no configuration needed.

## Running

```bash
# Terminal 1
cd backend && uvicorn app.main:app --reload --port 8000

# Terminal 2
cd frontend && streamlit run app.py
```

Open http://localhost:8501, log in with `admin` / `Admin@123` (change the password after first login via the
Settings page's self-service password change, or the Admin Panel for other accounts).

## Known limitations in this environment

- No live NBC (or any) banking application is available here. Execution Agent, Self-Healing, Application
  Explorer, and Workflow Discovery are real, working implementations, verified against either a real `mvn
  test`/`cucumber-js` run (Execution/Reporting) or small local static HTML fixtures
  (`storage/samples/static-app/`, `storage/samples/static-app-workflow/` for login/search/form-walk) — not
  against a live banking app. The login/search/form-fill heuristics (label/name/placeholder matching) may
  need adjustment against a real app's actual markup.
- ChromaDB uses a custom offline embedding function (`app/vectorstore/embedding_function.py`) instead of its
  default, which downloads an ONNX model from the internet on first use and fails with
  `[Errno 11001] getaddrinfo failed` on networks that block that egress (e.g. a locked-down corporate
  desktop). This app never runs semantic similarity search, only metadata filtering, so the embedding values
  themselves don't need to be meaningful - only deterministic and network-free. `add_document` writes are
  also non-fatal now (logged, not raised) so a knowledge-base write hiccup never aborts an agent pipeline.
- Real LLM calls require valid credentials in `backend/.env` for whichever provider is active
  (`AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_DEPLOYMENT` for the default Azure OpenAI
  provider, or `DEEPSEEK_API_KEY` if `LLM_PROVIDER=deepseek`). All AI agents are verified with a mocked LLM
  client in the test suite; the live UI will show a clear "not configured" error from any agent that needs
  the LLM until credentials are set, and the Settings page shows which provider is active and what's missing.
- JDK/Maven were installed without admin rights (zip extracts under `tools/`), not via a system package
  manager — see `tools/env.sh` and `app/services/execution_env.py`.

## Tests

```bash
cd backend && pytest tests/ -v
```

17 tests, including one that actually shells out to `mvn test` and launches a real ChromeDriver session
(~45s) and three that crawl real local static sites with a real headless browser, including one exercising
the full login → transaction search → form-fill → forward-navigation flow — the rest are fast mocked-LLM
unit/integration tests. All pass.
