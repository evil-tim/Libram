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
│            /api/v1/prices/sma  /prices/ema  /prices/rsi│
│            /api/v1/prices/summary  /api/v1/compare  │
│            /api/v1/fundamentals (GET + POST)        │
│  MCP       /mcp                                      │
│  Built-in APScheduler: task generation at 08:00/20:00│
├─────────────────────────────────────────────────────┤
│  price_analysis/                                     │
│    moving_averages.py — SMA and EMA computations    │
│    rsi.py             — RSI (Wilder's smoothing)    │
│    comparison.py      — multi-entity comparison     │
│    max_drawdown.py    — max drawdown calculation    │
│    date_utils.py      — timezone-aware parsing      │
├─────────────────────────────────────────────────────┤
│  price_scheduler/                                    │
│    client.py   — generates tasks for missing prices  │
│    executor.py — threaded worker pool, polls for tasks│
├─────────────────────────────────────────────────────┤
│  price_management/                                   │
│    client.py     — fetch/store/query orchestrator    │
│    datasource.py — BaseDatasource ABC                │
├─────────────────────────────────────────────────────┤
│  price_sources/  (plugin implementations)            │
│    rest_datasource.py — abstract REST/JSON base      │
│    pse_edge_datasource.py   — PSE Edge OHLC          │
│    manulife_fund_datasource.py                       │
│    bpi_fund_datasource.py                            │
│    slamc_fund_datasource.py                          │
│    ofx_forex_datasource.py                           │
│    coindesk_ohlc_datasource.py                       │
│    html_datasource.py                                │
├─────────────────────────────────────────────────────┤
│  libram_database/db.py  — SQLAlchemy CRUD layer      │
│  libram_types/          — dataclasses: EntityRecord, │
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

### entity_fundamentals

Stores structured fundamental metrics (P/E, market cap, EPS, etc.) as timestamped snapshots. Each upload creates a new row, preserving history. Schema:

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | Generated as `fnd_<8hex>` |
| `entity_id` | UUID FK | References `entity(id)` |
| `metrics` | JSONB | Keys from the allowed set (see below) |
| `source_name` | TEXT | Provenance |
| `source_url` | TEXT | Optional |
| `as_of_date` | DATE | Defaults to today |
| `confidence` | TEXT | `high`, `medium`, or `low` |
| `notes` | TEXT | Optional |
| `uploaded_at` | TIMESTAMPTZ | Auto-set |
| `uploaded_by` | TEXT | Defaults to `agent` |

Allowed metric keys: `market_cap`, `pe_ratio`, `pb_ratio`, `eps`, `shares_outstanding`, `dividend_yield`, `net_income_ttm`.

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
| POST | `/api/v1/fundamentals` | `update_entity_fundamentals` | Upload fundamentals snapshot |
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

## Key Files

| File | Purpose |
|---|---|
| `server.py` | FastAPI app, MCP bridge, APScheduler, lifespan startup/shutdown |
| `cli_scheduler.py` | Standalone scheduler executor (for separate container/process) |
| `cli_schedule.py` | CLI to manually generate tasks |
| `cli_fetch.py` | CLI to manually fetch prices for an entity |
| `price_management/client.py` | Core fetch/store/query logic, fundamentals upload/query |
| `price_management/datasource.py` | BaseDatasource ABC |
| `price_sources/rest_datasource.py` | Abstract REST/JSON datasource base |
| `price_scheduler/client.py` | Task generation logic (daily/weekly/monthly) |
| `price_scheduler/executor.py` | Threaded task executor |
| `price_analysis/moving_averages.py` | SMA and EMA computation |
| `price_analysis/rsi.py` | RSI computation (Wilder's smoothing) |
| `price_analysis/comparison.py` | Multi-entity comparison with indicators |
| `price_analysis/max_drawdown.py` | Max drawdown calculation |
| `price_analysis/date_utils.py` | Timezone-aware datetime parsing |
| `libram_database/db.py` | SQLAlchemy database layer, `init_db()` for schema bootstrap |
| `libram_types/libram_types.py` | EntityRecord, PriceRecord, TaskRecord |
| `schema.sql` | Database schema (all tables + indexes, idempotent) |
| `data.sql` | Seed data (datasources + entities) |
