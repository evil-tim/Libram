# Entity Max Timestamp Plan

Status: Architecture locked; implementation pending.

## Context

`entity.min_timestamp` already records the availability floor for an entity — "earliest timestamp for which price data is available" (`schema.sql:32`). Exactly one consumer reads it: `generate_monthly_tasks()` uses it as the backward scan stop (`price_scheduler/service.py:111`, `stop_date = min_date or entity.min_timestamp`), falling back to `2000-01-01` when unset (`:113`).

There is no availability ceiling. All three task generators anchor on `now` in the entity's timezone and scan *backward* from a recent window, so nothing prevents them from creating a task covering time after the last observation that can ever exist:

| Generator | Entry point | Scan anchor | Backward bound |
|---|---|---|---|
| `generate_daily_tasks` | `service.py:157` | `now - 1 day` (`:176`) | previous Monday, `now - (weekday + 7)` (`:177`) |
| `generate_weekly_tasks` | `service.py:214` | `_prev_week(now)` (`:232`) | `now - 30 days` (`:235-236`) |
| `generate_monthly_tasks` | `service.py:89` | `_prev_month(now)` (`:108`) | `min_date or entity.min_timestamp` (`:111`) |

### The failure this fixes: RRHI

The PSE approved Robinsons Retail Holdings' voluntary delisting (index removal effective 16 July 2026; delisting from the official registry effective 31 August 2026). RRHI is a seeded DAILY entity in Libram (`data.sql:67`, `code='RRHI'`, `cmpy_id=646`, PSE Edge datasource `77796ac5-b6c4-459f-be29-9248c48744d4`), and PSE Edge serves chart data for it only through **2026-07-10** (the operator's reading of the source; this plan does not verify it — see Seed data).

No month window after July 2026 can contain data, yet `_month_has_missing_prices()` reports every one of them as missing — every weekday in them is absent, permanently. So for each new month the generators mint a task whose fetch cannot succeed:

- `generate_monthly_tasks` starts at the previous month and walks backward (`:108`). With `now` in September 2026 it finds August missing and creates a task for it; as the calendar advances the unclamped anchor advances with it, so September, then October, then November each get minted in turn — one new task per month, without bound (`:143`).
- Each of those tasks is picked up by `PriceSchedulerExecutor.execute_task()` (`price_scheduler/executor.py:93-116`), which calls `fetch_and_store()` over the task's own range with no ceiling check.
- An out-of-range PSE Edge request returns no usable `chartData`, and `PSEEdgeDataSource.parse_price_data()` turns that into `ValueError("Expected 'chartData' field in response data")` (`price_sources/pse_edge_datasource.py:42-43`). `execute_task()` catches it and calls `db.fail_task()` (`executor.py:113-116`, `libram_database/db.py:509-522`), so the task is retried on `retry_delay * 3^retry_count` backoff until it exhausts `max_retries` (5) and lands in `FAILED`.

The result is an unbounded tail of permanently `FAILED` tasks, each one having burned five requests at a live exchange endpoint. `min_timestamp` has the mirror problem at the other end of the series and solves it by bounding the scan; `max_timestamp` is the same mechanism applied to the recent end.

## Scope

In scope:

- a nullable `entity.max_timestamp` column;
- enforcement of that ceiling in the three `PriceSchedulerService.generate_*` entry points;
- exposing the value through `EntityRecord`, and therefore through `GET /api/v1/entities` and the `list_available_entities` MCP tool;
- the RRHI seed value in `data.sql`;
- focused unit tests in `tests/unit/price_scheduler/`.

Out of scope:

- **Cleanup of tasks that already exist beyond the ceiling.** No deletion, cancellation, rewrite, or status change of existing `task` rows. Stated limitation of this feature; see Risks.
- **Enforcement anywhere other than task creation.** The executor (`price_scheduler/executor.py`), `PriceManagerService.fetch_and_store()`, and `cli_fetch.py` are untouched: a task that already exists inside the ceiling's shadow must still be allowed to drain, and a manual fetch of an explicit range is an operator decision, not a scheduling rule.
- **An entity metadata write API.** No entity create/update surface exists today — `min_timestamp` is seed-only, and there is no `update_entity` in `libram_database/db.py`. Adding one is a separate concern (Decision 6).
- The snapshot subsystem (`snapshot_state`, `snapshot_scheduler/executor.py`). It is driven by durable leases, not by the historical task generators, and the generators only select `frequency = 'DAILY'` entities.
- Any change to stored prices, read endpoints, indicators, comparison, or portfolio math. The ceiling is a task-generation bound, not a data bound.
- Deriving the ceiling automatically (from an exchange delisting feed, or by inferring it from "the source returned nothing"). The value is operator-supplied, exactly like `min_timestamp`.

## Decisions

1. **`max_timestamp` is nullable, and `NULL` means unbounded.** The default for all 79 seeded entities is `NULL`, so every current behaviour is preserved without a data migration.
2. **Semantics: the latest timestamp for which price data can exist for this entity.** The mirror of `min_timestamp`, and read the same way — a scan bound, not a data filter.
3. **One enforcement rule: clamp the scan anchor.** In each generator, immediately after the scan anchor is computed: `scan = min(scan, max_timestamp.astimezone(now.tzinfo))` when the ceiling is set. Task bounds handed to `create_new_task()` are never clamped or rewritten.
4. **Window granularity, not instants.** Eligibility is decided by a window's own bounds, and the window *containing* the ceiling is admitted whole. This is what `min_timestamp` already does: `if month_end.date() < stop_date: break` (`service.py:128`) admits the month that straddles the floor in full, and compares calendar dates rather than instants.
5. **Task creation is the only enforcement point.** See the out-of-scope entry above.
6. **The value is set the way `min_timestamp` is set: seed/SQL.** No new write endpoint in this change.
7. **Read exposure is free.** `routes/entities.py` returns `EntityRecord` objects directly (`routes/entities.py:32`), so adding the field to the dataclass publishes it in both the REST and MCP surfaces with no route change.
8. **No new index.** The ceiling is read once per entity in Python during a scan; it is never a SQL predicate.

## Schema

Append immediately after the `entity` `CREATE TABLE` (`schema.sql:35`):

```sql
-- latest timestamp for which price data is available for this entity.
-- NULL means unbounded (no ceiling). Mirror of min_timestamp: a scan bound for
-- the task generators, not a filter on stored prices.
ALTER TABLE entity
    ADD COLUMN IF NOT EXISTS max_timestamp timestamptz;
```

The `ALTER` is required, not cosmetic: `CREATE TABLE IF NOT EXISTS` cannot add a column to a database that already has `entity`, and `init_db()` only replays `schema.sql` through `conn.execute(text(ddl))` (`libram_database/db.py:37-45`). The repository already uses this pattern to add a column idempotently — `ALTER TABLE portfolio ADD COLUMN IF NOT EXISTS description` (`schema.sql:175-176`).

`data.sql`'s entity `INSERT` (`:14`) names its columns explicitly and does **not** include `max_timestamp`, so it keeps working unchanged.

### Type

`libram_types/libram_types.py:20` — add the field after `min_timestamp`:

```python
    min_timestamp: Optional[datetime] = None
    max_timestamp: Optional[datetime] = None
```

`libram_database/db.py:111` — map it in `query_entities()`:

```python
                    min_timestamp=r.get("min_timestamp"),
                    max_timestamp=r.get("max_timestamp"),
```

Because PostgreSQL returns `timestamptz` as a timezone-aware `datetime`, the generators receive an aware value and the clamp below needs no naive-datetime handling.

## Enforcement

The three generators scan backward in one direction from a single anchor, so clamping that anchor is exactly equivalent to rejecting every window that starts after the ceiling — with no branch in any loop body, no change to window bounds, and no new helper function.

### Why an anchor clamp rather than a per-window gate

- A window starting before the ceiling is by definition a window that can contain data at or below it, so every window the clamped scan visits is eligible and no window after the ceiling is ever visited.
- It is a no-op when the ceiling is `NULL`, so the three existing tests in `tests/unit/price_scheduler/test_scheduler_service.py` continue to pass unchanged.
- The rejected alternative — a `_window_is_fetchable()` check inside each loop — needs three call sites, and invites a future fourth generator to forget it.

Clamping also leaves `scan` no longer necessarily on a window boundary. That is safe: every loop body re-derives its window via `_month_range_for(scan)` / `_week_range_for(scan)`, and advances via `_prev_month(scan)` / `_prev_week(scan)`, all of which normalize to the month/week start.

### `generate_daily_tasks` (`service.py:174-177`)

```python
            now = datetime.now(ZoneInfo(entity.timezone) if entity.timezone else None)
            # Start from yesterday and scan back through the previous week
            scan = now - timedelta(days=1)
            # never scan a window that starts after the entity's availability ceiling
            if entity.max_timestamp is not None:
                scan = min(scan, entity.max_timestamp.astimezone(now.tzinfo))
            week_cutoff = now - timedelta(days=now.weekday() + 7)  # Previous Monday
```

### `generate_weekly_tasks` (`service.py:231-236`)

```python
            now = datetime.now(ZoneInfo(entity.timezone) if entity.timezone else None)
            scan = _prev_week(now)
            # never scan a window that starts after the entity's availability ceiling
            if entity.max_timestamp is not None:
                scan = min(scan, entity.max_timestamp.astimezone(now.tzinfo))

            # determine the stop date for scanning (previous month)
            month_ago = now - timedelta(days=30)
```

### `generate_monthly_tasks` (`service.py:107-114`)

```python
            now = datetime.now(ZoneInfo(entity.timezone) if entity.timezone else None)
            scan = _prev_month(now)
            # never scan a window that starts after the entity's availability ceiling
            if entity.max_timestamp is not None:
                scan = min(scan, entity.max_timestamp.astimezone(now.tzinfo))

            # determine the stop date for scanning
            stop_date = min_date or entity.min_timestamp
```

### Why `astimezone(now.tzinfo)`

`now` is already in the entity's timezone, and `_month_range_for()` / `_week_range_for()` take the window's calendar month or week from `scan`'s own `tzinfo`. Converting the ceiling into the same frame keeps its calendar date in the entity's timezone. Without it, a ceiling stored as `2026-07-10 00:00:00+08` would be windowed in whatever frame the driver returned (typically a UTC offset), which shifts the ceiling's date for any value between 16:00 and 24:00 local.

This is the same class of detail `min_timestamp` gets wrong today — it compares a raw `.date()` against a Manila-anchored `month_end.date()` while the driver may have returned a UTC-offset instant. The ceiling normalizes rather than propagating the bug; the floor is left alone here.

### The straddling window is admitted whole, and stays "incomplete"

With the ceiling at 2026-07-10, the July window `[2026-07-01, 2026-08-01)` is still generated, and its fetch legitimately returns the bars through 10 July. Its weekday checks for 13–31 July keep returning `True` from `_month_has_missing_prices()` (`service.py:27-46`), because `has_weekend` is false for equities. That does not produce a second task: `get_task_for_range()` (`service.py:138`) finds the existing row, and the loop only reaches `open_task_count += 1` when that row is still `OPEN`.

### Worked example (RRHI, ceiling `2026-07-10 00:00:00+08`, now `2026-09-26 12:00` Manila)

| Generator | Anchor unclamped | Anchor clamped | First window | Windows after the ceiling |
|---|---|---|---|---|
| daily | 2026-09-25 12:00 | 2026-07-10 00:00 | none — `week_cutoff` is 2026-09-14, so `scan.date() >= week_cutoff.date()` is false immediately | none |
| weekly | 2026-09-14 00:00 | 2026-07-10 00:00 | none — `week_end` 2026-07-13 is before `stop_date` 2026-08-27 | none |
| monthly | 2026-08-01 00:00 | 2026-07-10 00:00 | `[2026-07-01, 2026-08-01)`, the ceiling's own month | none — the scan walks back into June and earlier, which can contain data |

Two useful properties fall out of this:

- The ceiling's precision is not load-bearing once it is in the past. Both `daily` and `weekly` are already bounded near `now` and produce nothing at all here; only `monthly` creates a task, and it is the same July window it would have created with no ceiling at all.
- The clamps are no-ops for the mid-window case: a ceiling inside the daily/weekly span truncates only the newer end of the walk, and the older part (which can contain data) is still backfilled.

## Read path

No route or MCP change. `libram_types/libram_types.py` and `libram_database/db.py` carry the field (above), and:

- `GET /api/v1/entities` serializes `EntityRecord`, so `max_timestamp` appears in the response;
- the `list_available_entities` MCP tool inherits the same route (`operation_id="list_available_entities"`, `routes/entities.py:14`).

This is the only observability the feature has: nothing logs or reports "this entity is capped" (see Risks).

## Seed data

Append to `data.sql` — a separate idempotent statement rather than a new column in the 79-row multi-row `INSERT`, which would require editing every `VALUES` tuple:

```sql
-- RRHI voluntarily delisted from the PSE (delisting effective 2026-08-31).
-- PSE Edge chart data for cmpy_id 646 ends 2026-07-10, so no fetch task should
-- be generated for time after that. Scoped by datasource_id because entity.code
-- is only unique per datasource (UNIQUE (datasource_id, code), schema.sql:34).
UPDATE public.entity
   SET max_timestamp = '2026-07-10 00:00:00+08'
 WHERE code = 'RRHI'
   AND datasource_id = '77796ac5-b6c4-459f-be29-9248c48744d4'::uuid;
```

The time-of-day component is inert under Decision 4, since eligibility compares calendar dates in the entity's timezone; `'2026-07-11 00:00:00+08'` would behave identically. `2026-07-10 00:00:00+08` is written because it is the `timestamp_start` of the last bar PSE Edge serves, which mirrors how `min_timestamp` records the first bar's start (`'2013-11-11 00:00:00+08'`, `data.sql:67`).

**The value is an operator assertion, not something this feature derives or verifies.** It is the last date PSE Edge serves for `cmpy_id=646`; the delisting article does not state a last trading day — it reports the block crossing on 13 July, suspension after the crossing, index removal on 16 July, and delisting effective 31 August. If the ceiling is re-derived later, re-check it against the live source.

## Implementation phases

Each phase is one commit.

**Phase 1 — schema, type, read mapping.** `schema.sql` (the `ALTER` after `:35`), `libram_types/libram_types.py:20`, `libram_database/db.py:111`. Nothing consumes the field yet. Verify: `uv run pytest tests/unit -q` stays green; replaying `schema.sql` twice against an existing database is a no-op.

**Phase 2 — enforcement.** The three clamps in `price_scheduler/service.py` (`:176`, `:232`, `:108`), plus the new tests below. Verify: `uv run pytest tests/unit/price_scheduler -q`.

**Phase 3 — seed.** The `UPDATE` in `data.sql`. Verify: replaying `data.sql` twice is idempotent and leaves `max_timestamp` at the same value. Requires a PostgreSQL instance; this is an operator step, not a test-suite step.

## Risks and safeguards

- **The limitation is real: existing out-of-range tasks are not cleaned up.** They still execute, fail five times, and become `FAILED`. They also *suppress* duplicates, because `get_task_for_range()` matches on the exact range and the generator skips a range that already has a task row (`service.py:140-149`) — so the pre-existing tail is stable rather than growing, and the ceiling stops new members from being added to it. An optional operator cleanup, deliberately not shipped as code:

  ```sql
  DELETE FROM task
   WHERE entity_id = '9194b1fb-a721-41d3-82af-4c729599f28d'::uuid
     AND timestamp_start > '2026-07-10T00:00:00+08'
     AND status IN ('OPEN', 'FAILED');
  ```

  Deleting a `COMPLETED` row is what would be wrong here: the July window is `COMPLETED` and permanently "incomplete" by weekday count, so removing it would make the generator recreate it on the next run.

- **The straddling window re-fetches if it is ever re-executed.** `_prices_exist()` (`price_management/service.py:205-230`) counts expected weekdays against stored rows, and post-ceiling weekdays can never be stored, so that task is permanently below the expected count and a re-run re-hits the source. The insert itself stays idempotent (`save_prices` skips existing rows, `db.py:117-198`), so this costs requests, not duplicates. Pre-existing behaviour; the ceiling neither causes nor fixes it.

- **A wrong ceiling fails silently.** Nothing reports that an entity is capped. `GET /api/v1/entities` carrying the field is the only signal, and no new logging is added. A ceiling set too late is mostly harmless — once it is in the past, the daily and weekly generators are already bounded near `now` and produce nothing, and the monthly generator's window is month-granular — so the recoverable error is limited to the delisting month itself.

- **`min_timestamp > max_timestamp` produces no tasks and no warning.** The monthly loop's existing `break` on `month_end.date() < stop_date` (`service.py:128-129`) fires once the clamped scan walks past the floor, so the entity goes quiet. Accepted: adding a validation channel for an operator-set pair is a larger change than the bound itself.

- **Set the ceiling by datasource, not by code alone.** `entity.code` is unique only per datasource (`UNIQUE (datasource_id, code)`, `schema.sql:34`), and `query_entities()` / `get_entity_by_code_raw()` filter on `code` with no datasource scope (`db.py:67-76`, `:84`) — a pre-existing ambiguity that a code-only `UPDATE` would silently inherit.

- **Container zone database is defective, so do not write tz tests through `ZoneInfo('UTC')`.** Verified in this environment: `/usr/share/zoneinfo/UTC` → `Etc/UTC` → `Etc/Universal` all report `utcoffset(2026-07-10) = 8:00:00`, `TZ=UTC date` prints `PST`, while `date -u` and `datetime.timezone.utc` are correct. Python `zoneinfo` has no `tzdata` fallback package installed, so `ZoneInfo('UTC')` is wrong here, and the seeded USD entity uses `timezone='Etc/Universal'` (`data.sql`). This is a host precondition, not something this feature fixes: the clamp uses `now.tzinfo` on both sides, so it stays internally consistent whatever the offset is. Build ceiling-test instants through the entity's own zone or `datetime.timezone.utc`, never through `ZoneInfo('UTC')`.

## Open decisions

Nothing blocks implementation. One choice to confirm before Phase 1, because it changes that phase's surface:

- **Is seed/SQL an acceptable write path for the ceiling (Decision 6)?** The alternative — an entity-metadata update endpoint and service method — would be the first write surface over `entity` at all, covering `min_timestamp` at the same time. Recommended: keep it out of this change; the trigger is a one-off delisting event, and the value is operator data.

## Verification

```bash
uv run pytest tests/unit -q
uv run pytest tests/unit/price_scheduler -q
uv run ruff check price_scheduler libram_database/db.py libram_types
uv run ruff format --check price_scheduler libram_database/db.py libram_types
git diff --check
```

Repo-wide `uv run ruff check .` and `uv run ruff format --check .` are not usable as gates: `main` already carries 318 lint findings and 41 unformatted files, and `ruff check .` also walks `.worktrees/`. Lint the paths a change touches, as `AGENTS.md` and `docs/fused_entity_data_plan.md` both note.

New tests belong in `tests/unit/price_scheduler/test_scheduler_service.py`, using the existing `StrictManagerSpy` / `StrictDbSpy` fakes and the `FixedDateTime` monkeypatch that file already establishes. All are unit tests — deterministic, no PostgreSQL, no network, per `AGENTS.md:231` and `:252-266`. Required cases:

1. **Regression guard.** The three existing tests pass unchanged with `max_timestamp=None` (the `EntityRecord` default), pinning that a NULL ceiling is a no-op.
2. **Daily, ceiling older than `week_cutoff`.** Assert `manager.price_queries == []` and `db.created == []` — no window after the ceiling is ever queried.
3. **Daily, ceiling inside the scan span.** Assert the first queried window is the ceiling's own day, and that every queried range satisfies `start.date() <= ceiling.date()`.
4. **Monthly, ceiling mid-month.** Assert the first queried window is `[ceiling month start, next month start)` — whole month, not clamped — and that no queried range starts after the ceiling.
5. **Monthly, ceiling before `min_timestamp`.** Assert nothing is created.
6. **Weekly, ceiling older than the 30-day stop.** Assert nothing is created.
7. **Timezone frame.** Entity `timezone="Asia/Manila"` with the ceiling supplied as a UTC-aware instant whose Manila date differs from its UTC date (`2026-07-09T20:00:00Z` = `2026-07-10` Manila). Assert the first monthly window is July, not June — this is the test that pins `astimezone(now.tzinfo)`. Build the UTC side with `datetime.timezone.utc`, not `ZoneInfo("UTC")`, per the environment hazard above.

Deliberately not tested: database-backed ceiling behaviour, route serialization of the new field (it is a dataclass field on an already-tested response), and the RRHI seed value (integration-shaped, and it is operator data).

No implementation, migration, or seed change ships with this document. Phase 1 is the first change to `schema.sql`.

## Repository references

Line numbers are verified against this plan's base, `main` at `0934a87`. They shift on other checkouts — `feat/entity-groups` alone reformats `libram_database/db.py` and `server.py` (671 insertions, 130 deletions in `db.py`), so re-check the anchors when reading this from a different worktree.

- `schema.sql` — `entity` (`:19-35`), `min_timestamp` (`:32`), uniqueness per datasource (`:34`), `task` (`:83-98`), the `ALTER ... ADD COLUMN IF NOT EXISTS` precedent (`:175-176`).
- `data.sql` — entity `INSERT` column list and RRHI row (`:14`, `:67`); PSE Edge datasource `77796ac5-b6c4-459f-be29-9248c48744d4`.
- `price_scheduler/service.py` — `generate_monthly_tasks` (`:89`), the `min_timestamp` scan stop (`:111`), `generate_daily_tasks` (`:157`), `generate_weekly_tasks` (`:214`), the missing-price helpers (`:27-77`).
- `price_scheduler/executor.py` — `execute_task()` runs a task's own range with no ceiling check (`:93-116`).
- `libram_database/db.py` — `get_entity_by_code_raw` (`:67`), `query_entities` (`:78`), the `code` filter (`:84`), `min_timestamp` mapping (`:111`), `save_prices` (`:117`), `create_new_task` (`:355`), `get_task_for_range` (`:384`), `fail_task` (`:509`).
- `libram_types/libram_types.py` — `EntityRecord` (`:9-20`).
- `price_management/service.py` — `fetch_and_store()` (`:43`) and `_prices_exist()` (`:205`), the weekday-count completeness check.
- `price_sources/pse_edge_datasource.py` — the empty-`chartData` failure (`:42-43`).
- `routes/entities.py` — returns `EntityRecord` directly (`:32`); `operation_id="list_available_entities"` (`:14`).
- `server.py` — the two daily task-generation jobs (`:17-22`); `cli_schedule.py` is the only caller of all three generators.
- `AGENTS.md` — scheduler architecture (`:122-136`), testing boundaries (`:229-268`).
- `docs/fused_entity_data_plan.md` — the house plan format this document follows.
