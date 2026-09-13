# Currency Conversion Plan

Status: Architecture locked; implementation pending.

A standalone module that converts a value from one currency to another using Libram's stored currency entities and price series. Extracted from `docs/fused_entity_data_plan.md`, which specified conversion inline; the fused-entity feature and an output-currency option on `GET /api/v1/prices` are its two consumers. The rules are the ones the fused plan already carried — this document is the extraction, the module's interface and batching semantics, and the raw-price consumer.

## Context

Libram knows how to denominate an entity in a currency and how to store a currency's own price series. It has no reusable way to convert a value between currencies. The existing helpers are narrow:

- `portfolio_management/totals.py` and `dividend_fees.py` convert into PHP, and only into PHP;
- `portfolio_management/dividend_calculation.py` has the right *shape* — a pure function taking an injected FX lookup — but is PHP-destination-only;
- `Database.get_price_at_or_before()` retrieves a rate, but returns a bare value with no timestamp and no lower bound.

### Facts this plan depends on

| Fact | Evidence |
|---|---|
| A currency is an entity. `entity.currency_id` references one. | `schema.sql:23-25` |
| `currency_id IS NULL` means PHP, and PHP has no entity | `schema.sql:23-25`; `portfolio_management/dividend_fees.py:29` |
| A currency entity carries its own price series, quoted "per one of itself" and denominated in its own `currency_id` | `data.sql:16` (`USD`), `data.sql:48` (`USDC`) |
| `type = 'CURRENCY'` is not a usable discriminator: `USDC` is `type = 'CRYPTOCURRENCY'` while eight entities are denominated in it | `data.sql:48`, `data.sql:72-79` |
| `get_price_at_or_before()` returns the most recent `COALESCE(close, price)` at or before an instant, with no lower bound and no timestamp | `db.py:1185-1204` |
| The OFX source emits naive local timestamps and uncoerced numeric rates | `price_sources/ofx_forex_datasource.py:47-53` |
| Unit tests must not require PostgreSQL | `AGENTS.md` "Testing" |

### The invariant everything rests on

> A currency entity's stored price series is denominated in the currency named by its own `currency_id`.

Checked against the current seed graph, and asserted in tests. If an entity's series is actually denominated in something other than its declared `currency_id`, conversion silently mis-scales — there is no runtime metadata that would catch it.

### Seed currency graph

```text
PHP  (NULL — implicit root, no entity)
  |
  +-- USD   a717db40   price: PHP per USD
  |     +-- EUR  79dc5e60, XAU  ba35a4e6
  |     +-- BTC  d88fd071 (chainlink), 8d4e6f2a (kraken)
  |     +-- ETH  e77679f9 (chainlink), bfc56336 (kraken)
  |     +-- SOL  fcd70381
  |
  +-- USDC  51bfd3f0   price: PHP per USDC
        +-- USDT 3a11350f, DAI 5c06cddc
        +-- WBTC 4967e268 (arbitrum), 62b7ec75 (ethereum)
        +-- WETH 68d176d4 (arbitrum), e0b2d39e (ethereum)
        +-- XAUt 6c789a2c, EURC 0643ea24
```

Reachability follows directly, and is the honest limit of the MVP:

| Conversion | Hops | Result |
|---|---|---|
| depth 1 ↔ PHP (`USD`, `USDC`) | 1 | direct / inverse |
| depth 2 ↔ its depth-1 parent (`EUR` ↔ `USD`, `USDT` ↔ `USDC`) | 1 | direct / inverse |
| depth 2 ↔ PHP (`EUR` → PHP, `USDT` → PHP) | 2 | **fails** |
| `USDC` ↔ `USD` | 2 (via PHP) | **fails** |

No member in current seed data is denominated in a depth-2 currency, so a PHP-denominated consumer can convert every one of them. A `USD`-denominated consumer cannot convert the `USDC`-denominated ones (`USDT`, `DAI`, `WBTC`, `WETH`, `XAUt`, `EURC`) — that is two hops.

## Scope

In scope:

- resolving a conversion between two currencies from `currency_id` links;
- direct and arithmetic-inverse conversion;
- a reference-instant rate lookup with no look-ahead;
- a batched, day-keyed rate series for bucket-aligned consumers;
- a per-instant conversion path for consumers holding real timestamps;
- an optional output currency on `GET /api/v1/prices`, converting every monetary field of every returned record;
- a typed failure for every conversion that cannot be performed.

Out of scope:

- multi-hop or pivot-based conversion; cross-rate derivation;
- rate freshness thresholds; rejecting a rate as too old;
- selecting among competing FX sources, or ranking them;
- storing an inverse pair as data (inversion is arithmetic only);
- historical rate versioning or restatement;
- new HTTP endpoints of this module's own — it extends one existing endpoint instead (Decision 11);
- converting `GET /api/v1/prices/summary`, which is not a flag on an existing query (see below);
- caching, persistence, new tables;
- rounding conventions other than 6 dp `ROUND_HALF_UP`;
- currency CRUD; currency entities are created through the existing entity flow.

## Decisions

### 1. A currency is an entity

Currency identity is `entity.id`. There is no symbol matching and no code matching: two entities both named `USD` are two distinct currencies, and a value denominated in one will not convert through the other's series. `NULL` means PHP, and PHP is the only currency with no entity.

This is what makes "no source selection" possible. The rate source is not chosen among candidates — it is named by the link being followed.

### 2. A pair is discovered from `currency_id` links, not from configuration

No `type` check, no `entity.config` parsing, no pair registry, no `base`/`currency` keys. Those keys exist on one datasource's rows and are OFX-specific; `USDC` shows that `type = 'CURRENCY'` would miss a currency that eight entities depend on. The link already states the pair.

### 3. One hop, two directions

```text
C == G                    -> identity; no rate needed
E_C.currency_id == G      -> direct:  value * rate
E_G.currency_id == C      -> inverse: value / rate
otherwise                 -> no path; fail
```

`E_C` is the entity for the source currency `C`; its series is quoted in `G`. `E_G` is the entity for the target currency `G`; its series is quoted in `C`. Either may be absent when that side is PHP.

When both directions resolve, prefer `direct` — deterministic, and unrepresentable in the current data. A reciprocal pair stored twice is a data smell, not an ambiguity to resolve.

### 4. The rate is at or before the reference instant, and never after it

No look-ahead. A historical conversion for day `D` uses the most recent rate at or before the end of `D`; if the series has no observation at or before that instant, the conversion fails rather than borrowing a later rate.

No staleness bound in this MVP: if a rate exists at or before the instant, it is used regardless of age. A freshness policy is a separate concern and is out of scope.

### 5. Precision

```text
direct:  (value * rate).quantize(6dp, ROUND_HALF_UP)
inverse: (value / rate).quantize(6dp, ROUND_HALF_UP)
```

Inverse divides rather than multiplying by a computed reciprocal, so there is one rounding step and no intermediate rule. A rate `<= 0` is invalid. Rates and values are `Decimal`; the OFX path's uncoerced float rates must be converted at the boundary before any arithmetic.

### 6. The module reports; the caller decides

A conversion either returns the converted value — with the caller reporting the arithmetic that produced it — or raises a typed failure:

```text
no_path        -> no single-hop route between the currencies
no_rate        -> no rate at or before the reference instant
invalid_rate   -> rate present but <= 0
```

The module never decides to skip a value or abort a caller's operation. Whether a failure fails an entire request or drops one item is the caller's policy — `docs/fused_entity_data_plan.md` chooses to fail the request.

### 7. A converted value must carry the arithmetic that produced it

A converted value must never be indistinguishable from one observed directly in the target currency, and its arithmetic must be reproducible from the response. Reporting the *stored* rate plus the direction is what makes it reproducible: `direct` means it was multiplied, `inverse` means it was divided.

There is deliberately no shared result *dataclass*. The two consumers report different fields — a price record reports the source currency, rate, and direction once per record, while the fused response reports the raw value as well, once per contributor per day — so each projects the arithmetic into its own response shape. A single type covering both would carry fields neither consumer uses whole, which is the same reason the single-shot `convert()` below is not built.

The requirement is on the response, not on a type: **no consumer may return a converted number without also disclosing the rate and direction applied.**

### 8. Rates are resolved per UTC day and batched

```python
rate_series(rate_entity_id, start, end) -> dict[date, Decimal]
```

One query per rate entity for a whole range, one rate per UTC calendar day (that day's last observation), so a year-long consumer issues one query per rate entity rather than one per day. Within a day, every value converted through a given pair uses the same rate, which is also what makes a range result internally consistent.

This is the same "last observation per UTC day" reduction that daily price consumers need, exposed as a shared database read:

```sql
SELECT DISTINCT ON ((COALESCE(timestamp, timestamp_start) AT TIME ZONE 'UTC')::date)
       (COALESCE(timestamp, timestamp_start) AT TIME ZONE 'UTC')::date AS day,
       COALESCE(timestamp, timestamp_start) AS observed_at,
       COALESCE(close, price) AS value
FROM price
WHERE entity_id = :entity_id
  AND COALESCE(close, price) IS NOT NULL
  AND COALESCE(timestamp, timestamp_start) >= :start
  AND COALESCE(timestamp, timestamp_start) < :end
ORDER BY (COALESCE(timestamp, timestamp_start) AT TIME ZONE 'UTC')::date ASC,
         COALESCE(timestamp, timestamp_start) DESC
```

`DISTINCT ON` is exactly "last row per UTC day", and query cost is proportional to days returned rather than rows stored. Value type is `Decimal`.

### 9. No persistence, no cache, no new tables

Conversion is derived at read time from existing rows. There is no materialized rate table, no FX cache, and no schema change in this plan.

### 10. Layering follows the repository's existing decomposition

| File | Responsibility |
|---|---|
| `currency_conversion/conversion.py` | pure: path resolution, arithmetic, precision, typed failures |
| `currency_conversion/service.py` | `CurrencyConversionService(db)`: entity denomination lookup, rate lookup, day-keyed rate series |
| `currency_conversion/price_records.py` | adapter: convert the records returned by `GET /api/v1/prices` |
| `currency_conversion/__init__.py` | public exports: service, pure functions, exceptions |
| `libram_database/db.py` | `query_daily_last_price()` (Decision 8) |
| `libram_types/libram_types.py` | `FxPath`, `DailyPrice` |
| `dependencies.py` | `get_currency_conversion_service` provider |
| `routes/prices.py` | the new `quote_currency_id` parameter and its error mapping |

`conversion.py` never imports the database or FastAPI. It receives each currency's denomination through an injected callable, so path resolution is testable with literal UUIDs.

### 11. `GET /api/v1/prices` gains an optional output currency

The raw price endpoint accepts `quote_currency_id`: a currency entity UUID, or the literal `PHP`. Omitted means today's behaviour, unchanged. Provided means every monetary field of every returned record is converted into that currency.

That contract is detailed in its own section below, because it is a different consumer from the fused-entity feature and needs a different access pattern for a real reason: a fused series is bucket-aligned, while a raw record carries an actual instant.

## Public interface

```python
# currency_conversion/conversion.py — pure
def resolve_path(from_id, to_id, denomination_of) -> FxPath | None
    # denomination_of: Callable[[UUID], UUID | None] -> that currency entity's own currency_id

def convert(value: Decimal, path: FxPath, rate: Decimal) -> Decimal
    # raises InvalidRate; applies Decision 5

# currency_conversion/service.py — I/O
class CurrencyConversionService:
    def resolve(self, from_id, to_id) -> FxPath | None
    def denomination_of(self, currency_id) -> UUID | None
    def currency_exists(self, currency_id) -> bool
    def currency_code(self, currency_id) -> str
    def rate_at(self, rate_entity_id, at) -> Decimal            # raises NoRate
    def rate_series(self, rate_entity_id, start, end) -> dict[date, Decimal]

# currency_conversion/price_records.py — adapter for GET /api/v1/prices
def convert_price_records(
    records: Iterable[PriceRecord],
    source_currency_id: UUID | None,   # the subject entity's currency_id; None = PHP
    quote_currency_id: UUID | None,    # requested output currency; None = PHP
    fx: CurrencyConversionService,
) -> list[dict]
    # raises NoPath / NoRate / InvalidRate
```

`FxPath` is a small frozen pair: the rate entity id and the direction. Consumers that need many conversions reuse one `FxPath` plus one `rate_series()` result.

The two lookup shapes exist for a reason. `rate_series()` is for consumers whose values are already aligned to a bucket, where one rate per bucket is the correct and honest answer. `rate_at()` is for consumers holding an actual instant, where a bucket rate would be look-ahead. Both are needed; neither is a convenience wrapper on the other.

Deliberately not built: a single-shot `convert(value, from_id, to_id, at)`. Both consumers compose `resolve()` with `rate_at()` or `rate_series()` themselves, so a wrapper would be unused surface. When a point-in-time consumer appears it should return `None` for identity and otherwise report the rate and direction it applied, so that identity is not reported as a conversion that did nothing.

## Consumer: `GET /api/v1/prices`

One new optional parameter:

```text
quote_currency_id=<currency entity UUID> | PHP      optional
```

Omitted: the response is byte-for-byte what it is today — no conversion, no new fields. Existing callers, including the `list_prices_for_entity` MCP tool, are unaffected.

Provided: every monetary field of every returned record is converted into that currency.

### Why the literal `PHP` is accepted

PHP has no entity (Decision 1), and an omitted parameter already means "do not convert", so a UUID alone cannot express "convert to PHP". The literal `PHP` is therefore accepted, case-insensitively, as the sentinel for the entity-less currency.

Resolving a currency *code* instead was rejected: entity codes are unique only per datasource and two entities are named `USD`, so `quote_currency=USD` would be ambiguous by construction — precisely the ambiguity Decision 1 exists to remove.

A blank value is **not** treated as PHP. An empty `quote_currency_id` is a caller bug, not a request for the entity-less currency, and it is rejected as `invalid_value` rather than silently converting. Only an omitted parameter or an explicit `PHP` means PHP.

### Reference instant

Each record converts at its own instant, `COALESCE(timestamp, timestamp_start)`:

| Record | Instant |
|---|---|
| point row | `timestamp` |
| OHLC bar | `timestamp_start` |

Not at the end of the record's UTC day. Day-keying is what the fused-entity consumer uses because its values are already bucketed; applied to a record that carries a real instant it becomes look-ahead — a row at `00:05Z` would convert using a rate observed later that same day, which Decision 4 forbids.

For a daily bar this means conversion at the bar's opening instant. That is the same `COALESCE(timestamp, timestamp_start)` expression the repository already uses for ordering, for `query_close_series()`, and for `query_price_summary()`, so one rule covers every row shape rather than a rule per shape.

### What converts

| Record shape | Fields |
|---|---|
| point row | `price` |
| OHLC bar | `open`, `high`, `low`, `close` |

Every monetary field of a bar converts by the same rate and direction, so OHLC invariants hold by construction: multiplying all four by one positive factor preserves `low <= min(open, close)` and `high >= max(open, close)`. A converted bar cannot become internally inconsistent.

One `FxPath` is resolved per request — the subject entity's `currency_id` to the requested currency — so only the rate varies per record. When the entity is already denominated in the requested currency, no rate is looked up at all.

### Response

Converted values replace the raw ones in place; each record gains two fields:

```json
{
  "price": null,
  "timestamp": null,
  "open": "64980.00",
  "high": "68970.00",
  "low": "64700.00",
  "close": "68411.00",
  "timestamp_start": "2026-01-01T00:00:00Z",
  "timestamp_end": "2026-01-02T00:00:00Z",
  "currency": "PHP",
  "conversion": { "from": "USD", "rate": 57.0, "direction": "direct" }
}
```

- `currency` is what the returned values are now in. `"PHP"` is the label for the entity-less currency, the same convention as `group.currency` in the fused plan.
- `conversion` reports the source currency, the stored rate, and how it was applied, so a converted record is never indistinguishable from one observed in the target currency. It is `null` when the record needed no conversion.
- Raw values are not duplicated. Reporting them would turn a four-number bar into eight, and the rate plus direction makes them recoverable by division anyway.
- Monetary values stay `Decimal` and encode as JSON numbers, because this endpoint already emits numbers. Making a field's type depend on whether a parameter was passed would be a worse trap than the precision it preserved. The fused-entity response, which is new, encodes money as strings for exactly that precision reason; the two endpoints differ deliberately.

### Layering

The route keeps its present shape — parse, resolve the entity's timezone, delegate — and gains one more delegation:

```python
records = price_manager.query_prices(entity_id, start_dt, end_dt, page, size)
return convert_price_records(records, entity["currency_id"], quote_currency_id, fx)
```

`convert_price_records()` lives in `currency_conversion/price_records.py`, so the handler holds no conversion logic. `PriceManagerService` is deliberately not modified: the raw-price domain does not acquire a conversion dependency, and no existing construction site or test changes.

### Cost

Each record needs its own rate lookup, bounded by `size`. The default page of 10 is trivial. A large `size` on a `CONTINUOUS` entity is not: `get_price_at_or_before()` orders by `COALESCE(timestamp, timestamp_start)`, while the existing index is on `(entity_id, timestamp)`.

Two bounded fixes exist if it becomes a problem — an expression index on the effective timestamp, or one range read of the rate entity plus a bisect per record (the same shape as `rate_series()`, but returning every observation rather than the last per day). Neither is in this plan; the cost is recorded rather than pre-optimized.

### `GET /api/v1/prices/summary` is not converted

Explicitly out of scope, and not for tidiness. `summary` aggregates in SQL — `min`, `max`, `avg`, `std_dev`, `first_close`, `last_close`. With a rate that varies per record, the mean of converted values is not the converted mean:

```text
avg(convert(x)) != convert(avg(x))
```

Converting it means converting every row before aggregating, which is a different computation from the existing query rather than a flag on it. If it is wanted, it should be its own decision with its own definition of what those aggregates mean in the output currency.

### Errors

| Condition | Status | Body |
|---|---|---|
| `quote_currency_id` is neither a UUID nor `PHP` | 422 | `{"error": "invalid_value", "field": "quote_currency_id", "value": "..."}` |
| `quote_currency_id` names no existing entity | 404 | `{"error": "entity_not_found", "entity_id": "<uuid>"}` |
| The entity's currency has no single-hop path to the requested currency | 422 | `{"error": "fx_no_path", "from": "USDC", "to": "USD"}` |
| A record's rate is missing or `<= 0` | 422 | `{"error": "fx_rate_unavailable", "pair": "<rate entity code>", "at": "<record instant or null>", "reason": "<detail>"}` |

Both `NoRate` and `InvalidRate` map to the single `fx_rate_unavailable` code: a client cannot act differently on a missing rate than on a non-positive one, and `reason` distinguishes them. `pair` names the rate entity's currency code and `at` is the record instant the rate was sought at, so a failure identifies which conversion broke and when.

## Errors

| Condition | Raised | HTTP status a consumer maps it to |
|---|---|---|
| No single-hop route | `NoPath` | 422 |
| No rate at or before the reference instant | `NoRate` | 422 |
| Rate `<= 0` | `InvalidRate` | 422 |

Exceptions are defined in `currency_conversion/conversion.py` and exported from the package, per `AGENTS.md` ("Request models and domain exceptions → relevant domain package"). Both consumers map these three to 422 rather than inventing statuses of their own.

## Implementation phases

Two phases, each independently verifiable. No schema change in either.

### Phase 1: the module

- Add `FxPath` and `DailyPrice` to `libram_types/libram_types.py`.
- Add `Database.query_daily_last_price()` per Decision 8.
- Add `currency_conversion/conversion.py`: `resolve_path()` in both directions with the `direct` preference, `convert()` with the Decision 5 arithmetic, and the three exception types.
- Add `currency_conversion/service.py` and the `dependencies.py` provider.
- Tests (`tests/unit/currency_conversion/`, literal inputs, no PostgreSQL): direct and inverse resolution; identity when `C == G`; `no_path` for a two-hop case (`USDC` → `USD`); `direct` preferred when both resolve; `None` handled as PHP on either side; direct and inverse arithmetic; 6-decimal quantization, half-up; `invalid_rate` for `0` and negative; `no_rate`; `rate_series` day-keying and the no-look-ahead boundary.

### Phase 2: output currency on `GET /api/v1/prices`

- Add `currency_conversion/price_records.py` with `convert_price_records()`.
- Add the `quote_currency_id` parameter to `routes/prices.py`, including the `PHP` sentinel, its validation, and the error mapping. Leave `PriceManagerService` unmodified.
- Tests (`tests/unit/currency_conversion/test_price_records.py`, a fake `CurrencyConversionService`): a point row's `price` converted; all four OHLC fields converted by the same factor; OHLC invariants preserved; a record already in the target currency returned with `conversion: null`; the reference instant is the record's own instant; `NoPath` and `NoRate` propagate.
- Add `tests/integration/test_prices_currency_conversion.py`: the HTTP contract end to end, using dependency overrides instead of PostgreSQL so it needs no services — an unchanged response when the parameter is omitted, converted records, `PHP` accepted, a currency code rejected, and the 404/422 mapping.

`query_daily_last_price()` is SQL and needs PostgreSQL, so its own test belongs in the `tests/integration/` boundary per `AGENTS.md` rather than the deterministic unit suite.

## Risks and safeguards

- **Denomination is asserted, not verified.** The invariant stated under Context — a currency entity's series is denominated in its own `currency_id` — cannot be checked at runtime. Test it against the seed graph; treat a new currency entity as requiring that check.
- **Silent mis-scaling is the failure mode of getting this wrong.** A wrong direction or a mis-declared denomination produces a plausible number. Hence Decision 7's reproducible arithmetic and the direct-preference rule rather than guessing.
- **Naive FX timestamps are an unverified look-ahead exposure.** `ofx_forex_datasource.py:47-53` builds its timestamps with `datetime.fromtimestamp(...)`, which yields *naive host-local* time, and they are written into a `timestamptz` column. If the writing session's zone is not UTC, every stored rate instant is shifted; a shift **earlier** means `get_price_at_or_before(rate_entity, record_instant)` can select a rate whose true observation was *after* the record's instant — exactly what Decision 4 forbids. Concretely, on a `+08:00` session a rate really observed at `10:00Z` is stored as `02:00Z` and will convert a record observed at `03:00Z` using a rate from its future. Nothing in this plan detects that. **Treat the session-zone assumption as a precondition, not a detail:** confirm it against a real database before relying on converted values, and normalise the datasource to UTC or reject non-UTC sessions if it does not hold.
- **The day-keyed read ships without a production caller.** `rate_series()` and `Database.query_daily_last_price()` exist for the fused-entity consumer, which is not implemented on this branch, so the SQL itself is exercised by no test here — the deterministic unit suite cannot reach PostgreSQL. Verify it against a real database before the fused-entity phase relies on it; until then its `DISTINCT ON`/`AT TIME ZONE 'UTC'` bucketing is reviewed, not proven.
- **`quote_currency_id` names a denomination, not a symbol or a "currency" in the narrow sense.** Because `resolve_path()` compares ids only, *any* entity can serve as the target unit: converting a PHP value into a share-count by passing a PHP-denominated stock's entity id is arithmetically correct and labels the output with that entity's code. This is deliberate — the model's whole premise is that identity is an entity id — but it means the parameter will happily convert money into non-money units and must not be validated against a `type` column, which is itself unreliable (`USDC` is `type = 'CRYPTOCURRENCY'`). Documented rather than narrowed, because narrowing to "entities referenced as some `currency_id`" would reject a legitimately new currency that nothing references yet.
- **Uncoerced float rates are handled, not assumed.** The OFX parser stores `InterbankRate` uncoerced, so `to_decimal()` coerces at the boundary and positivity is judged on the coerced value. Keep coercion before arithmetic, never after.
- **No staleness bound.** Stated in Decision 4. A consumer that needs freshness must add it; nothing here will alert on a months-old rate.
- **A missing rate fails a whole request through both consumers.** Deliberate: the fused series fails, and `/prices` fails rather than returning a page whose values are in mixed currencies. A caller wanting a best-effort page is asking for a different contract, and should say so.
- **A converted record declares its currency; an unconverted one still does not.** `/api/v1/prices` has never said what currency its values are in — that is discoverable only from the entity. This plan does not add the field unconditionally, because doing so changes an existing response shape. The asymmetry between a converted and an unconverted page is real and worth revisiting on its own.
- **The `PHP` sentinel is an API wart, and a direct consequence of the schema.** PHP is represented by absence, so no UUID can name it. If a PHP currency entity is ever introduced, the sentinel and `NULL`-means-PHP disappear together.
- **Two-hop failures are the common surprise.** `USDC ↔ USD`, `EUR → PHP`, and `USDT → PHP` all fail. The seed graph table above is the reference for what is reachable; make it the first thing read when a conversion "should" work.
- **Depth-2 → PHP failures will surprise a consumer that adds a USD- or USDC-denominated member to a PHP group.** Covered by the same table.

## Verification

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run ruff check currency_conversion routes/prices.py dependencies.py libram_database/db.py
uv run ruff format --check currency_conversion tests/integration
git diff --check
```

Repo-wide `uv run ruff check .` and `uv run ruff format --check .` are not usable as gates and are not the commands to run. As measured on `main`, the repository carries 318 lint findings and 41 unformatted files before this plan starts — and `ruff check .` also walks `.worktrees/`, which inflates the count to roughly 6000. Lint and format the paths a change actually touches.

Each `Depends(...)` argument default this plan adds produces one `B008` finding — three across the changed files. That rule fires on FastAPI's dependency-injection idiom, which the repository uses throughout and FastAPI requires; it is not fixable without a ruff configuration this plan does not add.

Focused tests must cover: path resolution in both directions, identity, `no_path`, direct preference, PHP on either side, both arithmetic directions, quantization, invalid rates, missing rates, the no-look-ahead boundary, and — for the `PriceRecord` adapter — point-row and OHLC-record conversion, one shared factor across a bar's fields, invariants preserved, identity returning `conversion: null`, and the record's own instant used as the reference.

No implementation, migration, or seed-data change ships with this document.

## Repository references

- `schema.sql` — `entity` (line 19), `price` (line 40).
- `data.sql` — the currency entities and `currency_id` links the seed graph is derived from.
- `AGENTS.md` — module placement, layering rules, testing boundaries.
- `libram_database/db.py` — `get_price_at_or_before()` (1185), the lookup this module wraps.
- `libram_types/libram_types.py` — `PriceRecord`, the record the `/prices` adapter converts.
- `routes/prices.py` — the endpoint this plan extends: `query_prices()` and the record response shape.
- `portfolio_management/dividend_calculation.py` — the existing injected-FX-lookup pattern.
- `portfolio_management/totals.py`, `dividend_fees.py` — PHP-destination-only helpers this module generalizes; they are not refactored by this plan.
- `price_sources/ofx_forex_datasource.py` — the stored FX pair source.
- `docs/fused_entity_data_plan.md` — the other consumer.
