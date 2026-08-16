"""Tests for entity comparison helpers."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from price_analysis.comparison import (
    _parse_indicator,
    _standard_competition_rank,
    build_comparison_payload,
)


def test_parse_indicator_supports_defaults_and_colon_syntax() -> None:
    assert _parse_indicator("SMA") == ("sma", 20)
    assert _parse_indicator(" ema:5 ") == ("ema", 5)
    assert _parse_indicator("rsi14") == ("rsi", 14)


@pytest.mark.parametrize("spec", ["", "macd", "sma:1", "rsi0", "ema:x"])
def test_parse_indicator_rejects_invalid_specs(spec: str) -> None:
    assert _parse_indicator(spec) is None


def test_standard_competition_rank_handles_ties_and_baseline() -> None:
    assert _standard_competition_rank(
        [("A", 10.0), ("B", 5.0), ("C", 10.0)],
    ) == [
        {"code": "A", "value": 10.0, "rank": 1, "delta_from_baseline": 0.0},
        {"code": "C", "value": 10.0, "rank": 1, "delta_from_baseline": 0.0},
        {"code": "B", "value": 5.0, "rank": 3, "delta_from_baseline": -5.0},
    ]


def test_standard_competition_rank_can_rank_ascending() -> None:
    assert [
        entry["code"]
        for entry in _standard_competition_rank(
            [("A", 3.0), ("B", 1.0), ("C", 2.0)],
            reverse=False,
        )
    ] == ["B", "C", "A"]


def test_standard_competition_rank_empty_values_has_zero_baseline() -> None:
    assert _standard_competition_rank([]) == []


class FakePriceManager:
    def __init__(self, records: dict[str, dict[str, object]]) -> None:
        self.records = records

    def query_entities(self, *_args: object) -> list[object]:
        code = _args[1]
        record = self.records.get(code)
        return [record["entity"]] if record else []

    def query_price_summary(
        self, entity_id: object, *_args: object
    ) -> dict[str, object] | None:
        for record in self.records.values():
            if record.get("entity").id == entity_id:
                return record.get("summary")  # type: ignore[return-value]
        return None

    def query_close_series(
        self, entity_id: object, *_args: object
    ) -> list[tuple[datetime, float]]:
        for record in self.records.values():
            if record.get("entity").id == entity_id:
                return record["series"]  # type: ignore[return-value]
        return []


def make_record(
    code: str, closes: list[float], period_return: float
) -> dict[str, object]:
    entity = SimpleNamespace(
        id=uuid4(), code=code, name=f"Entity {code}", timezone="UTC"
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    series = [
        (start + timedelta(days=index), value) for index, value in enumerate(closes)
    ]
    return {
        "entity": entity,
        "series": series,
        "summary": {
            "count": len(closes),
            "first_close": closes[0],
            "last_close": closes[-1],
            "min": min(closes),
            "max": max(closes),
            "avg": sum(closes) / len(closes),
            "std_dev": 1.0,
            "period_return_pct": period_return,
        },
    }


def build(
    manager: FakePriceManager, codes: list[str], indicators: list[str]
) -> dict[str, object]:
    return asyncio.run(
        build_comparison_payload(
            codes,
            "2026-01-01T00:00:00",
            "2026-01-10T00:00:00",
            indicators,
            "period_return_pct",
            manager,  # type: ignore[arg-type]
        ),
    )


def test_comparison_ranks_entities_and_builds_baseline() -> None:
    manager = FakePriceManager(
        {
            "A": make_record("A", [10, 11, 12, 13], 20.0),
            "B": make_record("B", [10, 10, 10, 10], 10.0),
        }
    )

    result = build(manager, ["A", "B"], ["sma:2"])

    assert [entity["status"] for entity in result["entities"]] == ["ok", "ok"]
    assert result["rankings"]["period_return_pct"][0]["code"] == "A"
    assert result["rankings"]["period_return_pct"][0]["rank"] == 1
    assert result["baseline"]["period_return_pct"] == 15.0
    assert result["baseline"]["sma2"] == 11.25


def test_comparison_reports_missing_and_no_data_entities() -> None:
    records = {"A": make_record("A", [10, 11, 12], 20.0)}
    records["EMPTY"] = {
        "entity": SimpleNamespace(id=uuid4(), name="Empty", timezone="UTC"),
        "series": [],
        "summary": None,
    }

    result = build(FakePriceManager(records), ["A", "EMPTY", "MISSING"], [])

    assert [entity["status"] for entity in result["entities"]] == [
        "ok",
        "no_data",
        "not_found",
    ]
    assert result["baseline"]["metric"] == "period_return_pct"
    assert "period_return_pct" in result["rankings"]


def test_comparison_warns_about_unknown_indicators() -> None:
    result = build(
        FakePriceManager(
            {"A": make_record("A", [1, 2], 5.0), "B": make_record("B", [2, 3], 6.0)}
        ),
        ["A", "B"],
        ["bogus"],
    )

    assert result["meta"]["warnings"] == ["Unknown indicator spec: bogus"]


@pytest.mark.parametrize(
    "codes, start, end, message",
    [
        ([], "2026-01-01T00:00:00", "2026-01-02T00:00:00", "Need at least 2"),
        (["A"] * 11, "2026-01-01T00:00:00", "2026-01-02T00:00:00", "Maximum 10"),
        (["A", "B"], "bad", "2026-01-02T00:00:00", "Invalid date"),
        (["A", "B"], "2026-01-02T00:00:00", "2026-01-01T00:00:00", "start must be"),
    ],
)
def test_comparison_validates_request(
    codes: list[str], start: str, end: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        asyncio.run(
            build_comparison_payload(
                codes, start, end, [], "period_return_pct", FakePriceManager({})
            )
        )  # type: ignore[arg-type]
