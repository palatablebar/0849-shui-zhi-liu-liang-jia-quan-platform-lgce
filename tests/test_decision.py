from app.domain.decision import decide_rows
from tests.domain_cases import calc, run, run_profile


def test_clean_sample_has_no_blocker():
    results, _ = run_profile("clean")
    assert sum(1 for item in results if item["verdict"] == "REJECT") == 0


def test_dirty_sample_has_blocker():
    results, _ = run_profile("dirty")
    assert any(item["verdict"] == "REJECT" for item in results)


def test_each_result_has_reason():
    results, _ = run_profile("dirty")
    assert all(item.get("reason") for item in results)


def test_boundary_case_one():
    results, issues = run([{'station_id': 'V1', 'sample_at': '2026-11-01 08:00', 'concentration': '0.5', 'flow_m3s': '1', 'detection_limit': '0.1', 'qualifier': '', 'unit': 'mg/L', 'sample_id': 'X001'}])
    assert not issues
    assert results[0]["verdict"] == "PASS"
    assert results[0]["reason"] == "WITHIN_LIMIT"


def test_boundary_case_two():
    results, issues = run([{'station_id': 'V2', 'sample_at': '2026-11-01 08:00', 'concentration': '5', 'flow_m3s': '1', 'detection_limit': '0.1', 'qualifier': '', 'unit': 'mg/L', 'sample_id': 'X002'}])
    assert not issues
    assert results[0]["verdict"] == "REVIEW"


def test_verdict_counts_match_rows():
    from app.service import pipeline
    payload = pipeline("clean")
    assert sum(payload["summary"].values()) == payload["counts"]["computed"]
    assert set(payload["summary"]) == {"PASS", "REVIEW", "REJECT"}
