# Fused Entity Data Plan (v2)

Status: Architecture revised; implementation pending. Supersedes `fused_entity_data_plan.md` (v1) pending review.

v2 keeps the v1 model — `entity -> entity_group_member -> entity_group`, explicit membership, query-time fusion, no persisted derived layer — and resolves the contradictions that blocked implementation. Every revision is listed as a decision with a rationale in "Changes from v1" and "Decisions taken in v2".

## Changes from v1

Resolved (were contradictions or undefined contracts):

1. **FX inversion** is defined as arithmetic inversion of a single stored pair, not a stored reverse pair. v1 required "inverse stored FX pairs", which cannot exist: PHP is represented by NULL, so no `PHP -> X` entity is representable.
2. **Freshness** is bucket-relative. v1's single `max_staleness_seconds=300` was applied to year-long historical ranges with no defined reference instant.
3. **Daily bucketing has an explicit calendar basis.** v1 hard-coded UTC while the repository treats `entity.timezone` as authoritative.
4. **Phase order fixed.** v1's Phase 2 promised daily OHLC output while all OHLC reconciliation sat in Phase 3.
5. **Failure classes are separated.** FX faults fail the request; incompatible or invalid members are rejected per member. v1 said both.
6. **Group output currency has one owner** (the group row). v1 had a request parameter and a column with no stated precedence.
7. **Median's properties are stated honestly** for the n=2 case the plan's own example uses.
8. **The `mode=ohlc` response shape is specified** (v1's example returned a scalar `price` with no OHLC fields).
9. **Removed as constant or dead surface:** `mode` (one value), `resolution` (fixed by scope), `include_provenance` (always required), `conversion_errors` (always `0` under fail-fast), three unused `relationship_type` values, three undefined quality fields, HTTP status mapping, deterministic ordering, PUT payload semantics, and `enabled=false` behavior.
10. **Module ownership assigned** per `AGENTS.md`.
11. **`entity_group.timezone` added**; reverse index on membership added; `ON DELETE RESTRICT` made explicit on `quote_currency_id`.

Kept from v1 without change: source entities and raw prices untouched; explicit closed-world strong membership; broad inclusion opt-in; strength/relationship/confidence distinct from observation quality; equal-weight median as the only aggregation method; fused results never written to `price`; multi-hop conversion, configurable weighting, historical membership, and persisted derived data out of scope.

## Context

Libram stores source-specific entities and their raw price observations:

```text
entity -> datasource
entity -> price observations
```

The same reference may be available through multiple entities:

```text
CoinDesk / BTC
Chainlink / BTC
```

Other entities may be related without being interchangeable:

```text
WBTC -> related to BTC as a wrapped representation
```

Libram should support grouping these entities and deriving a fused price series without changing the source entities or raw price rows. The feature uses one relationship:

```text
entity -> entity_group_member -> entity_group
```

The membership row describes the entity's relationship to that particular group. Group membership is explicit and scoped to the group.

### Repository facts this plan depends on

| Fact | Evidence |
|---|---|
| `entity` is source-specific; `UNIQUE (datasource_id, code)` | `schema.sql:19-35` |
| Entity codes are not globally unique | `schema.sql:34` |
| `entity.currency_id` is nullable; NULL means PHP | `schema.sql:23-25`; `portfolio_management/dividend_fees.py:29` |
| `entity.timezone` is NOT NULL and authoritative for that entity's dates | `schema.sql:31`; `routes/prices.py:43-48` |
| `entity.frequency` is `DAILY`, `CONTINUOUS`, etc.; `has_weekend` exists | `schema.sql:29-30` |
| `price` holds either a point (`price`,`timestamp`) or a full OHLC interval | `schema.sql:40-77` |
| Existing per-entity range queries convert caller dates into the entity timezone | `routes/prices.py:20-31`; `price_analysis/date_utils.py:15-35` |
| `Database.get_price_at_or_before()` returns a value only, with no lower bound | `libram_database/db.py:1185-1204` |
| FX pairs are encoded as `entity.config` on a `type='CURRENCY'` row | `data.sql:16` |
| Only `USD` (base USD, quote PHP) exists in seed data; PHP has no entity | `data.sql:16`; `portfolio_management/dividend_fees.py:29` |
| A pure, injected FX-lookup pattern already exists | `portfolio_management/dividend_calculation.py:23,42` |
| OFX FX parsing emits naive local timestamps | `price_sources/ofx_forex_datasource.py:47-53`; `tests/unit/price_sources/test_datasources.py:29-31` |
| Unit tests must not require PostgreSQL | `AGENTS.md` "Testing" |

## Scope

This plan covers:

- creating and managing named entity groups;
- adding source-specific entities to a group;
- distinguishing strong and broad membership;
- recording membership relationship, confidence, and provenance;
- deriving a fused price from group members at query time;
- converting member prices into the group's output currency;
- reconciling point-price and OHLC inputs into daily output;
- limiting fused queries to daily granularity and at most one year per range;
- returning contributor, exclusion, and observation-quality metadata.

Out of scope for this plan:

- automatic membership inference; fuzzy symbol or name matching;
- relationships between groups;
- persisted or materialized fused prices;
- multi-hop currency conversion; automatic selection among competing FX sources;
- sub-daily fused output;
- alternative aggregation or source-selection methods;
- historical membership versioning or an audit event log;
- label-based day alignment across differing per-entity day conventions (see Decision 6).

## Decisions

### 1. Preserve source entities and raw prices

`entity` remains source-specific and `price` continues to hold raw observations keyed by `entity_id`. Do not merge entities on matching symbols or names. `BTC` from two datasources may be explicitly added to one group; no automatic identity inference is required.

### 2. Membership is explicit and group-scoped

The membership table is the authoritative record of why an entity belongs to a group, whether it is strong or weak, how confident the assertion is, where it came from, and whether it is enabled. A matching code, name, or external relationship does not create membership.

Membership is referenced by `entity_id` (UUID), never by entity code: codes are unique only per datasource (`schema.sql:34`). Responses therefore always pair `entity_code` with `datasource`.

### 3. Strength and broadness are selection modes over one group

Use one group with member-level strength rather than duplicated strong and weak groups.

```text
strong -> enabled rows where membership_strength = 'strong'
broad  -> enabled rows where membership_strength IN ('strong', 'weak')
```

`broad` is the single name for the inclusive mode. Weak membership does not imply low-quality data; it means a looser relationship to the group.

### 4. Strength, relationship, and confidence are separate

```text
membership_strength -> may this entity participate in strict fusion?
relationship_type   -> what is the entity's relationship to this group?
confidence          -> how certain is the membership assertion?
```

A high-confidence wrapped relationship is still not strong membership in a pure BTC fusion. Observation quality is a separate concern that belongs to the fusion result, not the membership row (Decision 11).

### 5. Strong membership is explicit and closed-world

A strong query includes only explicitly enabled strong members. It must not discover members through matching symbols or names, inferred related entities, wrapped/derivative/proxy relationships, or correlation. Including WBTC in a strong BTC result requires an explicit strong membership row for that group.

### 6. Daily buckets use the group's declared calendar

`entity_group.timezone` is a required IANA timezone name (default `UTC`) that defines the group's calendar day. It exists because a group has no single member whose timezone could serve as the basis, and because the repository already treats a declared timezone as authoritative for an entity's dates (`routes/prices.py:43-48`).

For a requested day `D`:

```text
period_start = D at 00:00 in entity_group.timezone, converted to UTC
period_end   = D + 1 day at 00:00 in entity_group.timezone, converted to UTC
```

Buckets are half-open `[period_start, period_end)`. The `date` label on a result bar is `D` in the group timezone; `period_start` and `period_end` are the corresponding UTC instants.

Per-entity assignment for day `D`:

- **Point observations** are assigned to the group-timezone day containing their instant.
- **A native OHLC bar** contributes only if `[timestamp_start, timestamp_end)` equals `[period_start, period_end)` exactly.
- **Finer OHLC bars** may compose into a daily bar only if their intervals exactly tile the day with no gap, overlap, or partial coverage.
- **Coarser or partially overlapping bars** are rejected with reason `partial_day_coverage`. Do not shift, stretch, or resample them.

**Consequence, stated plainly:** a group's timezone must match the day convention its native daily bars are quoted in. A group mixing, for example, `Asia/Manila` daily bars with `UTC` daily bars cannot fuse at daily granularity under this rule; the mismatched member is rejected with an explicit reason rather than silently reassigned. Label-based alignment across differing day conventions is deliberately deferred (see Deferred).

### 7. Currency conversion is group-scoped and fails fast

The group's output currency is `entity_group.quote_currency_id`. `NULL` means PHP, matching `entity.currency_id` and `portfolio_order.*_entity_id` semantics. There is no per-request override in this plan (see "Decisions taken in v2").

A member whose pricing currency differs from the group's output currency is converted before fusion, using stored currency-pair entities and their price observations. Only direct and single-arithmetic-inverse conversion are supported; multi-hop is out of scope.

If a required conversion is unavailable, ambiguous, stale, or invalid, the **entire request fails**. Do not omit the member and do not return a partial result.

Conversion is a group concern but not a group-only implementation: the pure conversion function is reusable and takes an injected lookup (Decision 12).

### 8. Fused output is daily and bounded

The first implementation produces daily output only. Point prices and source OHLC data are inputs, not alternate output modes. Sub-daily output is out of scope.

Every fused query requires `start` and `end`. The range is inclusive at `start` and exclusive at `end` and may span at most one year. A request outside these limits fails validation before any price or FX data is read. `2026-01-01` through `2027-01-01` is exactly one year and valid; through `2027-01-02` is not.

### 9. Fusion is derived at query time

Do not insert fused results into `price`. That table represents source observations; a fused value is derived from several. The initial implementation calculates fusion at query time and returns contributors, exclusions, and observation-quality metadata. Persisted derived prices are out of scope.

### 10. Aggregation is a fixed equal-weight median

Median is the only supported method. It resists a single stale or anomalous contributor once three or more independent contributors are present. At two contributors the median equals the mean, so it provides no outlier resistance; `spread` and `median_absolute_deviation` additionally degenerate below three. The response discloses the contributor count per bar rather than hiding this, and `median_absolute_deviation` is `null` when there are fewer than three contributions instead of reporting a misleading number.

A request with no valid contribution for any selected entity returns an empty `bars` list, not an error. A request with exactly one contributing entity returns that bar marked `is_fallback = true`.

### 11. Membership confidence and observation quality are separate

The membership row records confidence in the relationship ("WBTC is confidently related to the group as a wrapped representation"). The fused result records quality of the observations ("WBTC's current observation is stale or divergent"). Observation quality covers freshness, missing data, conflicts, and contributor count, and must not be inferred from membership confidence.

### 12. Layering follows the repository's existing decomposition

Pure logic is separated from I/O so it is testable without PostgreSQL, which the unit suite forbids.

```text
routes/entity_groups.py            HTTP only: parse, delegate, map errors
entity_group_management/service.py orchestration: group/membership CRUD, fusion requests
entity_group_management/models.py  Pydantic request models
entity_group_management/membership.py  pure membership resolution
price_analysis/fx.py               pure conversion, injected lookup
price_analysis/fusion.py           pure bucketing, reconciliation, aggregation
libram_database/db.py              SQL: group/membership CRUD, FX value+timestamp lookup
libram_types/libram_types.py       EntityGroupRecord, EntityGroupMemberRecord, FxRate
dependencies.py                    get_entity_group_service provider
server.py                          include the new router
schema.sql                         DDL
```

`price_analysis/fx.py` generalizes the existing injected-lookup pattern in `portfolio_management/dividend_calculation.py:23,42` (`FxLookup = Callable[[UUID, date], Decimal | None]` plus a pure `_to_php`) from PHP-destination-only to arbitrary currency pairs.

## Proposed schema

Idempotent DDL, matching `schema.sql` conventions. PostgreSQL 18 supplies `uuidv4()`.

```sql
CREATE TABLE IF NOT EXISTS entity_group (
    id uuid PRIMARY KEY DEFAULT uuidv4(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    description text,
    -- NULL means PHP, matching entity.currency_id semantics.
    quote_currency_id uuid REFERENCES entity(id) ON DELETE RESTRICT,
    -- Calendar basis for daily bucketing. IANA name, mirrors entity.timezone.
    timezone text NOT NULL DEFAULT 'UTC',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS entity_group_member (
    group_id uuid NOT NULL
        REFERENCES entity_group(id) ON DELETE CASCADE,
    entity_id uuid NOT NULL
        REFERENCES entity(id) ON DELETE CASCADE,

    membership_strength text NOT NULL
        CHECK (membership_strength IN ('strong', 'weak')),

    -- Only the types required by the current design are permitted.
    -- Adding a type later requires an explicit CHECK alteration.
    relationship_type text NOT NULL
        CHECK (relationship_type IN ('same_reference', 'wrapped')),

    -- Confidence in the membership assertion, not the price observation.
    confidence text NOT NULL DEFAULT 'medium'
        CHECK (confidence IN ('high', 'medium', 'low')),

    enabled boolean NOT NULL DEFAULT true,
    -- Expected keys mirror entity_fundamentals naming for portability:
    -- source_name, source_url, as_of_date, note.
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (group_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_group_member_entity
    ON entity_group_member (entity_id);
```

Notes on the schema:

- `created_at`/`updated_at` record row lifecycle only. Membership *history* (an event log enabling point-in-time reconstruction) remains out of scope; timestamps are not history.
- `PRIMARY KEY (group_id, entity_id)` already serves group-first lookups; `idx_entity_group_member_entity` serves entity-to-groups lookups.
- `quote_currency_id` uses `ON DELETE RESTRICT` so a currency entity in use cannot be deleted silently.
- The schema deliberately omits group-wide aggregation configuration, member weights, source priorities, validity ranges, and multi-hop configuration.

### Membership examples

```text
Group: btc        timezone: UTC        quote_currency: NULL (PHP)

Entity               Datasource   Strength  Relationship     Confidence
BTC                  coindesk     strong    same_reference   high
BTC                  chainlink    strong    same_reference   high
WBTC                 ethereum     weak      wrapped          high
```

## Membership resolution

`entity_group_management/membership.py` is pure: it accepts member records plus a mode and returns included and excluded members with reasons. It performs no database access.

```text
strong -> enabled rows where membership_strength = 'strong'
broad  -> enabled rows where membership_strength IN ('strong', 'weak')
```

Reason codes:

| Reason | Meaning |
|---|---|
| `weak_member_excluded_by_strong_mode` | Weak member not selected under `strong` |
| `membership_disabled` | `enabled = false`; excluded in both modes |
| `incompatible_semantics` | Units, decimal interpretation, price meaning, or interval semantics differ |
| `invalid_source_bar` | Source bar violates OHLC invariants |
| `partial_day_coverage` | Bar is coarser than, or partially overlaps, the requested day |
| `stale_observation` | Failed the applicable freshness rule (Decision 8 / below) |
| `missing_observation` | No observation in the bucket |

Group membership is current-state only. Historical membership dates are deferred until historical group reconstruction is needed.

## Currency conversion

### Pair discovery

An FX pair is a stored entity with `type = 'CURRENCY'` whose `config` carries `base` and `currency`; its price is "quote currency per one base currency". Seed data:

```text
entity USD   type CURRENCY   currency_id NULL   config {"base": "USD", "currency": "PHP"}
```

Given a member priced in currency `C` and a group output currency `G`:

```text
C == G                -> no conversion (conversion is null in the response)
pair base == C, quote == G -> direct:  value = price * rate
pair base == G, quote == C -> inverse: value = price / rate
otherwise             -> no path: fail the request
```

Because PHP is represented by absence, the comparison treats "PHP" as the empty currency. A member priced in PHP fused into a USD group therefore resolves against the `USD` pair as an inverse conversion and divides.

### Pair resolution

A member's pricing currency entity is `entity.currency_id`; `NULL` means PHP and requires no conversion to a PHP group. Inversion is arithmetic (`Decimal(1) / rate`); no reverse pair entity is required or expected.

### Rate lookup contract

`libram_database/db.py` gains a lookup returning the value **with** its observation instant and a lower bound, since `get_price_at_or_before()` (`db.py:1185-1204`) returns a bare value with no floor:

```python
@dataclass(frozen=True)
class FxRate:
    value: Decimal        # rate actually applied
    source_rate: Decimal  # stored rate before any inversion
    observed_at: datetime # tz-aware UTC
    entity_id: UUID       # stored pair entity used
    direction: str        # "direct" | "inverse"
```

```text
lookup(currency_entity_id, target, max_age_seconds) -> FxRate | None
```

Selection: the most recent `COALESCE(close, price)` from the pair entity with `COALESCE(timestamp, timestamp_start)` in `[target - max_age_seconds, target]`, ordered by that expression descending. No qualifying row means the pair is stale or missing and the request fails.

Note that this inherits the existing helper's semantics: for an OHLC pair row the value is the bar's `close` while the ordering key is `timestamp_start`. `observed_at` must therefore be reported as `timestamp_start` and the response must not present a bar-start instant as the instant the rate was observed.

### Precision

- Rates are read as `Decimal`. The OFX parser currently stores `InterbankRate` uncoerced (`price_sources/ofx_forex_datasource.py:49`), which yields a float in tests; the pair path must coerce to `Decimal` before arithmetic.
- Inversion uses `Decimal(1) / source_rate` quantized to 10 decimal places, `ROUND_HALF_UP`.
- A rate `<= 0` is invalid and fails the request.
- Converted contribution values are quantized to 6 decimal places, `ROUND_HALF_UP`, matching the `DecimalPlaces: 6` the OFX request already asks for.
- Converted contributions carry `conversion` provenance and must never be indistinguishable from a value observed directly in the output currency.

### FX freshness

`fx_max_staleness_seconds` defaults to `604800` (7 days), appropriate for the daily-reporting pair source. The reference instant is the same one used for price staleness (below).

The rate is selected and applied once per bucket, at that bucket's reference instant, and the resulting converted value is reused for every contributor in the bucket. `age_seconds` is therefore identical across the contributors of a bar.

## Fusion pipeline

Two stages, so source frequency does not determine influence:

```text
raw price rows
  -> resolve explicit group membership
  -> validate compatible selected entities
  -> resolve each entity's pricing currency
  -> convert values into the group's output currency
  -> assign observations to group-timezone daily buckets
  -> produce at most one contribution per entity per bucket
  -> apply freshness and conflict rules
  -> aggregate across entities
  -> attach contributors, exclusions, and observation quality
```

A high-frequency entity must not dominate a low-frequency entity merely because it has more raw rows. Do not forward-fill or interpolate.

### Freshness

Freshness is bucket-relative, never wall-clock for historical buckets. The reference instant and threshold depend on whether the bucket has elapsed:

| Bucket state | Definition | Reference instant | Threshold |
|---|---|---|---|
| Open | contains `now` | `now` (UTC) | `max_staleness_seconds`, default 300 |
| Closed | `period_end <= min(now, end_instant)` | `period_end` | expected cadence, below |

Expected cadence for a closed bucket, in order of preference:

1. `snapshot_state.interval_seconds` for the entity, when present (`schema.sql:180-198`);
2. `86400` when `entity.frequency = 'DAILY'`;
3. the request-level `default_bucket_gap_seconds`, default `86400`.

An entity that has no observation within one expected cadence of the reference instant is `stale_observation` for that bucket and excluded from aggregation for it, while the request still succeeds.

A closed bucket whose entity produces no observation at all is `missing_observation`. An open bucket is returned with `"partial": true` and is not subject to the closed-bucket cadence rule.

## Reconciliation into daily bars

For each selected entity and requested day `D`:

1. Use a native OHLC bar when `[timestamp_start, timestamp_end)` equals `[period_start, period_end)` exactly. Form: `native`.
2. Compose a daily bar from finer bars only when those intervals exactly tile the day. Form: `composed`.
   ```text
   open  = earliest open
   high  = maximum high
   low   = minimum low
   close = latest close
   ```
3. Reconstruct a bar from point observations within the bucket. Form: `synthetic`.
   ```text
   open  = first point
   high  = maximum point
   low   = minimum point
   close = last point
   ```
4. Reject coarser or partially overlapping bars as `partial_day_coverage`. Never fabricate daily detail from them.
5. Validate source bars before use. A bar violating `low <= min(open, close)`, `high >= max(open, close)`, or `low <= high` is rejected as `invalid_source_bar`, not repaired.
6. A point outside a bar's `[low, high]` range raises the `conflict` flag; it is not averaged with that bar's close.

A single point cannot establish a genuine daily high or low. In the close-only stage (Phase 2) one point is sufficient because `close` is well defined. In the OHLC stage (Phase 3) a synthetic bar requires at least two points in the bucket; with exactly one point and `allow_single_point_bar=true`, `close` is emitted and `open`/`high`/`low` are `null` with `fields.<name>.form = "unavailable"`.

### Cross-entity aggregation

```text
open  = median(entity opens)
high  = median(entity highs)
low   = median(entity lows)
close = median(entity closes)
```

Field-level medians are computed independently, then validated:

```text
high >= max(open, close)
low  <= min(open, close)
low  <= high
```

For individually valid source bars these invariants hold by construction: order statistics are monotone, so independent per-field medians preserve the coordinate-wise inequalities. A violation therefore indicates an invalid input that escaped source validation. Treat it as an internal assertion, not a user-facing rejection path, and ensure source validation runs first.

A bar-level field `form` is the weakest form among the contributors to that field: `unavailable` < `synthetic` < `composed` < `native`.

A market-envelope mode for venue-like sources is deferred.

## Metric definitions

All counts are per bar. `relative_spread` and `coverage` are reported as decimal strings in `[0, 1]`.

| Field | Definition |
|---|---|
| `contributing_entity_count` | Entities contributing a valid value to this bar |
| `missing_entity_count` | Selected entities with no observation in the bucket |
| `stale_entity_count` | Entities excluded by the freshness rule |
| `rejected_entity_count` | Entities excluded for `incompatible_semantics`, `invalid_source_bar`, or `partial_day_coverage` |
| `spread` | `max(contribution) - min(contribution)` in the output currency; `"0"` when fewer than two contributions |
| `relative_spread` | `spread / median`; `"0"` when the median is `0` or there are fewer than two contributions |
| `median_absolute_deviation` | `median(abs(x - median(x)))`; `null` when fewer than three contributions |
| `conflict` | `true` when `relative_spread > conflict_relative_spread_threshold` (default `"0.01"`) or a point fell outside a bar's `[low, high]` |
| `is_fallback` | `contributing_entity_count == 1` |
| `coverage` | Request-level: `bars_returned / requested_days`, where `requested_days` is the count of group-timezone days in `[start, end)` |

Conflict is reported in metadata rather than resolved by rejection. A future strict mode may require a minimum contributor count.

## Response shape

Fused bars are returned as a list ordered by `date ASC`. Contributors and exclusions are per bar, because staleness and coverage vary by day. Request-level quality is a rollup.

Phase 2 (close only):

```json
{
  "group": {
    "code": "btc",
    "name": "Bitcoin reference",
    "membership": "strong",
    "quote_currency": "PHP",
    "timezone": "UTC"
  },
  "range": { "start": "2026-01-01", "end": "2027-01-01", "requested_days": 365 },
  "aggregation": { "method": "median", "fields": ["close"] },
  "bars": [
    {
      "date": "2026-01-01",
      "period_start": "2026-01-01T00:00:00Z",
      "period_end": "2026-01-02T00:00:00Z",
      "partial": false,
      "close": "68402.565000",
      "contributors": [
        {
          "entity_code": "BTC",
          "datasource": "chainlink",
          "strength": "strong",
          "relationship": "same_reference",
          "membership_confidence": "high",
          "form": "native",
          "raw_value": "1200.09",
          "value": "68405.130000",
          "observed_at": "2026-01-01T23:59:55Z",
          "conversion": {
            "fx_entity": "USD",
            "direction": "direct",
            "source_rate": "57.000000",
            "rate": "57.000000",
            "observed_at": "2026-01-01T00:00:00Z",
            "age_seconds": 86400
          }
        },
        {
          "entity_code": "BTC",
          "datasource": "coindesk",
          "strength": "strong",
          "relationship": "same_reference",
          "membership_confidence": "high",
          "form": "native",
          "raw_value": "1200.00",
          "value": "68400.000000",
          "observed_at": "2026-01-01T23:59:00Z",
          "conversion": {
            "fx_entity": "USD",
            "direction": "direct",
            "source_rate": "57.000000",
            "rate": "57.000000",
            "observed_at": "2026-01-01T00:00:00Z",
            "age_seconds": 86400
          }
        }
      ],
      "excluded": [
        {
          "entity_code": "WBTC",
          "datasource": "ethereum",
          "relationship": "wrapped",
          "reason": "weak_member_excluded_by_strong_mode"
        }
      ],
      "quality": {
        "contributing_entity_count": 2,
        "missing_entity_count": 0,
        "stale_entity_count": 0,
        "rejected_entity_count": 0,
        "spread": "5.130000",
        "relative_spread": "0.000075",
        "median_absolute_deviation": null,
        "conflict": false,
        "is_fallback": false
      }
    }
  ],
  "quality": {
    "bars_returned": 365,
    "bars_with_conflict": 0,
    "fallback_bar_count": 0,
    "coverage": "1.000000"
  }
}
```

Phase 3 adds `open`, `high`, `low` to each bar alongside `close`, plus a per-field form block:

```json
{
  "fields": {
    "open":  { "form": "synthetic", "contributor_count": 2 },
    "high":  { "form": "synthetic", "contributor_count": 2 },
    "low":   { "form": "synthetic", "contributor_count": 2 },
    "close": { "form": "native",    "contributor_count": 2 }
  }
}
```

### Numeric encoding

Monetary values, rates, `spread`, `relative_spread`, and `coverage` are serialized as **strings** to preserve `Decimal` precision; counts are integers; `median_absolute_deviation` is a string or `null`. This is a deliberate deviation from routes that currently emit `float(...)` (for example `portfolio_management/totals.py:97`), chosen because fused output is a derived money value and must not lose precision at the boundary.

## API surface

```text
POST   /api/v1/entity-groups
GET    /api/v1/entity-groups
GET    /api/v1/entity-groups/{group_code}
PATCH  /api/v1/entity-groups/{group_code}
DELETE /api/v1/entity-groups/{group_code}

GET    /api/v1/entity-groups/{group_code}/members
PUT    /api/v1/entity-groups/{group_code}/members/{entity_id}
DELETE /api/v1/entity-groups/{group_code}/members/{entity_id}

GET    /api/v1/entity-groups/{group_code}/prices
GET    /api/v1/entity-groups/{group_code}/summary
```

Fused queries stay separate from `/api/v1/prices?entity_id=...`. An entity is a source-specific observation producer; a group is a derived fusion context.

### Query parameters for `/prices`

```text
membership=strong|broad              default strong
start=YYYY-MM-DD                     required, inclusive, group timezone
end=YYYY-MM-DD                       required, exclusive, group timezone
max_staleness_seconds=300            open-bucket freshness
fx_max_staleness_seconds=604800      FX freshness
default_bucket_gap_seconds=86400     fallback cadence for closed buckets
allow_single_point_bar=false         Phase 3 only
```

`mode` and `resolution` are absent because daily output is the only mode and granularity in scope; add them when a second value exists. `include_provenance` is absent because provenance is always required. `quote_currency` is absent because the group row owns the output currency.

`start`/`end` mirror the existing `/api/v1/prices` parameter names for the same concept (`routes/prices.py:20-31`). The format is `YYYY-MM-DD`, a deliberate simplification of that route's `YYYY-MM-DDTHH:MM:SS`, justified because fused output is daily. Both bounds are interpreted in `entity_group.timezone`, consistent with that route's "converted to the entity's timezone" behavior.

### PUT membership payload

`PUT` is a full replacement; there is no partial member update in this plan, which removes any omitted-versus-null ambiguity.

```json
{
  "membership_strength": "strong",
  "relationship_type": "same_reference",
  "confidence": "high",
  "enabled": true,
  "notes": null,
  "provenance": {}
}
```

`membership_strength` and `relationship_type` are required. `confidence` defaults to `medium`, `enabled` to `true`, `notes` to `null`, `provenance` to `{}`. Repeating the same request is idempotent.

### `/summary`

Mirrors `/api/v1/prices/summary` (`routes/prices.py:53-92`) over the fused daily series: `count`, `min`, `max`, `avg`, `std_dev`, `first_close`, `last_close`, `period_return_pct`, plus `bars_with_conflict`, `fallback_bar_count`, and `coverage`. It uses the same `start`/`end`/`membership` parameters as `/prices` and returns `404` for an unknown group and `422` for invalid bounds.

### Status and error mapping

| Condition | Status | Body |
|---|---|---|
| Unknown group code | 404 | `{"error": "group_not_found", "group": "<code>"}` |
| Unknown entity in membership write | 404 | `{"error": "entity_not_found", "entity_id": "<uuid>"}` |
| Duplicate group code | 409 | `{"error": "group_code_exists", "group": "<code>"}` |
| Bad date format, `start >= end`, or range over one year | 422 | `{"error": "invalid_range", "start": "...", "end": "...", "reason": "..."}` |
| Invalid enum value | 422 | `{"error": "invalid_value", "field": "...", "value": "..."}` |
| No FX path or stale/missing FX rate | 422 | `{"error": "fx_conversion_unavailable", "from": "USD", "to": "PHP", "reason": "no_path"\|"stale"\|"missing"}` |
| Nothing selected by the resolved membership | 422 | `{"error": "empty_membership", "group": "<code>", "membership": "strong"}` |
| No data for the range | 200 | Empty `bars` list with `coverage = "0"`; not an error |

Route handlers parse, delegate, and map exceptions only. They contain no fusion logic (`AGENTS.md` "Keep HTTP routing thin").

### Ordering

Group list by `code ASC`; member list by `entity.code ASC, datasource.name ASC`; bars by `date ASC`; contributors by `entity.code ASC, datasource.name ASC`; exclusions by `entity.code ASC`.

## Implementation phases

Each phase is independently verifiable.

### Phase 1: schema, group and membership management, FX resolution

- Add `entity_group` and `entity_group_member` to `schema.sql` as specified.
- Add `EntityGroupRecord`, `EntityGroupMemberRecord`, and `FxRate` to `libram_types/libram_types.py`.
- Add database methods for group CRUD, membership CRUD, and the bounded FX value-plus-timestamp lookup.
- Add `entity_group_management/membership.py`: pure inclusion/exclusion resolution with reason codes.
- Add `price_analysis/fx.py`: pure conversion with an injected lookup, direct and arithmetic-inverse, with the quantization rules above.
- Add `entity_group_management/service.py`, `models.py`, the `dependencies.py` provider, and `routes/entity_groups.py` for group and membership management, including ordering and status mapping.
- Validate `start`/`end` presence, format, ordering, and the one-year bound before any data is read.
- Verify and document that `entity.currency_id` identifies the member pricing currency, with `NULL` meaning PHP.
- Tests in `tests/unit/entity_group_management/` and `tests/unit/price_analysis/`: strong/broad filtering, weak exclusion in strong mode, disabled members, idempotent PUT, `NULL`-means-PHP output currency, direct and inverse conversion, rate `<= 0`, quantization, no-path and stale-rate failure, and range bounds. All pure or fake-backed; no PostgreSQL.

### Phase 2: daily close fusion and endpoints

- Add `price_analysis/fusion.py`: group-timezone bucketing, one contribution per entity per bucket, native-bar exact match, synthetic close from points, freshness, and equal-weight median of closes.
- Add `routes/entity_groups.py` `GET /prices` (close-only response shape) and `GET /summary`.
- Assemble contributors, exclusions, per-bar quality, and request-level coverage.
- Tests: per-entity deduplication, median across entities, bucket-relative freshness for open and closed buckets, `is_fallback`, empty-range behavior, conflict flagging, provenance assembly, and unchanged existing entity and raw-price behavior.

### Phase 3: daily OHLC fusion

- Extend reconciliation with finer-bar composition, OHLC medians, field-level forms, and the two-point synthetic-bar density rule.
- Add `open`/`high`/`low` to bars and `fields` to each bar; support `allow_single_point_bar`.
- Add cross-entity OHLC invariant assertions after source validation.
- Tests: mixed point and OHLC inputs, exact-tiling composition, partial overlap rejection, incomplete bars, single-point behavior with and without the flag, invariant handling, and field-level coverage.

## Risks

Risks not already expressed as decisions:

- **Alignment mismatch is a hard failure, not a silent shift.** A group whose declared timezone disagrees with its native daily bars will reject those members as `partial_day_coverage`. This is intentional and must surface with the reason code so the fix is obvious.
- **FX parse-time hazards.** `price_sources/ofx_forex_datasource.py:47-53` produces naive local timestamps and uncoerced numeric rates. The bounded lookup must not compare naive timestamps against UTC bucket boundaries, and the pair path must coerce to `Decimal`. Resolve this before Phase 1's FX task, not after.
- **Provenance payload size.** Per-bar contributors and exclusions over a 365-day range with several members is a large but bounded response. If it becomes a problem, the answer is a documented projection parameter, not moving provenance out of the response.
- **Reverse lookups.** Entity-to-group queries depend on `idx_entity_group_member_entity`; omitting it would make future membership screens do sequential scans.

## Decisions taken in v2

These were resolved by judgment during the review. They are deliberate and reversible; each is a scope or contract reduction rather than an addition.

| # | Decision | Rationale |
|---|---|---|
| 1 | Inverse conversion is arithmetic, not a stored reverse pair | A `PHP -> X` pair entity is unrepresentable while PHP is NULL |
| 2 | Freshness is bucket-relative with two thresholds | 300 s cannot express historical-bucket staleness |
| 3 | `entity_group.timezone` is a required column | A group has no single member timezone to inherit |
| 4 | Native and composed bars must exactly match the group day | Prevents silently fabricating or shifting daily detail |
| 5 | FX faults fail the request; incompatible or invalid members are rejected per member | v1 asserted both without separating the classes |
| 6 | Group row owns the output currency; the request parameter was removed | Avoids two defaults and undefined precedence |
| 7 | `relationship_type` is limited to `same_reference` and `wrapped` | The other three were unused and are a NOT NULL CHECK change later |
| 8 | `mode`, `resolution`, and `include_provenance` removed | Each had a single possible value |
| 9 | `start`/`end` and `YYYY-MM-DD` replace `date_from`/`date_to` | Matches the adjacent `/api/v1/prices` contract |
| 10 | Monetary values serialize as strings | Preserves `Decimal` precision at the boundary |
| 11 | PUT is a full replacement; no PATCH for members | Eliminates omitted-versus-null ambiguity |
| 12 | Phase 2 emits close only; Phase 3 adds OHLC | Gives each phase a coherent, testable output |
| 13 | `coverage` is days-with-data over requested days | Removes the entity-fraction alternative reading |
| 14 | `median_absolute_deviation` is `null` below three contributions | Avoids reporting a degenerate value |
| 15 | Membership timestamps added, membership history still deferred | Row lifecycle is cheap; an event log is not |

## Open questions before implementation

1. Is the exact-match day alignment of Decision 4 acceptable, or should label-based alignment across differing per-entity day conventions be pulled into scope? (Currently deferred.)
2. Is `default_bucket_gap_seconds = 86400` the right fallback for a `CONTINUOUS` entity with no `snapshot_state` row?
3. Is reading FX pairs from `type = 'CURRENCY'` plus `config.base`/`config.currency` acceptable, or should the pair become first-class columns? The current shape is OFX-specific.
4. `quote_currency` was removed as a request parameter. Is per-request override wanted after all?

## Recommended initial BTC example

```text
Group: btc
Timezone: UTC
Output currency: NULL (PHP)

Range: start 2026-01-01, end 2027-01-01

Members:
  BTC  / coindesk   strong / same_reference / high confidence
  BTC  / chainlink  strong / same_reference / high confidence
  WBTC / ethereum   weak   / wrapped        / high confidence
```

Expected behavior:

```text
membership=strong -> BTC/coindesk + BTC/chainlink
membership=broad  -> BTC/coindesk + BTC/chainlink + WBTC/ethereum
```

The broad result must identify itself as a BTC-related composite rather than presenting itself as a pure BTC reference price. Note that with two strong members the median equals the mean (Decision 10), so `relative_spread` and the contributor list carry the disagreement signal, not the aggregation method.

## Summary

```text
entity               = source-specific price producer
entity_group         = named fusion context with a calendar and output currency
entity_group_member  = explicit membership, relationship, provenance, membership confidence
price                = raw source observation
fused result         = query-time derived observation with contributor, exclusion, and quality metadata
```

Core invariant:

> An entity participates in a fusion only because an explicit group-member relation admits it. Aggregation determines how admitted observations are combined; it does not determine membership.

## Verification expectations

When implementation begins:

```bash
uv run pytest tests/unit -q
uv run pytest tests/unit/entity_group_management -q
uv run pytest tests/unit/price_analysis -q
uv run ruff check .
uv run ruff format --check .
git diff --check
```

Focused tests must cover:

- group and membership CRUD, idempotent PUT, and deterministic ordering;
- strong versus broad resolution, disabled members, and explicit weak-member exclusion from strong fusion;
- `NULL`-means-PHP output currency and member pricing-currency resolution;
- direct and inverse FX conversion, rate `<= 0`, quantization, no-path and stale-rate failure;
- range validation: format, ordering, and the one-year bound;
- group-timezone bucketing, including a non-UTC group timezone;
- per-entity deduplication and one contribution per entity per bucket;
- equal-weight median, contributor counts, `spread`, `relative_spread`, `null` MAD below three contributors;
- bucket-relative freshness for open and closed buckets;
- exact-match and partial-overlap bar handling;
- provenance assembly and exclusion reason codes;
- unchanged existing entity and raw-price behavior.

Unit tests must remain deterministic and must not require PostgreSQL, Docker, live network access, or scheduler threads (`AGENTS.md` "Testing"). Database and route behavior belongs in an explicit `tests/integration/` boundary when that work begins.

No implementation, migration, or seed-data changes ship with this document. It is a design artifact only; Phase 1 is the first change to `schema.sql`.

## References to current repository structure

- `schema.sql` — idempotent PostgreSQL DDL; `price`, `entity`, `snapshot_state`.
- `data.sql` — seed datasources and entities, including the `USD` currency pair.
- `AGENTS.md` — architecture, conventions, testing boundaries, module placement.
- `libram_database/db.py` — SQLAlchemy Core operations; `get_price_at_or_before()` at line 1185.
- `libram_types/libram_types.py` — core dataclasses.
- `price_management/service.py` — price fetch/store/query orchestration.
- `price_analysis/date_utils.py` — timezone conversion helper.
- `price_analysis/` — pure calculation modules.
- `portfolio_management/dividend_calculation.py` — existing injected FX-lookup pattern to generalize.
- `price_sources/ofx_forex_datasource.py` — stored FX pair source.
- `routes/entities.py`, `routes/prices.py` — existing entity and raw price endpoints.
- `tests/unit/price_analysis/`, `tests/unit/price_management/` — existing pure and service test patterns.
