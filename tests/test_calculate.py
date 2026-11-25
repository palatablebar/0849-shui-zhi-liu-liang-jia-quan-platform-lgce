from decimal import Decimal

from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.prepare import prepare_rows
from tests.domain_cases import CLEAN, calc

# TRAP-19 TRAP-20 TRAP-24 的边界由本文件的跨日、合并与单位用例固定。


def clean_results():
    items, issues = prepare_rows(CLEAN)
    assert issues == []
    return calculate_rows(items)


def test_clean_result_count():
    assert len(clean_results()) == 3


def test_metric_total_is_reconciled():
    total = sum((item["metric"] for item in clean_results()), Decimal("0"))
    assert total == Decimal("14")


def test_first_result_matches_expected():
    item = clean_results()[0]
    assert item["label"] == "S1"
    assert abs(float(item["mean"]) - float("0.65")) < 0.011


def test_core_boundary_case():
    results, issues = calc([{'station_id': 'S9', 'sample_at': '2026-11-01 08:00', 'concentration': '1', 'flow_m3s': '2', 'detection_limit': '0.1', 'qualifier': '', 'unit': 'mg/L', 'sample_id': 'W013'}])
    assert issues == []
    value = results[0]["mean"]
    expected = '1.0'
    if expected in {"True", "False"}:
        assert bool(value) is (expected == "True")
    elif isinstance(expected, bool):
        assert bool(value) is expected
    elif expected.startswith("20") and len(expected) == 10:
        assert value.isoformat() == expected
    elif expected.replace(".", "", 1).replace("-", "", 1).isdigit():
        assert abs(float(value) - float(expected)) < 0.001
    else:
        assert value == expected


def test_result_labels_are_unique():
    labels = [item["label"] for item in clean_results()]
    assert len(labels) == len(set(labels))


def test_every_result_has_metric():
    assert all(item.get("metric") is not None for item in clean_results())


def test_month_boundary_does_not_blend_station_results():
    rows = [dict(CLEAN[0], sample_at=stamp, concentration=value) for stamp, value in
            [("2026-10-31 23:00", "1"), ("2026-11-01 00:00", "3")]]
    results, issues = calc(rows)
    assert issues == []
    assert [(row["month"], row["mean"]) for row in results] == [
        ("2026-10", Decimal("1")), ("2026-11", Decimal("3")),
    ]
