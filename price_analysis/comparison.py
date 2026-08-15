"""Comparison helpers for ranking and summarizing entities."""

import asyncio
import re
import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from price_analysis.date_utils import convert_to_timezone_aware
from price_analysis.max_drawdown import compute_max_drawdown
from price_analysis.moving_averages import compute_ema, compute_sma
from price_analysis.rsi import compute_rsi

if TYPE_CHECKING:
    from price_management.service import PriceManagerService


_INDICATOR_RE = re.compile(r"^(sma|ema|rsi)(?::?(\d+))?$")


def _parse_indicator(spec: str) -> Optional[tuple[str, int]]:
    """Parse an indicator spec string into (type, period) or None if invalid."""
    m = _INDICATOR_RE.match(spec.strip().lower())
    if not m:
        return None
    kind = m.group(1)
    period = int(m.group(2)) if m.group(2) else {"sma": 20, "ema": 20, "rsi": 14}[kind]
    if period < 2:
        return None
    return (kind, period)


def _compute_indicator(kind: str, period: int, series: list[tuple[datetime, float]]) -> Optional[dict]:
    """Compute an indicator and return the latest data point, or None if not enough data."""
    if kind == "sma":
        data = compute_sma(series, period)
    elif kind == "ema":
        data = compute_ema(series, period)
    elif kind == "rsi":
        data = compute_rsi(series, period)
    else:
        return None
    if not data:
        return None
    last = data[-1]
    return {"latest": last["value"], "latest_date": last["date"]}


async def _resolve_and_fetch_entity(
    code: str,
    start_dt: datetime,
    end_dt: datetime,
    price_manager: PriceManagerService,
    indicator_specs: list[tuple[str, int]],
) -> dict:
    """Resolve an entity code and fetch its summary + indicators."""
    entities = price_manager.query_entities(None, code, None, None)
    entity_list = list(entities) if entities else []
    if not entity_list:
        return {
            "code": code,
            "name": None,
            "status": "not_found",
            "summary": None,
            "indicators": None,
        }

    entity = entity_list[0]
    entity_id = entity.id
    entity_name = entity.name
    timezone = getattr(entity, "timezone", None) or "UTC"

    tz_start = convert_to_timezone_aware(start_dt.strftime("%Y-%m-%dT%H:%M:%S"), timezone)
    tz_end = convert_to_timezone_aware(end_dt.strftime("%Y-%m-%dT%H:%M:%S"), timezone)

    summary_raw = price_manager.query_price_summary(entity_id, tz_start, tz_end)
    if not summary_raw:
        return {
            "code": code,
            "name": entity_name,
            "status": "no_data",
            "summary": None,
            "indicators": None,
        }

    series = price_manager.query_close_series(entity_id, tz_start, tz_end)

    count_val: int = summary_raw.get("count", 0)  # type: ignore[assignment]
    period_return_val: float = summary_raw.get("period_return_pct", 0.0)  # type: ignore[assignment]
    avg_val: float = summary_raw.get("avg", 0.0)  # type: ignore[assignment]
    std_dev_val: float = summary_raw.get("std_dev", 0.0)  # type: ignore[assignment]

    count = int(count_val) if count_val is not None else 0
    period_return_pct = float(period_return_val) if period_return_val is not None else 0.0
    annualized_return_pct = round(period_return_pct * (252 / count), 2) if count > 0 else 0.0

    avg = float(avg_val) if avg_val is not None else 0.0
    std_dev = float(std_dev_val) if std_dev_val is not None else 0.0
    volatility_pct = round((std_dev / avg) * 100, 2) if avg and avg != 0 else 0.0

    max_drawdown_pct = compute_max_drawdown(series) if series else 0.0

    summary = {
        "count": count,
        "first_close": summary_raw.get("first_close"),
        "last_close": summary_raw.get("last_close"),
        "min": summary_raw.get("min"),
        "max": summary_raw.get("max"),
        "avg": summary_raw.get("avg"),
        "std_dev": summary_raw.get("std_dev"),
        "period_return_pct": period_return_pct,
        "annualized_return_pct": annualized_return_pct,
        "volatility_pct": volatility_pct,
        "max_drawdown_pct": max_drawdown_pct,
    }

    indicators = {}
    for kind, period in indicator_specs:
        key = f"{kind}{period}"
        try:
            result = _compute_indicator(kind, period, series)
            indicators[key] = result
        except Exception:
            indicators[key] = None

    return {
        "code": code,
        "name": entity_name,
        "status": "ok",
        "summary": summary,
        "indicators": indicators if indicators else None,
    }


def _standard_competition_rank(values: list[tuple[str, float]], reverse: bool = True) -> list[dict]:
    """Rank entities by a metric using standard competition ranking."""
    sorted_vals = sorted(values, key=lambda x: x[1], reverse=reverse)

    ranked = []
    current_rank = 1
    for i, (code, val) in enumerate(sorted_vals):
        if i > 0 and val != sorted_vals[i - 1][1]:
            current_rank = i + 1
        ranked.append({"code": code, "value": val, "rank": current_rank})

    ok_values = [v for _, v in values]
    baseline = statistics.median(ok_values) if ok_values else 0.0

    for entry in ranked:
        entry["delta_from_baseline"] = round(entry["value"] - baseline, 2)

    return ranked


async def build_comparison_payload(
    entity_codes: list[str],
    start: str,
    end: str,
    indicators: list[str],
    normalize_to: str,
    price_manager: PriceManagerService,
) -> dict:
    """Build the comparison payload for a set of entities."""
    if len(entity_codes) < 2:
        raise ValueError("Need at least 2 entities to compare.")
    if len(entity_codes) > 10:
        raise ValueError("Maximum 10 entities per comparison.")

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%S")
        end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise ValueError("Invalid date format. Use YYYY-MM-DDTHH:MM:SS.") from exc

    if start_dt >= end_dt:
        raise ValueError("start must be before end.")

    parsed_indicators: list[tuple[str, int]] = []
    unknown_indicators: list[str] = []
    for spec in indicators:
        parsed = _parse_indicator(spec)
        if parsed:
            parsed_indicators.append(parsed)
        else:
            unknown_indicators.append(spec)

    tasks = [
        _resolve_and_fetch_entity(code, start_dt, end_dt, price_manager, parsed_indicators)
        for code in entity_codes
    ]
    entity_results = await asyncio.gather(*tasks)

    ok_entities = [e for e in entity_results if e["status"] == "ok"]
    rankings: dict[str, list[dict]] = {}

    if ok_entities:
        for metric, reverse in [
            ("period_return_pct", True),
            ("annualized_return_pct", True),
            ("volatility_pct", False),
            ("max_drawdown_pct", True),
        ]:
            values = [(e["code"], e["summary"][metric]) for e in ok_entities]
            rankings[metric] = _standard_competition_rank(values, reverse=reverse)

        for kind, period in parsed_indicators:
            key = f"{kind}{period}"
            values = []
            for e in ok_entities:
                ind = e.get("indicators", {})
                if ind and key in ind and ind[key] is not None:
                    values.append((e["code"], ind[key]["latest"]))
            if values:
                reverse = kind != "rsi"
                rankings[key] = _standard_competition_rank(values, reverse=reverse)

    baseline: dict[str, object] = {"metric": normalize_to}
    if ok_entities:
        for metric in ["period_return_pct", "annualized_return_pct", "volatility_pct", "max_drawdown_pct"]:
            vals = [e["summary"][metric] for e in ok_entities]
            baseline[metric] = round(statistics.median(vals), 2)
        for kind, period in parsed_indicators:
            key = f"{kind}{period}"
            vals = []
            for e in ok_entities:
                ind = e.get("indicators", {})
                if ind and key in ind and ind[key] is not None:
                    vals.append(ind[key]["latest"])
            if vals:
                baseline[key] = round(statistics.median(vals), 4)
    else:
        baseline["note"] = "No valid entities to compute baseline."

    meta = {
        "start": start,
        "end": end,
        "entity_count": len(entity_codes),
        "requested_indicators": indicators,
        "normalize_to": normalize_to,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    if unknown_indicators:
        meta["warnings"] = [f"Unknown indicator spec: {s}" for s in unknown_indicators]

    return {
        "meta": meta,
        "entities": entity_results,
        "rankings": rankings,
        "baseline": baseline,
    }


__all__ = [
    "_parse_indicator",
    "_standard_competition_rank",
    "build_comparison_payload",
]
