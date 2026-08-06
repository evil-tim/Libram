# Plan v4: Dividend Events and Portfolio Dividend Totals

## Context

Libram is an ad-hoc personal finance tracker with portfolio orders and average-cost totals. It currently reports realized gains, unrealized gains, current value, cost basis, and ordinary order fees.

Add a lightweight dividend feature that:

- Stores dividend events for entities.
- Derives eligible shares from portfolio orders.
- Calculates a gross dividend gain.
- Records user-supplied dividend-related fees, which may include withholding tax, broker fees, or other deductions.
- Supports dividend amounts denominated in currencies other than PHP.
- Records user-supplied total dividend fees per portfolio/event without modeling their source.
- Includes dividend gain and dividend fees in aggregate and per-entity totals.

This is intentionally not an accounting or broker-reconciliation system. Do not add provenance, event status, receipt records, tax-rate policy, or tax-law-specific machinery in this iteration.

## Design decisions

### Dividend event fields

Create a `dividend_event` table with:

- `id`
- `entity_id`
- `declaration_date` (nullable)
- `ex_date` (required)
- `record_date` (nullable)
- `payment_date` (nullable)
- `dividend_type`
- `amount_per_share`
- `amount_per_share_entity_id` (nullable; NULL means PHP)
- `created_at`
- `updated_at`

`amount_per_share_entity_id` follows the same convention as
`portfolio_order.cost_basis_entity_id`:

- `NULL` means the amount is denominated in PHP.
- A non-NULL value references a currency entity used to convert the dividend
  amount to PHP.

The API should expose the corresponding currency entity by code, for example
`amount_per_share_entity_code`, while the database and dataclass use the UUID
field `amount_per_share_entity_id`.

Suggested dividend types:

- `regular`
- `special`
- `return_of_capital`

The type is initially informational. Do not introduce special calculation behavior for `return_of_capital` unless a later requirement calls for it.

### Portfolio-specific dividend fees

Do not model tax rates, fee sources, or fee categories. The user can
provide the total fees that actually applied to a dividend for each portfolio.

Add a `portfolio_dividend` table:

```sql
CREATE TABLE portfolio_dividend (
    id uuid PRIMARY KEY DEFAULT uuidv4(),
    portfolio_id uuid NOT NULL REFERENCES portfolio(id) ON DELETE CASCADE,
    dividend_event_id uuid NOT NULL REFERENCES dividend_event(id) ON DELETE CASCADE,
    fees numeric NOT NULL DEFAULT 0 CHECK (fees >= 0),
    fees_entity_id uuid REFERENCES entity(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (portfolio_id, dividend_event_id)
);
```

`fees` is the total fee amount applied to that portfolio for that dividend
event. It may include withholding tax, broker fees, processing fees, or any
other deductions. Libram does not need to know the source.

`fees_entity_id` follows the existing `portfolio_order.fees_entity_id`
convention:

- `NULL` means PHP.
- A non-NULL value references the currency entity used for the fee amount.

The event itself has no fees or tax fields. The portfolio/event row is required
because different portfolios may have different fee amounts for the same event.

### Calculation semantics

For each dividend event and portfolio:

```text
eligible shares = shares held immediately before ex_date
dividend gain = eligible shares × amount_per_share
dividend gain (PHP) = convert dividend gain using the event currency entity
dividend fees (PHP) = convert portfolio_dividend.fees using the fee currency entity
```

Replay orders chronologically. Apply only orders with `order.date < dividend_event.ex_date`; a buy on the ex-date is not eligible under this simplified rule.

Dividend fees must remain separate from existing order fees and realized gains:

- `total_fees` remains trading/order fees.
- `total_dividend_gain` is gross dividend income.
- `total_dividend_fees` is the supplied total fee amount associated with dividend events.
- Do not fold dividend fees into `realized_gain`.
- Net dividend amounts are out of scope.

## Implementation tasks

### Task 1: Add the database schema

**Files:** `schema.sql`

Add the `dividend_event` table and an index on `(entity_id, ex_date)`.

Use the existing SQLAlchemy Core/PostgreSQL conventions and idempotent DDL style (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).

Add `amount_per_share_entity_id uuid REFERENCES entity(id)` to
`dividend_event`. A NULL value means PHP, matching the existing
`portfolio_order.cost_basis_entity_id` convention.

Add the `portfolio_dividend` table with its fee currency reference and a unique constraint on `(portfolio_id, dividend_event_id)`.

This iteration adds only new idempotent tables and indexes to `schema.sql`. Applying schema changes to an existing deployment is a separate operational step; do not introduce a migration framework as part of this feature.

**Verify:** Review the generated SQL and run schema initialization against a disposable/test PostgreSQL database if available.

### Task 2: Add dividend dataclasses and request models

**Files:**

- `libram_types/libram_types.py`
- `portfolio_management/dividend.py`
- `portfolio_management/dividend_fees.py`

Add `DividendEventRecord` with the database fields and nullable date fields represented consistently with the existing dataclasses.

Add Pydantic request models for create/update operations in the focused domain modules. Use `Decimal` for monetary values and precise date/datetime types rather than introducing additional float/string parsing for this feature. Suggested API fields:

```text
entity_code
ex_date
declaration_date
record_date
payment_date
dividend_type
amount_per_share
```

The create model should require `entity_code`, `ex_date`, and `amount_per_share`. The update model should make mutable fields optional.

`amount_per_share_entity_code` is optional. When omitted or NULL, the
amount-per-share currency is PHP. When provided, resolve it to a currency
entity UUID in the domain/service layer, just as order cost-basis currency is
resolved for `portfolio_order`.

Validate:

- `amount_per_share >= 0`
- `amount_per_share_entity_code`, when provided, resolves to an existing entity
- `dividend_type` is one of the supported values
- dates are valid ISO 8601 values

The database should also enforce `amount_per_share >= 0`. Either enforce the supported dividend types in the database or explicitly keep that validation at the application boundary.

### Task 3: Add database CRUD/query methods

**Files:** `libram_database/db.py`

Add methods for:

- inserting a dividend event
- retrieving an event by ID
- updating an event
- deleting an event
- listing/filtering events by entity and ex-date range
- loading all relevant events for totals calculation

Add a `_row_to_dividend_event` converter consistent with `_row_to_order` and the other database converters.

Resolve `entity_code` to `entity_id` in the domain/service layer, not in the database layer.

Resolve `amount_per_share_entity_code` to `amount_per_share_entity_id` in the
domain/service layer. The database layer should receive only UUIDs, matching
the existing order currency fields.

Include deterministic ordering for list queries, preferably `ex_date ASC, id ASC`.

Keep database methods limited to persistence and record conversion. Domain services resolve entity and currency codes before calling them.

### Task 4: Add portfolio dividend fee service

**Files:**

- `portfolio_management/dividend_fees.py`
- `libram_database/db.py`

Add create/read/update/delete support for `portfolio_dividend` records. Accept `fees` and optional
`fees_entity_code` from the API, resolving the currency code to
`fees_entity_id` in the domain/service layer, matching order fee handling.

Scope every operation by both `portfolio_id` and `dividend_event_id`. Return 404 for a missing portfolio, dividend event, or portfolio/event fee association. Define whether an explicitly null `fees_entity_code` clears the stored currency and distinguish that from an omitted update field.

The unique constraint on `(portfolio_id, dividend_event_id)` supplies the normal fee lookup index.

### Task 5: Implement dividend calculations in a focused calculation module

**Files:**

- `portfolio_management/dividend_calculation.py`

Add a pure calculation function that receives already-loaded orders, one event, the matching optional portfolio/event fee record, and an FX lookup callback. Keep database access and response formatting outside this function.

For each portfolio/entity combination:

1. Replay orders in chronological order using `date ASC, id ASC`.
2. Apply only orders with `order.date < dividend_event.ex_date`; this excludes both buys and sells occurring on the ex-date.
3. Compute eligible shares.
4. Compute gross dividend gain.
5. Convert gross dividend gain to PHP using the event’s
   `amount_per_share_entity_id` and the existing price lookup/FX convention.
6. Load the matching `portfolio_dividend` row, if present.
7. Convert its supplied `fees` to PHP using `fees_entity_id` and the same FX
   date convention.
8. Accumulate PHP gross gain and dividend fees separately.

For all-portfolios totals, calculate each portfolio independently using only its own orders and fee rows, then sum the portfolio results. Never replay orders from different portfolios as one position.

Add these fields to aggregate totals:

```text
total_dividend_gain
total_dividend_fees
```

Add these fields to each by-entity result:

```text
dividend_gain
dividend_fees
```

Keep the aggregate output in PHP. A NULL currency ID is a no-op PHP conversion;
a non-NULL value must use the existing FX price lookup behavior, with the event
payment date when available and otherwise the ex-date as the conversion date.

Calculate gross amounts only. Do not calculate or expose net dividends.

Use `Decimal` arithmetic, matching the existing average-cost implementation. Convert to JSON-compatible numeric values only at the response boundary.

The implementation must support these behaviors:

- buy before ex-date receives the dividend
- buy on ex-date does not receive the dividend
- sell before ex-date reduces eligible shares
- no holdings produces zero dividend values
- supplied portfolio/event fees accumulate separately from gross dividend gain
- zero supplied fees produce zero dividend fees
- separate portfolios can use different fee amounts for the same dividend event
- a NULL amount currency is treated as PHP
- a foreign-currency amount is converted to PHP using the event FX entity
- foreign-currency portfolio fees are converted to PHP using the fee FX entity
- a missing FX rate raises the existing portfolio validation error
- multiple dividend events accumulate correctly
- multiple entities aggregate correctly
- portfolio-scoped totals do not include events from other portfolios
- all-portfolios totals equal the sum of independently calculated portfolios
- separate portfolios holding different quantities remain isolated

### Task 6: Add dividend event REST/MCP endpoints

**Files:**

- `server.py`
- `portfolio_management/client.py`
- `portfolio_management/dividend.py`

Add minimal CRUD/list routes:

```text
POST   /api/v1/dividends
GET    /api/v1/dividends
PUT    /api/v1/dividends/{dividend_id}
DELETE /api/v1/dividends/{dividend_id}
```

Recommended list filters:

```text
entity_code
ex_date_from
ex_date_to
```

Keep route handlers thin: parse/validate request input, call the façade/domain service, map domain exceptions to HTTP responses, and return the result. Responses should expose entity code/name, amount currency code, serialized dates, and standard IDs/timestamps rather than only internal UUID fields. Return 404 for a missing event or entity association.

Because the server exposes routes through `FastMCP.from_fastapi`, the new endpoints should also become MCP tools through the existing bridge.

**Verify:** Exercise the OpenAPI routes with a test client or running local server, then inspect the generated MCP tool list if the project has an MCP smoke-test convention.

### Task 7: Update documentation

**Files:**

- `AGENTS.md`
- any API documentation under `docs/`

Document:

- the `dividend_event` table
- `portfolio_dividend` fee records
- the ex-date eligibility rule
- the meaning of `total_dividend_gain` and `total_dividend_fees`
- the PHP aggregate output and the optional event amount currency/FX behavior
- the optional portfolio dividend fee currency and FX behavior
- the fact that fees are user-supplied totals with no modeled source
- that only gross dividend amounts are calculated; net amounts are out of scope
- the absence of receipt/accounting reconciliation

**Verify:** Confirm the documented endpoint paths and response fields match the implementation.

## Out of scope

Do not implement these in this iteration:

- dividend provenance or source metadata
- dividend event status/cancellation workflow
- broker receipt records
- gross/net receipt reconciliation
- withholding-tax history or effective-date tax rules
- automatic FX-rate ingestion or currency-rate management
- tax-law validation or tax-rate calculation
- separate account/custody entities
- automatic dividend ingestion
- special accounting treatment for return of capital

## Acceptance criteria

## Implemented API surface

The implemented REST routes are also exposed through the FastMCP bridge:

- `POST`, `GET`, `PUT`, and `DELETE /api/v1/dividends` (with `{dividend_id}` for item operations)
- `POST`, `GET`, `PUT`, and `DELETE /api/v1/portfolios/{portfolio_id}/dividends/{dividend_id}`
- `GET /api/v1/portfolios/{portfolio_id}/dividends` for fee-row listing
- `GET /api/v1/portfolios/{portfolio_id}/dividends/totals` and
  `GET /api/v1/portfolios/dividends/totals`

Event responses include entity and amount-currency codes; fee responses include
the fee-currency code. Aggregate totals are PHP values and expose gross
`total_dividend_gain` plus separate `total_dividend_fees`. No net dividend or
tax-rate result is calculated.

The feature is complete when:

- Dividend events can be created, listed, updated, and deleted.
- A dividend event stores only the agreed simple fields plus standard IDs/timestamps and an optional amount-per-share currency entity.
- Aggregate totals expose `total_dividend_gain` and `total_dividend_fees`.
- By-entity totals expose `dividend_gain` and `dividend_fees`.
- Dividend eligibility follows the documented pre-ex-date holdings rule.
- Dividend amounts are converted to PHP when `amount_per_share_entity_id` is provided.
- Portfolio-specific dividend fees can be supplied and optionally converted to PHP using `fees_entity_id`.
- Aggregate totals expose gross dividend gain and separately accumulated dividend fees.
- No tax or net-dividend calculation is performed.
- Existing realized, unrealized, and ordinary-fee totals retain their current meanings.
- API and agent documentation reflect the simplified design.

## Suggested implementation order

1. Confirm the current checkout and architecture.
2. Define date, currency, response, and all-portfolio aggregation semantics.
3. Add schema, records, and request models.
4. Add database methods.
5. Implement the dividend and portfolio-fee services.
6. Implement the focused dividend calculation and integrate it with totals.
7. Add dividend event and portfolio-fee endpoints.
8. Update documentation.
