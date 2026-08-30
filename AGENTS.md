# Libram — Agent Guide

Financial price data aggregation service. Scrapes prices from multiple sources (PSE stocks, Philippine mutual funds, forex, and crypto) into PostgreSQL, exposes them through a REST API and MCP server, and maintains scheduler tasks for missing prices.

## Quick Reference

```bash
# Install dependencies (Python 3.14, managed by uv)
uv sync

# Run API + MCP server (dev mode, hot reload)
LIBRAM_DB="postgresql://user:***@localhost:5432/libram" \
  uv run fastapi dev server.py --app combined_app --port 6778

# Run the standalone historical task executor
LIBRAM_DB="postgresql://user:***@localhost:5432/libram" \
  uv run cli_scheduler.py

# Run the standalone recurring snapshot executor
LIBRAM_DB="postgresql://user:***@localhost:5432/libram" \
  uv run cli_snapshot_scheduler.py

# Fetch a specific entity manually
LIBRAM_DB="..." uv run cli_fetch.py --entity_code RCR --start 2025-01-01T00:00:00 --end 2025-02-01T00:00:00

# Fetch a current snapshot observation (no date range needed); --dry_run skips writes
LIBRAM_DB="..." uv run cli_fetch.py --entity_code WBTC --snapshot [--dry_run]

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
  price_scheduler/executor.py        threaded historical task worker
  snapshot_scheduler/executor.py    recurring snapshot worker

Supporting packages
  price_analysis/                   pure calculations and comparison helpers
  price_sources/                    datasource plugin implementations
    web3/                           Web3 clients, ERC20 metadata, ABIs, and quotes
  libram_database/db.py             SQLAlchemy Core database layer
  libram_types/                    core dataclasses
```

## Server and lifecycle

`server.py` is intentionally small. It:

- creates the primary FastAPI app;
- imports and includes the routers from `routes/`;
- configures the FastMCP bridge at `/mcp`;
- builds `combined_app`, including both MCP and REST routes;
- configures CORS; and
- owns the APScheduler lifecycle.

The built-in APScheduler creates historical task-generation jobs for 08:00 and 20:00. It is started during the FastAPI lifespan startup and shut down during lifespan shutdown. The standalone `cli_scheduler.py` is a different component: it runs the historical worker executor that processes open tasks. The standalone `cli_snapshot_scheduler.py` is a third, independent process: it claims due rows in `snapshot_state` and performs recurring `fetch_price()` observations. Do not confuse historical task generation, historical task execution, and snapshot execution.

Snapshot schedules are explicitly enabled by rows in `snapshot_state`; `CONTINUOUS` frequency alone does not activate an entity. The sample `data.sql` seed enables WBTC at a 15-minute interval. Snapshot state uses durable leases: a worker claims a row before performing the RPC call, and completion/failure updates are accepted only for the current lease token. If a worker dies or exceeds its lease, another worker can recover the row; this provides at-least-once observations, so a repeated quote is possible.

The dependency providers in `dependencies.py` load `LIBRAM_DB`, construct `Database`, and build the service dependency chain. Route handlers receive services through FastAPI `Depends`; they must not instantiate database or service objects directly.

## Database

PostgreSQL. Schema and seed data:

- `schema.sql` — tables and indexes for datasource, entity, price, task, fundamentals, portfolios, orders, dividends, portfolio dividend fees, and snapshot state.
- `data.sql` — seed datasource, entity and scheduling state rows.

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

## Price fetching scheduling system

### Historical price fetching task system

The scheduler creates `task` rows for missing price ranges; the executor processes them:

1. `cli_schedule.py` or the server's APScheduler job scans entities that have the `DAILY` frequency and creates `OPEN` tasks.
2. `cli_scheduler.py` / `price_scheduler/executor.py` polls for `OPEN` tasks, locks them as `IN_PROGRESS`, fetches prices, then marks them `COMPLETED` or `FAILED`.
3. Failed tasks use exponential backoff (`retry_delay * 3^retry_count`).

`price_scheduler/service.py` contains task-generation logic. `price_scheduler/executor.py` contains worker execution logic. The server lifecycle only hooks in the task-generation scheduler; it does not replace the standalone executor.

### Price snapshot fetching system

Snapshot execution is separate from the historical task system. `snapshot_scheduler/executor.py` polls durable `snapshot_state` leases, calls `PriceManagerService.fetch_snapshot_and_store()`, and reschedules with fixed delay or bounded retry backoff. In the container, supervisord runs `server`, `scheduler`, and `snapshot_scheduler` as independent programs. Snapshot shutdown allows up to 360 seconds for in-flight RPC work; historical workers have a 30-second bounded stop wait.

## Datasource plugin pattern

Each datasource subclasses `BaseDatasource` and implements one or both capabilities:

- historical prices: `fetch_prices(entity, start, end) -> Iterable[PriceRecord]`;
- current observations: `fetch_price(entity) -> PriceRecord`.

`BaseDatasource` implements both as inoperable stub methods: an unsupported capability
raises `UnsupportedDatasourceOperationError`. Historical task processing must override
`fetch_prices`; snapshot execution must override `fetch_price`.

Datasources are loaded dynamically from `datasource.implementation` (`module.path:ClassName`); entity configuration is merged over datasource configuration.

Current source modules include `rest_datasource.py`, `html_datasource.py`, `pse_edge_datasource.py`, `coindesk_ohlc_datasource.py`, `ofx_forex_datasource.py`, `bpi_fund_datasource.py`, `manulife_fund_datasource.py`, `slamc_fund_datasource.py`, `uniswap_datasource.py`, and `chainlink_datasource.py`.

### Web3, Uniswap, and Chainlink sources

`price_sources/web3_datasource.py` provides `Web3DataSource`, the common
snapshot-only base for blockchain sources. It requires these merged datasource
configuration keys:

- `rpc_url` — HTTP RPC endpoint; and
- `contract_address` — contract used to obtain the quote.

Subclasses validate their own additional keys in `__init__` and implement
`fetch_blockchain_price(contract_address, web3) -> Decimal`, which receives the
live Web3 client created by the base class.

`UniswapDataSource` (`price_sources.uniswap_datasource:UniswapDataSource`)
additionally requires:

- `source_token_address` — ERC20 token being priced; and
- `target_token_address` — ERC20 denomination token.

`pool_fee` is optional and defaults to `0`; it identifies the Uniswap V3 pool
fee. `use_v2` is optional and defaults to `false`; when set, quotes are obtained
from the Uniswap V3 QuoterV2 contract instead of the original Quoter. It obtains
ERC20 name, symbol, and decimals through `price_sources/web3/erc20_token.py`,
then calls the Uniswap V3 quoter through `price_sources/web3/uniswap.py`
(`get_uniswap_pool_v3_quoter_swap_price` or
`get_uniswap_pool_v3_quoter_v2_swap_price`).

`ChainlinkDatasource` (`price_sources.chainlink_datasource:ChainlinkDatasource`)
reads the latest round from a Chainlink aggregator proxy through
`price_sources/web3/chainlink.py` and scales the answer by the feed's on-chain
`decimals()`. It requires only the base `rpc_url` and `contract_address` keys.

ABI files live beside those helpers in `price_sources/web3/`.

Web3 clients and contract/token metadata use bounded in-process caches. Contract
and token caches are keyed by the identity of the live Web3 client, not merely by
RPC URL; do not reuse cached contract objects across replacement clients.

Web3 sources currently support snapshots, not historical ranges. Their
`fetch_price()` result must be a single, timezone-aware, non-future `PriceRecord`;
`PriceManagerService.fetch_snapshot_and_store()` normalizes the timestamp to UTC
and persists it without historical-gap checks.

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
| `LIBRAM_SNAPSHOT_SCHEDULER_THREADS` | No | `2` |
| `LIBRAM_SNAPSHOT_POLL_INTERVAL_SECONDS` | No | `10` |
| `LIBRAM_SNAPSHOT_LEASE_SECONDS` | No | `300` |
| `LIBRAM_SNAPSHOT_RETRY_DELAY_SECONDS` | No | `30` |
| `LIBRAM_SNAPSHOT_MAX_BACKOFF_SECONDS` | No | `1800` |
| `LIBRAM_SNAPSHOT_RPC_CONCURRENCY` | No | `2` |
| `LIBRAM_SNAPSHOT_SHUTDOWN_TIMEOUT_SECONDS` | No | `360` |
| `LIBRAM_WEB3_RETRIES` | No | `3` retries when creating a Web3 client |
| `LIBRAM_WEB3_TIMEOUT` | No | `30` RPC connection timeout in seconds |
| `LIBRAM_WEB3_BACKOFF` | No | `5` seconds between Web3 connection retries |

## Project conventions

- Python 3.14, managed by `uv` (`.python-version`).
- SQLAlchemy Core, not a declarative ORM; raw SQL uses `text()`.
- Core domain types are dataclasses in `libram_types/`. Pydantic models are used for HTTP request models, primarily in `fundamentals_management/models.py` and `portfolio_management/models.py`.
- Pure analysis functions live in `price_analysis/` and should not depend on the database or FastAPI.
- Unit tests live outside production packages under `tests/unit/`, mirroring the source domains.
- Docker production setup is in `Dockerfile` and `docker-compose.yml`; supervisor runs the server, historical scheduler executor, and snapshot scheduler as independent programs in one container.
- Web3/Uniswap support is provided by the `web3` and `websockets` runtime dependencies; RPC access is configured per datasource/entity rather than through a single global endpoint.
- Ruff cleanup is part of the current codebase standard: preserve clean imports, formatting, and lint-compatible code.

## Testing

The project uses `pytest`, managed through `uv` and declared in the `dev` dependency group. The current suite is a deterministic unit-test suite; it does not require PostgreSQL, Docker, live network access, or external services.

```bash
# Install or refresh project and development dependencies
uv sync

# Run the complete unit suite
uv run pytest tests/unit -q

# Run a focused domain suite
uv run pytest tests/unit/price_analysis -q
uv run pytest tests/unit/price_sources -q
uv run pytest tests/unit/price_management -q
uv run pytest tests/unit/price_scheduler -q
uv run pytest tests/unit/portfolio_management -q

# Quality checks for tests
uv run ruff check tests/unit
uv run ruff format --check tests/unit
```

### Test layout and boundaries

- `tests/unit/price_analysis/` covers pure calculations, indicators, comparison, drawdown, and date utilities.
- `tests/unit/price_sources/` covers deterministic request construction and payload parsing for datasource implementations.
- `tests/unit/price_management/` covers datasource loading and price fetch/store orchestration.
- `tests/unit/price_scheduler/` covers missing-range generation and executor behavior without sleeping or live services.
- `tests/unit/portfolio_management/` covers portfolio, order, totals, dividend, and fee behavior.
- `tests/unit/price_sources/test_web3_datasources.py` covers Web3/Uniswap/Chainlink snapshot construction, token-unit conversion, ABI loading, and Web3 configuration validation.
- `tests/unit/price_sources/test_web3_helpers.py` covers Ethereum address normalization and Web3/contract cache behavior without live RPC calls.
- Keep tests outside production packages and mirror the production concern they exercise.
- Test behavior rather than implementation details. Use real domain logic where practical; mock only external boundaries such as HTTP, database, and scheduler I/O.
- Use small explicit payloads and expected records. Do not contact live services from unit tests.
- Keep test dates and timestamps deterministic. Use timezone-aware fixtures and explicit conversions; do not mutate process-global `TZ` or call `time.tzset()` in shared test setup.
- Put shared fixtures in `tests/conftest.py` only when they are genuinely cross-domain. Keep domain-specific fixtures beside the tests that use them.
- When adding a test for a missing or experimental optional implementation, do not inject a synthetic production module in `conftest.py`; either test an available implementation or document the limitation.

Integration tests for PostgreSQL, API routes, scheduler lifecycle, and live datasource boundaries are separate from this unit suite. Add them under an explicit `tests/integration/` boundary when that work begins, with isolated services and fixtures rather than weakening unit-test determinism.

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
| `cli_scheduler.py` | Standalone threaded historical scheduler executor |
| `cli_snapshot_scheduler.py` | Standalone recurring snapshot executor |
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
| `snapshot_scheduler/executor.py` | Durable snapshot claim, lease, retry, and shutdown worker |
| `price_analysis/` | SMA, EMA, RSI, comparison, drawdown, and date utilities |
| `price_sources/` | Datasource implementations |
| `price_sources/web3_datasource.py` | Common Web3 snapshot datasource |
| `price_sources/uniswap_datasource.py` | Uniswap V3 datasource adapter |
| `price_sources/chainlink_datasource.py` | Chainlink price feed datasource adapter |
| `price_sources/web3/` | Web3 client/cache helpers, ERC20 support, Uniswap and Chainlink quote helpers, and ABI JSON files |
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
