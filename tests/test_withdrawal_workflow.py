"""抽检无效 -> 批次撤回工作流的回归测试。

覆盖需求要点：
- 用到无效水样的签发批次失效，未用到的照常有效；
- 复核仍可查验原签发证据与 artifact_sha256；
- 撤回后旧证据链接停止交付；
- 撤回/签发并发时含无效水样的批次最终无效；
- 撤回重试幂等不新增记录，重跑不会重新签发；
- 批次 JSON 暴露当前效力与撤回原因。
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.main import create_app
from app.review.service import create_run, serialize, withdraw_sample
from app.service import pipeline


@pytest.fixture
def app(tmp_path):
    application = create_app(str(tmp_path / "workbench.sqlite3"))
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def approved_run(client, profile="clean"):
    run = client.post("/api/runs", json={"profile": profile}).get_json()
    response = client.post(
        f"/api/runs/{run['run_id']}/review",
        json={"decision": "APPROVED", "comment": "已核验", "expected_revision": 0})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_lineage_lists_calculation_samples(client):
    run = client.post("/api/runs", json={"profile": "clean"}).get_json()
    assert set(run["sample_ids"]) == {"W001", "W002", "W003", "W004", "W005", "W006"}


def test_unused_invalid_sample_leaves_batch_effective(client):
    run = approved_run(client)
    response = client.post("/api/samples/W999/invalidate", json={"reason": "无关编号"})
    assert response.status_code == 200
    body = client.get(f"/api/runs/{run['run_id']}").get_json()
    assert body["effective"] is True
    assert body["valid"] is True
    assert body["status"] == "APPROVED"


def test_used_sample_invalidates_issued_batch_and_stops_evidence(client):
    run = approved_run(client)
    response = client.post("/api/samples/W002/invalidate", json={"reason": "锰复测不合格"})
    assert response.status_code == 200
    assert run["run_id"] in response.get_json()["affected_run_ids"]

    body = client.get(f"/api/runs/{run['run_id']}").get_json()
    assert body["effective"] is False
    assert body["valid"] is False
    assert body["withdrawn"] is True
    assert body["status"] == "WITHDRAWN"
    assert "锰复测不合格" in body["withdrawal_reason"]

    evidence = client.get(f"/api/runs/{run['run_id']}/evidence")
    assert evidence.status_code in (409, 410)


def test_withdrawal_retry_adds_no_records(client):
    run = approved_run(client)
    for _ in range(3):
        response = client.post("/api/withdrawals",
                               json={"sample_id": "W002", "reason": "重复撤回"})
        assert response.status_code == 200
    records = client.get("/api/withdrawals").get_json()
    assert len(records) == 1
    invalid = client.get("/api/invalid-samples").get_json()
    assert len(invalid) == 1
    assert invalid[0]["sample_id"] == "W002"
    assert client.get(f"/api/runs/{run['run_id']}").get_json()["effective"] is False


def test_rerun_returns_same_withdrawn_batch_and_cannot_reissue(client):
    run = approved_run(client)
    client.post("/api/samples/W002/invalidate", json={"reason": "无效"})

    again = client.post("/api/runs", json={"profile": "clean"}).get_json()
    assert again["run_id"] == run["run_id"]
    assert again["effective"] is False

    stale = client.post(f"/api/runs/{run['run_id']}/review",
                        json={"decision": "APPROVED", "comment": "重签", "expected_revision": 1})
    assert stale.status_code == 409

    current = client.get(f"/api/runs/{run['run_id']}").get_json()
    fresh = client.post(
        f"/api/runs/{run['run_id']}/review",
        json={"decision": "APPROVED", "comment": "再签",
              "expected_revision": current["revision"]})
    assert fresh.status_code == 409
    assert client.get(f"/api/runs/{run['run_id']}/evidence").status_code == 409


def test_original_evidence_and_checksum_remain_verifiable(client):
    run = approved_run(client)
    artifact = client.get(f"/api/runs/{run['run_id']}/evidence").get_json()
    digest = artifact["artifact_sha256"]
    client.post("/api/samples/W002/invalidate", json={"reason": "无效"})

    response = client.post("/api/evidence/verify", json=artifact)
    assert response.status_code == 200
    result = response.get_json()
    assert result["checksum_valid"] is True
    assert result["verified"] is True
    assert result["effective"] is False
    assert result["withdrawn"] is True
    assert result["actual_sha256"] == digest

    by_run = client.post(f"/api/runs/{run['run_id']}/verify", json={}).get_json()
    assert by_run["checksum_valid"] is True
    assert by_run["original"]["artifact_sha256"] == digest

    tampered = dict(artifact)
    tampered["project"] = "被改动"
    assert client.post("/api/evidence/verify", json=tampered).get_json()["checksum_valid"] is False


def test_concurrent_approval_and_withdrawal_ends_invalid(app):
    with app.app_context():
        store = app.extensions["runs"]
        run = create_run(store, pipeline("clean"))
        run_id = run["run_id"]

    barrier = threading.Barrier(2)

    def approve():
        barrier.wait()
        return app.test_client().post(
            f"/api/runs/{run_id}/review",
            json={"decision": "APPROVED", "comment": "竞速", "expected_revision": 0})

    def withdraw():
        barrier.wait()
        return app.test_client().post(
            "/api/samples/W003/invalidate", json={"reason": "竞速撤回"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        approval = pool.submit(approve)
        withdrawal = pool.submit(withdraw)
        assert withdrawal.result(timeout=20).status_code == 200
        assert approval.result(timeout=20).status_code in (200, 409)

    final = app.test_client().get(f"/api/runs/{run_id}").get_json()
    assert final["effective"] is False
    assert final["valid"] is False
    assert "W003" in final["invalid_samples"]
    assert app.test_client().get(f"/api/runs/{run_id}/evidence").status_code == 409


def test_cascade_is_selective_between_two_batches(app):
    with app.app_context():
        store = app.extensions["runs"]
        first = create_run(store, pipeline("clean"))
        payload = json.loads(json.dumps(pipeline("clean")))
        payload["run_key"] = "custom-batch-without-w002"
        payload["sample_ids"] = ["W001", "W003", "W004"]
        second = create_run(store, payload)

        from app.review.service import approve
        approve(store, first["run_id"], "APPROVED", "a", 0)
        approve(store, second["run_id"], "APPROVED", "b", 0)
        withdraw_sample(store, "W002", "坏水样")

        affected = serialize(store.get(first["run_id"]))
        unaffected = serialize(store.get(second["run_id"]))
        assert affected["effective"] is False
        assert unaffected["effective"] is True
        assert unaffected["status"] == "APPROVED"


def test_review_endpoint_can_withdraw_with_vocabulary(client):
    run = approved_run(client)
    response = client.post(
        f"/api/runs/{run['run_id']}/review",
        json={"decision": "REVOKED", "reason": "复核撤回", "expected_revision": 1})
    assert response.status_code == 200
    body = response.get_json()
    assert body["effective"] is False
    assert body["status"] == "REVOKED"


def test_workbench_home_exposes_validity_and_reason(client):
    html = client.get("/").get_data(as_text=True)
    assert "水值" in html
    assert "validity-state" in html
    assert "withdraw-reason" in html
    assert "spot-invalidate" in html
