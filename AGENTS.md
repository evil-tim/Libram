# Libram — Agent Guide

Financial price data aggregation service. Scrapes prices from multiple sources (PSE stocks, Philippine mutual funds, forex, crypto) into PostgreSQL, exposes them via a REST API and MCP server.

## Quick Reference

```bash
# Install dependencies (requires Python 3.14, uv)
uv sync

# Run API + MCP server (dev mode, hot reload)
LIBRAM_DB="postgresql://user:pass@localhost:5432/libram" \
  uv run fastapi dev server.py --app combined_app --port 6778

# Run task scheduler (processes fetch tasks from DB)
LIBRAM_DB="postgresql://user:pass@localhost:5432/libram" \
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
│  MCP       /mcp                                      │
│  Built-in APScheduler: task generation at 08:00/20:00│
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

- `schema.sql` — tables: `datasource`, `entity`, `price`, `task`
- `data.sql` — seed rows: 6 datasources, ~30 entities (PSE stocks, funds, crypto, forex)

```bash
psql -U libram -d libram -f schema.sql
psql -U libram -d libram -f data.sql
```

The `LIBRAM_DB` env var (or `.env` file) holds the SQLAlchemy DSN:
`postgresql://user:pass@host:5432/libram`

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
- **Types**: Dataclasses in `libram_types/`, not Pydantic models
- **No tests directory** — contributions welcome
- **Docker**: `Dockerfile` + `docker-compose.yml` exist for production; supervisor runs both server and scheduler in one container

## Key Files

| File | Purpose |
|---|---|
| `server.py` | FastAPI app, MCP bridge, APScheduler task generation |
| `cli_scheduler.py` | Standalone scheduler executor (for separate container/process) |
| `cli_schedule.py` | CLI to manually generate tasks |
| `cli_fetch.py` | CLI to manually fetch prices for an entity |
| `price_management/client.py` | Core fetch/store/query logic |
| `price_management/datasource.py` | BaseDatasource ABC |
| `price_sources/rest_datasource.py` | Abstract REST/JSON datasource base |
| `price_scheduler/client.py` | Task generation logic (daily/weekly/monthly) |
| `price_scheduler/executor.py` | Threaded task executor |
| `libram_database/db.py` | SQLAlchemy database layer |
| `libram_types/libram_types.py` | EntityRecord, PriceRecord, TaskRecord |
| `schema.sql` | Database schema |
| `data.sql` | Seed data (datasources + entities) |
