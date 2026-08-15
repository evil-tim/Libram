# Server Refactor Plan

Goal: Break `server.py` into focused, testable modules to improve maintainability, testability, and clarity.

Overview
- Rationale: `server.py` currently combines app creation, dependency providers, route handlers, MCP bridge, and scheduler. Split responsibilities so each module has a single concern.
- Principles: small modules, explicit dependency injection via FastAPI `Depends`, minimal route handlers (thin wrappers), and clear import boundaries.

Proposed Modules
- `app_factory.py` — create FastAPI `app` and `combined_app`, lifespan wiring, middleware.
- `dependencies.py` — `get_db_string`, `get_database`, `get_price_manager_client`, `get_portfolio_manager_client`, `get_fundamentals_manager_client`, `get_scheduler_client`.
- `routes/entities.py` — entity-related routes (list entities).
- `routes/prices.py` — `/api/v1/prices` and `/api/v1/prices/summary`.
- `routes/indicators.py` — SMA/EMA/RSI endpoints.
- `routes/compare.py` — `/api/v1/compare` handler.
- `routes/fundamentals.py` — fundamentals upload/query endpoints.
- `routes/portfolios.py` — portfolio create/list/update/delete, orders, totals, and totals-by-entity.
- `routes/dividends.py` — dividend events and portfolio dividend fees CRUD.
- `mcp.py` — MCP instantiation and `mcp_app` export.
- `scheduler.py` — scheduler setup and job registration (`build_all_tasks` binding).

Migration steps
1. Create `app_factory.py` with the app creation and lifespan functions extracted from `server.py`.
2. Add `dependencies.py` and move all provider callables. Update imports in routes to reference providers from `dependencies`.
3. Create `routes/` package and one file per logical route group. Each file should expose a `router = APIRouter(prefix="/api/v1", tags=[...])` or equivalent and register endpoints.
4. Replace current route definitions in `server.py` with `include_router()` calls from the new route modules.
5. Move MCP creation into `mcp.py` and export `mcp_app`. Import `mcp_app` in `app_factory` to construct `combined_app`.
6. Move scheduler logic into `scheduler.py` and import/trigger from `app_factory` or `server.py` during startup.
7. Run linters and adjust relative imports.

Notes & Tips
- Keep route handlers as thin wrappers that call service/client methods (no heavy logic in routes).
- Use `APIRouter` in each `routes/*.py` for clearer composing and tag grouping.
- Preserve original operation_id and descriptions to keep API semantics and MCP mapping stable.
- Update `__init__.py` in `routes/` if you want convenient bulk includes.

Testing & Verification
- After each extraction step, run a quick import check (e.g., `python -m pip install -r requirements` then `python -c "from app_factory import combined_app"`).
- Run any existing tests or at least `flake8`/`ruff`/`pylint` to catch import errors.

Suggested order of implementation
1. `dependencies.py`
2. `app_factory.py` + MCP import
3. `routes/entities.py`, include router and verify imports
4. `routes/prices.py` and `routes/indicators.py`
5. `routes/compare.py`, `routes/fundamentals.py`
6. `routes/portfolios.py` and `routes/dividends.py`
7. `scheduler.py`
8. Finalize `server.py` as a thin orchestrator that imports `combined_app` and starts scheduler if needed.

Files to create/modify
- Add: `app_factory.py`, `dependencies.py`, `mcp.py`, `scheduler.py`, `routes/__init__.py`, `routes/entities.py`, `routes/prices.py`, `routes/indicators.py`, `routes/compare.py`, `routes/fundamentals.py`, `routes/portfolios.py`, `routes/dividends.py`.
- Modify: `server.py` to become a slim orchestrator that imports `combined_app` from `app_factory.py`.

Estimated effort
- Small incremental PRs: 6–10 changes; each extraction can be validated independently. Total time: 2–4 hours depending on test coverage and CI feedback.

Next steps
- If you approve, I'll start by extracting the dependency providers into `dependencies.py` and update `server.py` to import them.

---
Generated on: 2026-08-15
