# Libram — Agent Guide

Financial price data aggregation service. Scrapes prices from multiple sources (PSE stocks, Philippine mutual funds, forex, and crypto) into PostgreSQL, exposes them through a REST API and MCP server, and maintains scheduler tasks for missing prices.

## Quick Reference

```bash
# Install dependencies (Python 3.14, managed by uv)
uv sync

# Run API + MCP server (dev mode, hot reload)
LIBRAM_DB="postgresql://user:***@localhost:5432/libram" \
  uv run fastapi dev server.py --app combined_app --port 6778

# Run the standalone task executor
LIBRAM_DB="postgresql://user:***@localhost:5432/libram" \
  uv run cli_scheduler.py

# Fetch a specific entity manually
LIBRAM_DB="..." uv run cli_fetch.py --entity_code RCR --start 2025-01-01T00:00:00 --end 2025-02-01T00:00:00

# Generate scheduler tasks manually
LIBRAM_DB="..." uv run cli_schedule.py [--entity_id UUID] [--min_date YYYY-MM-DDTHH:MM:SS]
```

The local development port is **6778**. The repository uses `uv`; do not install project dependencies with system `pip`.

## Architecture

```text
server.py
  FastAPI application + MCP bridge + scheduler lifecycle wiring
  includes route modules grouped by concern:
    routes/entities.py       entity discovery
    routes/prices.py         price retrieval and summary
    routes/indicators.py     SMA, EMA, and RSI
    routes/compare.py        multi-entity comparison
    routes/fundamentals.py   fundamentals upload/query
    routes/portfolios.py     portfolio, order, totals, and portfolio dividend fees
    routes/dividends.py      issuer dividend events

Route dependencies are defined in dependencies.py:
  Database
    └─ PriceManagerService
       ├─ FundamentalsManagerService
       ├─ PortfolioManagerService
       └─ PriceSchedulerService

Domain packages
  price_management/service.py       fetch/store/query orchestration
  fundamentals_management/service.py fundamentals business logic
  portfolio_management/service.py   coordinator for portfolio sub-services
  price_scheduler/service.py        missing-price task generation
  price_scheduler/executor.py       threaded task worker

Supporting packages
  price_analysis/                   pure calculations and comparison helpers
  price_sources/                    datasource plugin implementations
  libram_database/db.py             SQLAlchemy Core database layer
  libram_types/                    core dataclasses
```

`client.py` has been removed from the domain managers. The public manager classes are now named `PriceManagerService`, `FundamentalsManagerService`, `PortfolioManagerService`, and `PriceSchedulerService`, and imports should use their `service.py` modules.

## Server and lifecycle

`server.py` is intentionally small. It:

- creates the primary FastAPI app;
- imports and includes the routers from `routes/`;
- configures the FastMCP bridge at `/mcp`;
- builds `combined_app`, including both MCP and REST routes;
- configures CORS; and
- owns the APScheduler lifecycle.

The built-in APScheduler creates task-generation jobs for 08:00 and 20:00. It is started during the FastAPI lifespan startup and shut down during lifespan shutdown. The standalone `cli_scheduler.py` is a different component: it runs the worker executor that processes open tasks. Do not confuse task generation with task execution.

The dependency providers in `dependencies.py` load `LIBRAM_DB`, construct `Database`, and build the service dependency chain. Route handlers receive services through FastAPI `Depends`; they must not instantiate database or service objects directly.

## Database

PostgreSQL. Schema and seed data:

- `schema.sql` — tables and indexes for datasource, entity, price, task, fundamentals, portfolios, orders, dividends, and portfolio dividend fees.
- `data.sql` — seed datasource and entity rows.

DDL uses `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, so repeated setup is safe.

```bash
psql -U libram -d libram -f schema.sql
psql -U libram -d libram -f data.sql
```

`LIBRAM_DB` (or `.env`) contains the SQLAlchemy DSN, for example:
`postgresql://user:***@host:5432/libram`.

## REST API organization

All REST endpoints are under `/api/v1` and are also exposed as MCP tools through `/mcp`.

| Route module | Concern | Main endpoints |
|---|---|---|
| `routes/entities.py` | Entity discovery | `/entities` |
| `routes/prices.py` | Price records and summaries | `/prices`, `/prices/summary` |
| `routes/indicators.py` | Technical indicators | `/prices/sma`, `/prices/ema`, `/prices/rsi` |
| `routes/compare.py` | Cross-entity analysis | `/compare` |
| `routes/fundamentals.py` | Fundamental snapshots | `/fundamentals` |
| `routes/portfolios.py` | Portfolios, orders, totals, portfolio dividend fees | `/portfolios/...` |
| `routes/dividends.py` | Issuer dividend events | `/dividends` |

When adding an endpoint, put it in the route module matching its concern and keep the handler a thin HTTP adapter: parse/validate request data, call a service or pure domain function, translate expected domain errors to `HTTPException`, and return the result.

## Task system

The scheduler creates `task` rows for missing price ranges; the executor processes them:

1. `cli_schedule.py` or the server's APScheduler job scans entities and creates `OPEN` tasks.
2. `cli_scheduler.py` / `price_scheduler/executor.py` polls for `OPEN` tasks, locks them as `IN_PROGRESS`, fetches prices, then marks them `COMPLETED` or `FAILED`.
3. Failed tasks use exponential backoff (`retry_delay * 3^retry_count`).

`price_scheduler/service.py` contains task-generation logic. `price_scheduler/executor.py` contains worker execution logic. The server lifecycle only hooks in the task-generation scheduler; it does not replace the standalone executor.

## Datasource plugin pattern

Each datasource subclasses `BaseDatasource` and implements:
`fetch_prices(entity, start, end) -> Iterable[PriceRecord]`.

REST/JSON sources extend `RestJSONDatasource` and implement `build_request_params(...)` and `parse_price_data(...)`. Datasources are loaded dynamically from `datasource.implementation` (`module.path:ClassName`); entity configuration is merged over datasource configuration.

Current source modules include `rest_datasource.py`, `html_datasource.py`, `pse_edge_datasource.py`, `coindesk_ohlc_datasource.py`, `ofx_forex_datasource.py`, `bpi_fund_datasource.py`, `manulife_fund_datasource.py`, and `slamc_fund_datasource.py`.

## Environment variables

| Variable | Required | Default / purpose |
|---|---|---|
| `LIBRAM_DB` | Yes | PostgreSQL connection string |
| `LIBRAM_SCHEDULER_MAX_RETRIES` | No | `5` |
| `LIBRAM_SCHEDULER_RETRY_DELAY_SECONDS` | No | `300` |
| `LIBRAM_SCHEDULER_THREADS` | No | `8` |
| `LIBRAM_SCHEDULER_MAX_TASKS_PER_DATASOURCE` | No | `4` |
| `LIBRAM_SCHEDULER_POLL_INTERVAL_SECONDS` | No | `60` |
| `LIBRAM_SCHEDULER_POLL_JITTER_SECONDS` | No | `30` |

## Project conventions

- Python 3.14, managed by `uv` (`.python-version`).
- SQLAlchemy Core, not a declarative ORM; raw SQL uses `text()`.
- Core domain types are dataclasses in `libram_types/`. Pydantic models are used for HTTP request models, primarily in `fundamentals_management/models.py` and `portfolio_management/models.py`.
- Pure analysis functions live in `price_analysis/` and should not depend on the database or FastAPI.
- There is currently no tests directory.
- Docker production setup is in `Dockerfile` and `docker-compose.yml`; supervisor runs the server and standalone scheduler executor in one container.
- Ruff cleanup is part of the current codebase standard: preserve clean imports, formatting, and lint-compatible code.

## Architectural rules

### Keep HTTP routing thin

Route modules contain HTTP concerns only. Business logic, database access, calculations, datasource loading, and orchestration belong in domain services or pure modules. If a function can be tested without importing FastAPI, it usually does not belong in `routes/` or `server.py`.

### Use service dependency injection

Services are constructed by the provider chain in `dependencies.py`:

```python
async def get_price_manager_service(
    db: Database = Depends(get_database),
) -> PriceManagerService:
    return PriceManagerService(db)
```

A route consumes the provider rather than constructing a service:

```python
@router.get("/api/v1/prices")
async def list_prices(
    ...,
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    return price_manager.query_prices(...)
```

When a new domain service depends on another service, extend `dependencies.py` and chain providers there. Do not duplicate construction logic across route modules.

### Where code belongs

| Code type | Destination |
|---|---|
| Pure computation, no DB/I/O | `price_analysis/<module>.py` |
| DB/API business logic | Relevant domain `service.py` or focused domain module |
| Request models and domain exceptions | Relevant domain package (`models.py`, `__init__.py`, or focused module) |
| HTTP route handler | `routes/<concern>.py` |
| FastAPI/MCP/lifecycle assembly | `server.py` |
| Dependency providers | `dependencies.py` |
| Datasource plugin | `price_sources/<name>_datasource.py` |
| CLI entrypoint | `cli_*.py` |

### Prefer focused modules

For a feature spanning multiple concerns, split parsing, computation, and orchestration into focused functions/modules. Add public exports in the relevant package `__init__.py` where appropriate. Do not grow `server.py` into a monolith, and do not put business logic into a route handler merely because it is endpoint-specific.

## Key files

| File | Purpose |
|---|---|
| `server.py` | FastAPI/MCP assembly and APScheduler lifecycle |
| `dependencies.py` | FastAPI service/database dependency providers |
| `routes/*.py` | REST routes grouped by concern |
| `cli_scheduler.py` | Standalone threaded scheduler executor |
| `cli_schedule.py` | Manual task generation CLI |
| `cli_fetch.py` | Manual price-fetch CLI |
| `fundamentals_management/service.py` | Fundamentals business logic |
| `fundamentals_management/models.py` | Fundamentals request models |
| `portfolio_management/service.py` | Portfolio manager coordinator |
| `portfolio_management/models.py` | Portfolio request models |
| `portfolio_management/portfolio.py` | Portfolio CRUD |
| `portfolio_management/order.py` | Order CRUD and sell validation |
| `portfolio_management/totals.py` | Average-cost totals and per-entity breakdowns |
| `portfolio_management/dividend.py` | Issuer dividend operations |
| `portfolio_management/dividend_fees.py` | Portfolio/event dividend fee operations |
| `price_management/service.py` | Price fetch/store/query orchestration |
| `price_management/datasource.py` | Base datasource abstraction |
| `price_scheduler/service.py` | Missing-price task generation |
| `price_scheduler/executor.py` | Worker polling and task processing |
| `price_analysis/` | SMA, EMA, RSI, comparison, drawdown, and date utilities |
| `price_sources/` | Datasource implementations |
| `libram_database/db.py` | Database layer and SQLAlchemy helpers |
| `libram_types/` | Core dataclasses |
| `schema.sql` | Database DDL |
| `data.sql` | Seed data |
| `docs/server_refactor_plan.md` | Server refactor design notes |

## Anti-pattern

```python
# Bad: service construction and business logic inside a route
@router.get("/api/v1/compare")
async def compare(...):
    db = Database(os.environ["LIBRAM_DB"])
    manager = PriceManagerService(db)
    # fetch, parse, rank, and transform here
```

```python
# Good: provider-injected service and domain function
@router.get("/api/v1/compare")
async def compare(
    ...,
    price_manager: PriceManagerService = Depends(get_price_manager_service),
):
    return await build_comparison_payload(..., price_manager=price_manager)
```

When modifying this repository, update the relevant focused module and preserve the separation between route assembly, service orchestration, pure analysis, and scheduler execution.
