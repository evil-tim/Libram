# Libram — Agent Guide

Financial price data aggregation service. Scrapes prices from multiple sources (PSE stocks, Philippine mutual funds, forex, crypto) into PostgreSQL, exposes them via a REST API and MCP server.

## Quick Reference

```bash
# Install dependencies (requires Python 3.14, uv)
uv sync

# Run API + MCP server (dev mode, hot reload)
LIBRAM_DB="postgresql://user:***@localhost:5432/libram" \
  uv run fastapi dev server.py --app combined_app --port 6778

# Run task scheduler (processes fetch tasks from DB)
LIBRAM_DB="postgresql://user:***@localhost:5432/libram" \
  uv run cli_scheduler.py

# Fetch a specific entity manually
LIBRAM_DB="..." uv run cli_fetch.py --entity_code RCR --start 2025-01-01T00:00:00 --end 2025-02-01T00:00:00

# Generate scheduler tasks manually
LIBRAM_DB="..." uv run cli_schedule.py [--entity_id UUID] [--min_date YYYY-MM-DDTHH:MM:SS]
```

## Architecture

Three layers, each independently runnable:

```
┌─────────────────────────────────────────────────────┐
│  server.py (FastAPI + MCP)                          │
│  REST API  /api/v1/entities  /api/v1/prices         │
│            /api/v1/prices/sma  /api/v1/prices/ema    │
│            /api/v1/prices/rsi  /api/v1/prices/summary│
│            /api/v1/compare  /api/v1/fundamentals      │
│  MCP       /mcp                                      │
│  Built-in APScheduler: task generation at 08:00/20:00│
├─────────────────────────────────────────────────────┤
│  fundamentals_management/                            │
│    client.py — fundamentals upload/query business    │
├─────────────────────────────────────────────────────┤
│  price_analysis/                                     │
│    moving_averages.py — SMA and EMA computations      │
│    rsi.py             — RSI (Wilder's smoothing)     │
│    comparison.py      — multi-entity comparison       │
│    max_drawdown.py    — max drawdown calculation      │
│    date_utils.py      — timezone-aware parsing        │
├─────────────────────────────────────────────────────┤
│  price_scheduler/                                    │
│    client.py   — generates tasks for missing prices  │
│    executor.py — threaded worker pool, polls for tasks│
├─────────────────────────────────────────────────────┤
│  price_management/                                   │
│    client.py     — fetch/store/query orchestrator     │
│    datasource.py — BaseDatasource ABC                 │
├─────────────────────────────────────────────────────┤
│  price_sources/  (plugin implementations)            │
│    rest_datasource.py — abstract REST/JSON base      │
│    html_datasource.py — abstract HTML base           │
│    pse_edge_datasource.py   — PSE Edge OHLC          │
│    coindesk_ohlc_datasource.py — CoinDesk OHLC      │
│    ofx_forex_datasource.py — OFX forex time series   │
│    bpi_fund_datasource.py                            │
│    manulife_fund_datasource.py                       │
│    slamc_fund_datasource.py                          │
├─────────────────────────────────────────────────────┤
│  libram_database/db.py  — SQLAlchemy CRUD layer       │
│  libram_types/          — dataclasses: EntityRecord,  │
│                           PriceRecord, TaskRecord     │
└─────────────────────────────────────────────────────┘
```

## Database

PostgreSQL. Schema and seed data:

- `schema.sql` — tables: `datasource`, `entity`, `price`, `task`, `entity_fundamentals` (plus indexes)
- `data.sql` — seed rows: 6 datasources, ~30 entities (PSE stocks, funds, crypto, forex)

The database schema is found in `schema.sql`, applied manually to the target database for now. All DDL uses `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`, so repeated calls are safe and new tables/indexes are created automatically.

```bash
# Manual schema setup (if needed)
psql -U libram -d libram -f schema.sql
psql -U libram -d libram -f data.sql
```

The `LIBRAM_DB` env var (or `.env` file) holds the SQLAlchemy DSN:
`postgresql://user:***@host:5432/libram`

## REST API Endpoints

| Method | Path | Operation ID | Description |
|---|---|---|---|
| GET | `/api/v1/entities` | `list_available_entities` | List/filter entities |
| GET | `/api/v1/prices` | `list_prices_for_entity` | Paginated price records |
| GET | `/api/v1/prices/summary` | `list_price_summary` | Aggregate stats (count, min, max, avg, std_dev, return) |
| GET | `/api/v1/prices/sma` | `get_simple_moving_average` | SMA over close/price series |
| GET | `/api/v1/prices/ema` | `get_exponential_moving_average` | EMA over close/price series |
| GET | `/api/v1/prices/rsi` | `get_rsi` | RSI (Wilder's smoothing) |
| GET | `/api/v1/compare` | `compare_entities` | Multi-entity comparison with indicators |
| POST | `/api/v1/fundamentals` | `update_entity_fundamentals` | Upload fundamentals snapshot. Allowed metric keys: `market_cap`, `pe_ratio`, `pb_ratio`, `eps`, `shares_outstanding`, `dividend_yield`, `net_income_ttm`. |
| GET | `/api/v1/fundamentals` | `get_entity_fundamentals` | Query fundamentals (latest or all) |

All endpoints are also exposed as MCP tools via FastMCP at `/mcp`.

## Datasource Plugin Pattern

Each datasource subclasses `BaseDatasource` and implements `fetch_prices(entity, start, end) -> Iterable[PriceRecord]`.

For REST/JSON sources, extend `RestJSONDatasource` instead and implement:
- `build_request_params(entity, start, end, config)` — return `(url, query_params, body)`
- `parse_price_data(data)` — return `Iterable[PriceRecord]`

Datasources are loaded dynamically from the `datasource.implementation` column (format: `module.path:ClassName`). Entity-level config is merged on top of datasource-level config.

## Task System

The scheduler creates `task` rows for date ranges with missing prices. The executor picks them up:

1. **cli_schedule.py** / **server.py** APScheduler — scans entities, creates `OPEN` tasks
2. **cli_scheduler.py** / **executor.py** — worker threads poll for `OPEN` tasks, lock them (`IN_PROGRESS`), fetch prices, mark `COMPLETED` or `FAILED`
3. Exponential backoff on failure: `retry_delay * 3^retry_count`

Task granularity: daily (last week), weekly (last month), monthly (historical backfill).

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LIBRAM_DB` | Yes | PostgreSQL connection string |
| `LIBRAM_SCHEDULER_MAX_RETRIES` | No | Max task retries (default: 5) |
| `LIBRAM_SCHEDULER_RETRY_DELAY_SECONDS` | No | Base retry delay (default: 300) |
| `LIBRAM_SCHEDULER_THREADS` | No | Worker thread count (default: 8) |
| `LIBRAM_SCHEDULER_MAX_TASKS_PER_DATASOURCE` | No | Rate limit per source (default: 4) |
| `LIBRAM_SCHEDULER_POLL_INTERVAL_SECONDS` | No | Poll interval (default: 60) |
| `LIBRAM_SCHEDULER_POLL_JITTER_SECONDS` | No | Random jitter (default: 30) |

## Project Conventions

- **Python**: 3.14, managed by `uv` (see `.python-version`)
- **Port**: 6778 for local dev
- **ORM**: SQLAlchemy Core (no declarative models), raw SQL via `text()`
- **Types**: Dataclasses in `libram_types/`, not Pydantic models (except request models in `server.py`)
- **Analysis**: Pure functions in `price_analysis/` — no DB dependency, operate on `(timestamp, value)` lists
- **No tests directory** — contributions welcome
- **Docker**: `Dockerfile` + `docker-compose.yml` exist for production; supervisor runs both server and scheduler in one container

## Architectural Rules

### server.py is a thin routing layer

server.py contains ONLY:
- FastAPI app setup, dependency injection (`Depends`), lifespan management
- Route handlers that validate input, delegate to domain modules, and return results
- MCP bridge setup (`FastMCP.from_fastapi`)
- APScheduler wiring (task generation cron jobs)

server.py must NOT contain:
- Business logic, computation, or data transformation
- Helper functions that don't directly depend on FastAPI request/response objects
- Imports of `statistics`, `math`, `re`, `asyncio` (for non-route concerns)

**Litmus test:** if a function can be unit-tested without importing FastAPI, it doesn't belong in server.py.

### Services are injected via FastAPI Depends

Domain clients (`PriceManagerClient`, `FundamentalsManagerClient`, `PriceSchedulerClient`, `Database`) are **never instantiated directly** in route handlers. They are created through a `Depends` chain defined in server.py:

```python
# Provider functions (defined once in server.py)
async def get_price_manager_client(
    db: Database = Depends(get_database),
) -> PriceManagerClient:
    return PriceManagerClient(db)

# Route handlers receive clients via Depends — never construct them manually
@app.get("/api/v1/prices")
async def list_prices(
    ...,
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
):
    ...
```

When adding a new domain package, add a corresponding `get_*_client` provider in server.py and inject it via `Depends` in every route that needs it. If the new client depends on an existing one (e.g. needs `PriceManagerClient`), chain them:

```python
async def get_new_client(
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
    db: Database = Depends(get_database),
) -> NewClient:
    return NewClient(price_manager, db)
```

### Where code belongs

| Code type | Destination | Example |
|---|---|---|
| Pure computation (no DB, no I/O) | `price_analysis/<module>.py` | SMA, RSI, max drawdown, ranking |
| Business logic that talks to DB/API | Domain package `client.py` | `price_management/client.py`, `fundamentals_management/client.py` |
| HTTP route handler (thin wrapper) | `server.py` | Parse query params, call domain module, return dict |
| Datasource plugin | `price_sources/<name>_datasource.py` | PSE Edge, CoinGecko, BPI fund |
| CLI entrypoint | `cli_*.py` | `cli_fetch.py`, `cli_schedule.py` |

### New modules over monoliths

When adding a new feature that introduces significant logic:
- Create a new module in the appropriate package (e.g. `price_analysis/comparison.py`)
- Export public functions from the package `__init__.py`
- Keep the route handler in server.py under ~25 lines
- If the feature spans multiple concerns (parsing + computation + ranking), split into separate functions within the module — don't create one giant function

### Anti-pattern: "route handler as implementation"

```
# BAD — computation lives in server.py, service instantiated inline
@app.get("/api/v1/compare")
async def compare_entities(...):
    pm = PriceManagerClient(db)   # don't do this
    # 200 lines of parsing, fetching, computing, ranking
    ...

# GOOD — computation lives in domain module, service injected via Depends
from price_analysis.comparison import build_comparison_payload

@app.get("/api/v1/compare")
async def compare_entities(
    ...,
    price_manager: PriceManagerClient = Depends(get_price_manager_client),
):
    return build_comparison_payload(entity_codes, start, end, indicators, price_manager)
```

## Key Files

| File | Purpose |
|---|---|
| `server.py` | FastAPI app, MCP bridge, scheduled task generation, request routing |
| `cli_scheduler.py` | Standalone scheduler executor (worker process for tasks) |
| `cli_schedule.py` | CLI to manually generate scheduler tasks |
| `cli_fetch.py` | CLI to manually fetch prices for a specific entity/date range |
| `main.py` | Minimal entrypoint / project placeholder |
| `fundamentals_management/client.py` | Fundamentals upload/query business logic |
| `price_management/client.py` | Core fetch/store/query orchestrator for entities/prices |
| `price_management/datasource.py` | BaseDatasource ABC for plugin datasources |
| `price_sources/rest_datasource.py` | Abstract REST/JSON datasource base |
| `price_sources/pse_edge_datasource.py` | PSE Edge OHLC datasource implementation |
| `price_sources/coingecko_ohlc_datasource.py` | CoinGecko OHLC datasource implementation |
| `price_sources/coindesk_ohlc_datasource.py` | CoinDesk OHLC datasource implementation |
| `price_sources/ofx_forex_datasource.py` | OFX forex time series datasource |
| `price_sources/bpi_fund_datasource.py` | BPI fund datasource implementation |
| `price_sources/manulife_fund_datasource.py` | Manulife fund datasource implementation |
| `price_sources/slamc_fund_datasource.py` | SLAMC fund datasource implementation |
| `price_sources/html_datasource.py` | HTML scraper datasource implementation |
| `price_scheduler/client.py` | Task generation logic for missing price ranges |
| `price_scheduler/executor.py` | Worker executor polling and task processing |
| `price_analysis/moving_averages.py` | SMA and EMA calculation utilities |
| `price_analysis/rsi.py` | RSI calculation utilities |
| `price_analysis/comparison.py` | Multi-entity comparison and indicator payload builder |
| `price_analysis/max_drawdown.py` | Max drawdown calculation utilities |
| `price_analysis/date_utils.py` | Timezone-aware date parsing and conversion helpers |
| `libram_database/db.py` | Database layer and SQLAlchemy helpers |
| `libram_types/libram_types.py` | Core dataclasses for entities, prices, tasks |
| `schema.sql` | Database schema DDL for all tables and indexes |
| `data.sql` | Seed data for datasources and entities |
