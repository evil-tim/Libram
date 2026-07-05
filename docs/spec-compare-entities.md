# Spec: compare_entities — Peer Comparison Tool

**Status:** Draft
**Author:** Sunflower + evil_tim
**Date:** 2026-07-05

---

## Overview

A single MCP tool that takes a list of entity codes (stock tickers), a date range, and
optional indicator parameters, and returns a side-by-side comparison table with relative
rankings. Composable entirely from existing Libram primitives (OHLCV, price_summary,
SMA, EMA, RSI).

**Design goal:** Replace N tool calls + manual arithmetic with one call that returns a
comparison table ready for analysis. The key value-add over calling existing tools
individually is the **relative ranking** — each entity's metrics expressed as rank and
delta-from-median within the peer group.

---

## Tool Signature

```python
compare_entities(
    entity_codes: list[str],      # Required. 2-10 entity codes (tickers).
    start: str,                    # Required. ISO 8601 datetime, inclusive.
    end: str,                      # Required. ISO 8601 datetime, exclusive.
    indicators: list[str] = [],    # Optional. Any of: "sma20", "sma50", "ema20", "ema50", "rsi14".
                                   # Custom periods via "sma:<N>", "ema:<N>", "rsi:<N>".
    normalize_to: str = "median"   # Optional. "median" (default) or "first" — baseline for
                                   # relative return calculation.
) -> ComparisonResult
```

### Parameter Details

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| entity_codes | string[] | Yes | — | 2-10 codes. Each resolved via existing entity lookup. Missing codes return an error per-code, not a hard failure. |
| start | ISO datetime | Yes | — | Converted to each entity's timezone (same as existing tools). |
| end | ISO datetime | Yes | — | Exclusive upper bound. |
| indicators | string[] | No | `[]` | Named presets ("sma20", "sma50", "ema20", "ema50", "rsi14") or custom via "type:period" syntax. Empty = summary-only output. |
| normalize_to | string | No | `"median"` | `"median"` = relative return vs. group median. `"first"` = relative return vs. group's first-period mean (sector baseline). |

### Indicator Presets and Custom Syntax

Presets:
- `"sma20"` → SMA, period 20
- `"sma50"` → SMA, period 50
- `"ema20"` → EMA, period 20
- `"ema50"` → EMA, period 50
- `"rsi14"` → RSI, period 14

Custom:
- `"sma:100"` → SMA, period 100
- `"ema:12"` → EMA, period 12
- `"rsi:7"` → RSI, period 7

Validation: period must be >= 2. Unknown indicator types return a per-request warning, not a hard failure (graceful degradation — return what you can).

---

## Response Schema

### Success Response

```json
{
  "meta": {
    "start": "2026-01-01T00:00:00+08:00",
    "end": "2026-07-05T00:00:00+08:00",
    "entity_count": 4,
    "requested_indicators": ["sma20", "rsi14"],
    "normalize_to": "median",
    "generated_at": "2026-07-05T12:00:00Z"
  },
  "entities": [
    {
      "code": "MWIDE",
      "name": "Megawide Construction Corporation",
      "status": "ok",
      "summary": {
        "count": 127,
        "first_close": 2.95,
        "last_close": 4.09,
        "min": 2.71,
        "max": 4.09,
        "avg": 3.18,
        "std_dev": 0.287,
        "period_return_pct": 38.64,
        "annualized_return_pct": 86.2,
        "volatility_pct": 22.7,
        "max_drawdown_pct": -8.1
      },
      "indicators": {
        "sma20": { "latest": 3.714, "latest_date": "2026-07-03" },
        "rsi14": { "latest": 80.36, "latest_date": "2026-07-03" }
      }
    },
    {
      "code": "EEI",
      "name": "EEI Corporation",
      "status": "ok",
      "summary": { "..." : "..." },
      "indicators": { "..." : "..." }
    },
    {
      "code": "INVALID_XYZ",
      "name": null,
      "status": "not_found",
      "summary": null,
      "indicators": null
    }
  ],
  "rankings": {
    "period_return_pct": [
      { "code": "MWIDE", "value": 38.64, "rank": 1, "delta_from_baseline": 22.14 },
      { "code": "DMC", "value": 18.20, "rank": 2, "delta_from_baseline": 1.70 },
      { "code": "EEI", "value": 12.50, "rank": 3, "delta_from_baseline": -4.00 },
      { "code": "CLI", "value": 9.80, "rank": 4, "delta_from_baseline": -6.70 }
    ],
    "volatility_pct": [ "..." ],
    "max_drawdown_pct": [ "..." ],
    "rsi14": [ "..." ]
  },
  "baseline": {
    "metric": "median",
    "period_return_pct": 16.50,
    "volatility_pct": 19.3,
    "note": "Median of group. Each entity's delta_from_baseline = entity_value - this."
  }
}
```

### Key Fields Explained

**summary (per entity):**

| Field | Source | Notes |
|-------|--------|-------|
| count | price_summary | Number of trading days in range |
| first_close | price_summary | Close on first day of range |
| last_close | price_summary | Close on last day of range |
| min / max | price_summary | Price range |
| avg | price_summary | Arithmetic mean close |
| std_dev | price_summary | Standard deviation of closes |
| period_return_pct | price_summary | (last_close - first_close) / first_close * 100 |
| annualized_return_pct | **computed** | period_return_pct * (252 / count). Annualized assuming 252 trading days/year. |
| volatility_pct | **computed** | (std_dev / avg) * 100. Coefficient of variation — unitless comparison across price levels. |
| max_drawdown_pct | **computed** | Largest peak-to-trough decline within the range. Computed from OHLCV close series. |

**rankings (cross-entity):**

Each metric gets a ranked list. `delta_from_baseline` is the entity's value minus the
group baseline (median by default). This is the core analytical output — it tells you
at a glance which stocks are outperforming/underperforming peers and by how much.

**baseline:**

The reference point for relative calculations. `median` = middle value of the group
(excluding entities with `status != "ok"`). `first` = mean of all entities' first_close
(useful for equal-weight sector index comparison).

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| 1 or 0 entity_codes | 400 error: "Need at least 2 entities to compare." |
| Entity code not found | `status: "not_found"` in that entity's row. Other entities still returned. |
| Entity has no data in range | `status: "no_data"`, summary/indicators null. Other entities still returned. |
| Indicator computation fails for one entity | Indicator field null for that entity. Other indicators and entities unaffected. |
| All entities fail | Return entities array with all non-ok statuses, rankings empty. HTTP 200 (not an error — valid request, no data). |
| start >= end | 400 error. |
| >10 entity_codes | 400 error: "Maximum 10 entities per comparison." |

**Partial success is the default.** A bad entity code should not prevent you from seeing
the results for the valid ones.

---

## Implementation Notes

### Composition from Existing Primitives

Internally, `compare_entities` calls existing Libram functions:

```
for each entity_code in entity_codes:
    1. resolve entity_id via entity lookup (existing)
    2. list_price_summary(entity_id, start, end)  # gets count, min, max, avg, std_dev, returns
    3. list_prices_for_entity(entity_id, start, end, size=count)  # for max_drawdown computation
    4. for each indicator:
       - get_simple_moving_average / get_exponential_moving_average / get_rsi
       - extract the last data point's value and date

then:
    5. compute annualized_return_pct, volatility_pct, max_drawdown_pct
    6. rank each metric across the group
    7. compute baseline (median) and deltas
```

**Performance consideration:** For N entities and M indicators, this is N * (1 + M) internal
calls. With N=10 and M=5, that's 60 calls. Consider:
- Running entity resolution in parallel (all independent)
- Running price_summary + OHLCV fetch in parallel per entity
- Running indicator computations in parallel per entity
- Caching entity resolution results (same entity may appear in multiple comparisons)

### Max Drawdown Computation

Not currently exposed as a standalone tool. Computed from the OHLCV close series:

```python
def max_drawdown(closes: list[float]) -> float:
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak
        if dd > max_dd:
            max_dd = dd
    return -max_dd * 100  # negative percentage
```

This is O(n) and can run on the same price series fetched for the summary.

### Annualized Return

Simple annualization: `period_return * (252 / trading_days_in_range)`.
This assumes the range is representative — it's a scaling, not a compound projection.
Disclose this assumption in the meta or a `note` field.

---

## What This Does NOT Do (Explicit Non-Goals)

1. **No valuation ratios** — P/E, P/B, EV/EBITDA require earnings/balance sheet data not in Libram.
2. **No automatic peer discovery** — caller must supply the entity list. Sector metadata is out of scope.
3. **No time-series comparison** — this is a point-in-time comparison over one range, not "show me how these stocks moved relative to each other over time." A future tool could return a time-series of relative returns.
4. **No weighting** — all entities are equal-weight in baseline computation. Market-cap weighting requires shares outstanding data.

---

## Future Extensions (Not In This Spec)

| Extension | Dependency | Value |
|-----------|-----------|-------|
| Sector metadata + auto peer discovery | Entity metadata layer | Single-ticker input |
| Valuation ratios (P/E, P/B) | Earnings/balance sheet data pipeline | True valuation comparison |
| Time-series relative return | New response shape | Chart-ready data |
| Sector index (equal/mcap weighted) | Shares outstanding data | Benchmark comparison |
| Rolling correlation matrix | OHLCV only (composable) | Diversification analysis |

---

## Example Usage (Agent Workflow)

**Agent prompt:** "Compare MWIDE against its construction peers EEI and DMC over H1 2026."

**Agent calls:**
```
compare_entities(
    entity_codes=["MWIDE", "EEI", "DMC"],
    start="2026-01-01T00:00:00",
    end="2026-07-01T00:00:00",
    indicators=["sma20", "sma50", "rsi14"]
)
```

**Agent interprets:**
"Megawide significantly outperformed its construction peers in H1 2026, returning +38.6%
vs. the peer median of +16.5% — a +22.1 percentage point outperformance. However, it also
carries the highest RSI (80.4 vs. peer median 62.1), suggesting the outperformance may be
extended. Volatility is in line with peers (22.7% vs. 19.3% median), so the higher return
came without proportionally higher risk. Max drawdown of -8.1% was the shallowest in the
group, indicating stronger buying support on pullbacks."

That interpretation — grounded in the comparison table rather than derived from one stock's
price pattern — is what makes this tool genuinely useful for fundamental analysis.
