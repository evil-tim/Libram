# Fused Entity Data Plan

Status: Architecture locked; implementation pending.

Depends on `docs/currency_conversion_plan.md` for Phase 2 only.

Supersedes `docs/archive/fused_entity_data_plan_v2.md` (and the v1 it replaced). Those two drafts solved a problem larger than the one we have. This plan keeps their model and deletes the rest.

## Context

Libram stores source-specific entities and their raw observations:

```text
entity -> datasource
entity -> price rows (point: price+timestamp | interval: open/high/low/close+start/end)
```

The same reference is reachable through several entities. Bitcoin exists four times today:

| Entity | Datasource | Denominated in | Shape |
|---|---|---|---|
| `BTC` | `coindesk-ohlc-json` | PHP (`currency_id` NULL) | DAILY OHLC bars |
| `BTC` | `kraken-ticker-rest-json` | USD | CONTINUOUS point rows |
| `BTC` | `chainlink-ethereum` | USD | CONTINUOUS point rows |
| `WBTC` | `web3-uniswap-arbitrum` | USDC | CONTINUOUS point rows |

A single source can go missing or carry an outlier-influenced print. Libram should group these entities and expose one combined price feed.

The feature needs one relationship:

```text
entity -> entity_group_member -> entity_group
```

## Scope

In scope:

- CRUD for named entity groups and their members;
- classifying a member as `strong` or `weak`;
- a fused daily close series derived from a group's members at query time;
- an output currency per group, defaulting to PHP;
- converting member values into that currency by delegating to the currency conversion module;
- returning the contributing sources with each day.

Out of scope for this MVP:

- OHLC output; fused output is one value per day;
- multi-hop conversion, competing FX sources, and rate freshness thresholds (all owned by the currency conversion module);
- relationship subtypes (`wrapped`, `tracking`, ...), membership confidence, membership provenance, member weights;
- historical membership, group-to-group relationships, automatic or fuzzy matching of entities;
- persisted fused rows;
- itemizing, per day, why an individual source was absent.

### What this drops from v2

| v2 | Replaced by |
|---|---|
| OHLC fusion: native/composed/synthetic forms, field-level medians, invariant checks, point-density rules, `allow_single_point_bar` | one value per day (Decision 4) |
| `entity_group.timezone` and the day-alignment mismatch failure class | UTC calendar days (Decision 6) |
| `relationship_type`, `confidence`, `provenance`, `notes` on the member row | one `strength` column (Decision 2) |
| FX pair discovery by parsing `entity.config`, a new bounded rate lookup with `FxRate` provenance, staleness windows, rate quantization | a standalone currency conversion module, `docs/currency_conversion_plan.md`, which this plan consumes (Decision 7) |
| Per-bar `spread`, `relative_spread`, `median_absolute_deviation`, `conflict`, `is_fallback`, `coverage`, per-bar `excluded` list | the `sources` list on each day (Decision 10) |
| `GET /summary`, `enabled` soft-disable, per-endpoint ordering rules, the expanded status-mapping table | omitted; the endpoint set and error surface above are what the MVP needs |
| Three phases | two |

## Dependencies

| Dependency | Status |
|---|---|
| `docs/currency_conversion_plan.md` | Required by Phase 2. Owns currency resolution, single-hop arithmetic, precision, rate lookup, and the typed conversion failures. |
| The group's output currency | `entity_group.quote_currency_id`, interpreted by that module. `NULL` means PHP. |

This feature adds no conversion logic of its own. The module is a general capability: its other consumer is an output-currency option on `GET /api/v1/prices`, with portfolios, dividend fees, and cross-currency comparison as likely later ones. Only two conversion rules are specific to this plan — the group owns the output currency, and a conversion failure fails the request.

## Decisions

### 1. Sources are untouched

`entity` stays source-specific and `price` stays raw observations. No merging, no symbol matching, no identity inference. A group is a derived read model over existing rows.

### 2. Membership is one row per (group, entity) with a strength

```text
strong -> the actual reference       (BTC on a major exchange, BTC from an oracle)
weak   -> a derivative or wrapper    (WBTC)
```

That is the entire relationship record. A `weak` row already asserts "related, not interchangeable"; nothing in the fused computation consumes a finer distinction. Subtypes can be added later with `ALTER TABLE ... ADD COLUMN` when a consumer needs them.

There is no `enabled` flag: removing a membership is how you stop using a source.

### 3. Selection is `membership=strong|all`, default `strong`

```text
strong -> members where strength = 'strong'
all    -> members where strength IN ('strong', 'weak')
```

Strong selection is closed-world. Members are admitted only by an explicit row, never by matching codes or names, related-entity inference, or similar price behaviour. Defaulting to `strong` means a fused BTC series is a pure BTC series unless the caller asks otherwise.

### 4. Fused output is one value per UTC calendar day

Not OHLC. `close` is the one quantity every source has — a point row's `price` and a bar's `close` are the same field. Fusing OHLC would mean four parallel reconciliations plus interval-coverage rules for a feature whose stated purpose is "one reliable price". Deferred until the close-only feed is in use.

### 5. Each member contributes at most one value per day: its last observation of that day

```text
effective time = COALESCE(timestamp, timestamp_start)
value          = COALESCE(close, price)
```

This is already the repository's notion of "the price of a day" — `db.get_price_at_or_before()`, `db.query_close_series()`, and `query_price_summary()`'s `last_close` all use exactly these expressions. Point rows, daily bars, and sub-daily bars therefore all reduce to one scalar with no composition, interpolation, or forward-fill. A CONTINUOUS source's daily value is its final observation of that day, which is what "current price at close" means for a snapshot feed.

### 6. Bucketing is UTC and the group has no timezone

A day `D` is `[D 00:00Z, D+1 00:00Z)`. Every entity that can be fused today is UTC-observed (`Etc/Universal` in seed data), so this is not a compromise for the current use case.

Known limitation, accepted: a daily bar aligned to another calendar (a `Asia/Manila` bar starts 16:00Z the previous day) buckets by its UTC date and lands one day early. Modelling per-group timezones and alignment rejection is where v2 grew, and there is no consumer for it yet.

### 7. Conversion is delegated to a standalone currency conversion module

`entity_group.quote_currency_id` names the group's output currency, and `NULL` means PHP. Everything about *how* a value crosses currencies lives in `docs/currency_conversion_plan.md`: path resolution from the `currency_id` chain, direct and inverse single-hop arithmetic, 6-decimal precision, rate lookup, and the typed failure set.

This plan consumes a narrow slice of that interface:

```python
same_currency = member.currency_id == group.quote_currency_id
path = None if same_currency else fx.resolve(member.currency_id, group.quote_currency_id)
# same_currency        -> no conversion needed
# path is None here    -> no single-hop route -> fail (Decision 8)
rates = fx.rate_series(path.rate_entity_id, start, end)   # one query per rate entity
value = conversion.convert(member_day_value, path, rates[day])
```

Note the two distinct ``None`` cases: `resolve()` returns `None` for identity as well as for an unreachable pair, so a caller must compare the currency ids before treating `None` as a failure.

Only the fusion-specific slice stays here: the group owns the output currency, every selected member is converted before aggregation, and a converted contribution is reported as converted (Decision 10).

Splitting this out is not just tidiness. Conversion is a general capability — portfolios, dividend fees, and cross-currency comparison all need it — and carrying it inline is part of what made the previous two iterations large. What remains here is one subject: reducing several sources of the same quantity into one daily value.

### 8. Conversion failures fail the request; they never shrink the result

The module reports a typed failure; this plan decides what it means. Any selected member that holds a value for a requested day must be convertible for that day. If it has no single-hop path to the group's currency, or no usable rate exists at or before that day's end, the fused request fails with an error naming the member and the day. No partial series and no quietly dropped member.

Note the deliberate asymmetry: a member with **no observation** on a day is simply absent from that day's contributors — aggregating around missing source data is the feature's premise. A member with an observation that cannot be **converted** fails the request. Missing data is expected; an unconvertible value is a contract violation.

The policy lives here rather than in the module because another consumer may reasonably choose the opposite — skip the item and return what it has — and the module must not pre-empt that choice.

Consequence worth knowing: `membership=all` selects weak members, so a group whose weak member has no single-hop path fails in `all` mode while succeeding in `strong` mode.

### 9. Aggregation is an equal-weight median of contributing values, per day

The simple robust choice for independent reference feeds: with three or more contributors one bad print does not move the result. With exactly two contributors the median *is* the mean and gives no outlier protection — the `sources` list is the disclosure, not the aggregation method.

Days with no contributing member are omitted from the response.

### 10. The response names its contributors

A fused day is never presented as if every member contributed. Every day carries the list of sources that actually did, with each source's value and observation time. This is the one piece of provenance that is not optional: it is what lets a caller see that a "BTC price" came from one feed rather than three.

### 11. Fusion happens at query time

Fused values are never written to `price`. That table holds source observations; a fused value is derived from several.

## Schema

Idempotent DDL in `schema.sql`, matching existing conventions. PostgreSQL 18 supplies `uuidv4()`.

```sql
-- entity_group
-- A named fusion context: one top-level entity assembled from several source entities.
CREATE TABLE IF NOT EXISTS entity_group (
    id uuid PRIMARY KEY DEFAULT uuidv4(),
    code text NOT NULL UNIQUE,
    name text NOT NULL,
    description text,
    -- Output currency for fused values. NULL means PHP, matching entity.currency_id.
    quote_currency_id uuid REFERENCES entity(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- entity_group_member
-- Explicit membership of one source entity in one group.
CREATE TABLE IF NOT EXISTS entity_group_member (
    group_id uuid NOT NULL REFERENCES entity_group(id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    -- strong: the actual reference. weak: derivative or wrapped representation.
    strength text NOT NULL CHECK (strength IN ('strong', 'weak')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_group_member_entity
    ON entity_group_member (entity_id);
```

Notes:

- `quote_currency_id` uses `ON DELETE RESTRICT`: a currency entity that is a group's output currency cannot be deleted silently.
- `PRIMARY KEY (group_id, entity_id)` serves group-first lookups; the index keeps the entity-delete cascade from scanning the membership table.
- No aggregation config, no weights, no priorities, no validity ranges.

### Membership example

```text
Group: btc        "Bitcoin reference"        output currency: NULL (PHP)

Entity   Datasource                Strength
BTC      coindesk-ohlc-json         strong
BTC      kraken-ticker-rest-json    strong
BTC      chainlink-ethereum         strong
WBTC     web3-uniswap-arbitrum      weak
```

## Read path

### Fetching

Each member's daily series is reduced in PostgreSQL by the shared read `Database.query_daily_last_price(entity_id, start, end)`, specified in the Currency Conversion Plan (`docs/currency_conversion_plan.md`, Decision 8). It returns at most one `DailyPrice(day, observed_at, value)` per UTC calendar day, so a year of 5-minute snapshots returns at most 365 rows and the existing paginated `query_prices()` is not involved. Values are `Decimal`.

Both plans need that read — the currency module for rates, this plan for member values — so it is specified once, there, and reused here.

`start`/`end` are `YYYY-MM-DD` and become UTC midnights, so unlike `routes/prices.py` there is no per-entity timezone conversion on this path.

### Fusing

`price_analysis/fusion.py` is pure — no database, no FastAPI — and receives conversion as an injected callable, so the currency module stays on the other side of the boundary:

```python
select_members(members, mode) -> (included, excluded)
fuse_daily(
    series: list[MemberSeries],          # member + its DailyPrice list
    start: date, end: date,
    convert: Callable[[MemberSeries, Decimal, date], Decimal],
) -> list[FusedBar]
```

The service builds `convert` from the group's currency and the currency conversion module: one `FxPath` per selected member resolved up front, and one day-keyed rate series per distinct rate entity for the whole range. `fusion.py` never resolves a path and never reads a rate; its tests pass a literal function.

Per requested day, in order:

1. collect each included member's value for that day (already reduced — at most one each);
2. a selected member that has a value for that day but no path, or no rate for that day: **fail the request** (Decision 8);
3. a member with no observation that day is skipped — that is the missing data this feature exists to smooth over;
4. if nothing remains, emit no bar; otherwise emit the median of the converted values.

A rate is resolved once per rate entity per UTC day and reused by every contributor to that bar, so a year-long range makes at most one rate query per distinct pair.

That is the whole algorithm. It exists as one small function so it can be tested with literal inputs.

### Response

```json
{
  "group": { "code": "btc", "name": "Bitcoin reference", "membership": "strong", "currency": "PHP" },
  "range": { "start": "2026-01-01", "end": "2026-01-03" },
  "members": [
    { "entity_code": "BTC", "datasource": "coindesk-ohlc-json", "strength": "strong", "status": "included" },
    { "entity_code": "BTC", "datasource": "kraken-ticker-rest-json", "strength": "strong", "status": "included" },
    { "entity_code": "BTC", "datasource": "chainlink-ethereum", "strength": "strong", "status": "included" },
    { "entity_code": "WBTC", "datasource": "web3-uniswap-arbitrum", "strength": "weak", "status": "excluded",
      "reason": "weak_member_excluded_by_strong_mode" }
  ],
  "bars": [
    {
      "date": "2026-01-01",
      "value": "68418.716667",
      "sources": [
        { "entity_code": "BTC", "datasource": "coindesk-ohlc-json", "value": "68420.000000",
          "observed_at": "2026-01-01T00:00:00Z", "converted_from": null },
        { "entity_code": "BTC", "datasource": "kraken-ticker-rest-json", "value": "68411.000000",
          "observed_at": "2026-01-01T23:59:00Z",
          "converted_from": { "currency": "USD", "value": "1200.19", "rate": "57.000000", "direction": "direct" } },
        { "entity_code": "BTC", "datasource": "chainlink-ethereum", "value": "68425.150000",
          "observed_at": "2026-01-01T23:58:00Z",
          "converted_from": { "currency": "USD", "value": "1200.44", "rate": "57.000000", "direction": "direct" } }
      ]
    }
  ]
}
```

- `value` is in the group's output currency, in both `bars` and `sources`. `converted_from` projects the arithmetic the currency module applied — raw value, its currency, the stored rate, and the direction it was applied in (`direct` multiplies, `inverse` divides) — so a converted contribution is never indistinguishable from a directly observed one and its arithmetic is reproducible. It is `null` when no conversion applied.
- `group.currency` is the output currency code, `PHP` when `quote_currency_id` is `NULL`.
- `sources` is ordered by `entity_code`, then `datasource`. It *is* the contributor count.
- `members` is a request-level rollup of membership resolution: what was admitted, what was not, and the single reason code that matters (`weak_member_excluded_by_strong_mode`).
- Monetary values serialize as strings to preserve `Decimal` precision.
- Because entity codes are unique only per datasource (`entity UNIQUE (datasource_id, code)`), every reference pairs `entity_code` with `datasource`.

Deliberate omission: the response does not say *why* a member is absent on a particular day (no observation vs. no FX rate). It shows that it is absent. Itemizing per-day absence is where v2's quality block started; add it when a consumer asks.

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
```

Group bodies are `{code, name, description, quote_currency_id}`; `quote_currency_id` is optional and `null` means PHP. `PATCH` accepts `name`, `description`, and `quote_currency_id`. The member `PUT` body is `{"strength": "strong" | "weak"}` and is a full, idempotent upsert.

CRUD responses add the server-owned fields: a group returns `{id, code, name, description, quote_currency_id, created_at, updated_at}` and a member returns `{entity_id, entity_code, datasource, strength, created_at}`. The output currency is reported as `quote_currency_id` alone — a `currency` label would need the currency module, which Phase 1 deliberately does not depend on; the fused response adds the code in Phase 2.

`code` and `name` are stripped and must be non-blank (422 `invalid_value` otherwise). `description` and `quote_currency_id` accept an explicit `null` — clearing them is meaningful — but `name` does not, because its column is `NOT NULL`; a null `name` is a client error, not a way to clear a field. Deletes answer with `{"deleted": true, ...}` and do not return the removed row.

Changing a group's `quote_currency_id` invalidates nothing on write — members are validated against it when a fused series is requested (Decision 8).

`/prices` parameters:

```text
start=YYYY-MM-DD                required, inclusive
end=YYYY-MM-DD                  required, exclusive
membership=strong|all           default strong
```

Fused queries stay separate from `/api/v1/prices?entity_id=...`: an entity produces observations, a group derives them.

Two things about these bodies. First, they appear under FastAPI's `detail` key, like every other route in this service: the response is `{"detail": {...}}`, where `{...}` is the object in the Body column. Second, the table covers *domain* checks only — a schema-level violation (a missing required field, a malformed UUID) is FastAPI's own 422 with its standard validation list, which this feature does not reshape.

| Condition | Status | Body |
|---|---|---|
| Unknown group code | 404 | `{"error": "group_not_found", "group": "<code>"}` |
| Unknown entity id (`quote_currency_id` or member write) | 404 | `{"error": "entity_not_found", "entity_id": "<uuid>"}` |
| Removing an entity that is not a member | 404 | `{"error": "member_not_found", "group": "<code>", "entity_id": "<uuid>"}` |
| Duplicate group code | 409 | `{"error": "group_code_exists", "group": "<code>"}` |
| A selected member has no single-hop conversion path | 422 | `{"error": "fx_no_path", "entity_code": "...", "datasource": "...", "from": "USDC", "to": "USD"}` |
| A selected member's rate is missing or `<= 0` for a requested day | 422 | `{"error": "fx_rate_unavailable", "entity_code": "...", "datasource": "...", "pair": "USD", "date": "YYYY-MM-DD"}` |
| Missing/malformed `start` or `end`, `start >= end`, range over 366 days | 422 | `{"error": "invalid_range", "start": "...", "end": "...", "reason": "..."}` |
| Invalid `strength` or `membership` value | 422 | `{"error": "invalid_value", "field": "...", "value": "...", "reason": "..."}` |
| Nothing admitted by the resolved membership | 422 | `{"error": "empty_membership", "group": "<code>", "membership": "strong"}` |
| No day has a contributing member | 200 | empty `bars` list; not an error |

The 366-day cap exists because these endpoints are exposed as MCP tools; it is validated before any price or rate row is read. Validating, not truncating.

## Layering

Following `AGENTS.md`:

| File | Responsibility |
|---|---|
| `routes/entity_groups.py` | HTTP only: parse, delegate, map domain errors |
| `entity_group_management/models.py` | Pydantic request models |
| `entity_group_management/service.py` | group and membership CRUD; assembling a fusion request |
| `price_analysis/fusion.py` | pure membership selection and daily median over injected conversions |
| `libram_database/db.py` | group and member SQL |
| `libram_types/libram_types.py` | `EntityGroupRecord`, `EntityGroupMemberRecord` |
| `dependencies.py` | `get_entity_group_service` provider |
| `server.py` | include the router |
| `schema.sql` | DDL above |
| `currency_conversion/` | dependency, not modified here — provided by `docs/currency_conversion_plan.md` |

`entity_group_management/` mirrors `fundamentals_management/`. Route handlers construct nothing.

## Implementation phases

Each phase is independently verifiable.

### Phase 1: groups and membership

- Add both tables to `schema.sql`; add the three dataclasses.
- Add group CRUD and membership upsert/delete/list to `db.py`.
- Add `entity_group_management/`, `routes/entity_groups.py` (CRUD endpoints), the provider, and the router registration.
- Tests (`tests/unit/entity_group_management/`, fake database): create/read/update/delete, duplicate code, unknown `quote_currency_id` or member entity, idempotent member upsert, `strength` validation, and `NULL`-means-PHP output currency.

### Phase 2: fused daily series

Requires the currency conversion module to exist. Phase 1 does not.

- Add `price_analysis/fusion.py`: membership selection, injected conversion, and the daily median.
- Add `get_fused_prices()` to the service: resolve one `FxPath` per selected member, load one rate series per distinct rate entity, then hand a `convert` callable to `fusion.py`.
- Add `GET /{group_code}/prices`.
- Tests (`tests/unit/price_analysis/test_fusion.py`, literal inputs): strong vs. all selection, weak exclusion under `strong`, a member already in the group's currency used unconverted, one injected conversion applied per member-day, no-path failure, missing-rate failure, a member skipped when it has no observation that day, median of three, two-contributor median, empty range, and `empty_membership`.

Path resolution, conversion arithmetic, quantization, and rate lookup are tested in the currency module's own suite, not here. `query_daily_last_price()` is SQL and needs PostgreSQL; per `AGENTS.md` it belongs in `tests/integration/`, not the deterministic unit suite.

## Risks

- **A single missing rate fails a whole range.** Decision 8 is deliberately strict. If that proves brittle on sparse FX data, the alternative is to skip just that member-day — a one-line change, but one that trades integrity for coverage.
- **Currency topology decides whether a group is viable, and the failures are unintuitive.** Every selected member must be one hop from the group's currency, so `USDC`-denominated members cannot join a `USD` group and a depth-2 currency cannot join a PHP group; both fail as two-hop paths. The seed currency graph in the currency module's plan is what to read before concluding a group is broken.
- **UTC bucketing is silent for non-UTC sources.** Stated in Decision 6. A group whose native bars are Manila-aligned will be off by one day rather than rejected. Do not fuse such an entity until group timezones exist.
- **One contributor looks like fusion.** A day built from a single source is returned normally. `sources` makes it visible; there is no minimum-contributor rule.
- **Aggregation is one line of math.** `statistics.median` on `Decimal` values; no weighting, no method selection, no configuration.

## Open decisions

Resolved during review:

- **Output currency** is a group property (`quote_currency_id`, `NULL` = PHP) with single-hop conversion in both directions; multi-hop fails rather than degrading (Decisions 7 and 8).
- **Per-day absence reasons** are out of scope; the response shows that a source is absent, not why.
- **Weak-member weighting** is out of scope; weak members count the same as strong ones under `membership=all`.

Nothing is blocking implementation. The one thing to watch is the strictness of Decision 8 on sparse FX data.

## Verification

```bash
uv run pytest tests/unit -q
uv run pytest tests/unit/price_analysis -q
uv run pytest tests/integration -q
uv run ruff check price_analysis routes/entity_groups.py entity_group_management
uv run ruff format --check price_analysis entity_group_management
git diff --check
```

Repo-wide `uv run ruff check .` and `uv run ruff format --check .` are not usable as gates: `main` already carries 318 lint findings and 41 unformatted files, and `ruff check .` also walks `.worktrees/`. Lint the paths a change touches. The Currency Conversion Plan's Verification section carries the same note.

Phase 2 additionally requires the currency conversion module's suite to pass; this plan does not re-test conversion arithmetic.

Confirm existing entity and raw-price behaviour is unchanged: `tests/unit/price_analysis/test_comparison.py`, `tests/unit/price_management/`, and `routes/prices.py` are not touched by this feature.

No implementation, migration, or seed changes ship with this document. Phase 1 is the first change to `schema.sql`.

## Repository references

- `docs/currency_conversion_plan.md` — the standalone module this plan depends on for Phase 2.
- `schema.sql` — `entity` (line 19), `price` (line 40), `snapshot_state` (line 180).
- `data.sql` — the four BTC/WBTC entities the worked example uses.
- `AGENTS.md` — architecture, module placement, testing boundaries, quality gates.
- `libram_database/db.py` — `query_prices()` (198), `query_close_series()` (536), `query_price_summary()` (571).
- `price_management/service.py` — `query_prices()`, `query_close_series()`, `query_price_summary()`.
- `price_analysis/comparison.py` — the existing multi-entity read pattern.
- `routes/prices.py` — the raw price contract this feature stays separate from.
