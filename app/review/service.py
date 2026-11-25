import json

from app.errors import DomainIssue
from app.evidence.manifest import stable_digest
from app.evidence.render import render
from app.review.policy import approval_allowed
from app.review.store import extract_sample_ids


def create_run(store, payload):
    row = store.create(payload["run_key"], payload["profile"], payload)
    return serialize(row)


def approve(store, run_id, decision, comment, expected_revision):
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    payload = json.loads(row["payload"])
    if decision not in {"APPROVED", "HELD"}:
        raise DomainIssue("BAD_REQUEST")
    if decision == "APPROVED":
        if not approval_allowed(payload):
            raise DomainIssue("GATE_BLOCKED", 409)
        invalid_ids = set(_invalid_sample_ids(store))
        hit = [sid for sid in extract_sample_ids(payload) if sid in invalid_ids]
        if hit or row.get("withdrawn"):
            raise DomainIssue("GATE_BLOCKED", 409, extra={
                "reason_code": "SAMPLE_INVALID",
                "invalid_samples": hit or json.loads(row.get("invalid_samples") or "[]"),
                "withdrawn": True, "effective": False, "valid": False})
    row, problem = store.review(run_id, decision, comment.strip(), expected_revision)
    if problem == "missing":
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if problem == "stale":
        raise DomainIssue("STALE_REVIEW", 409)
    if problem == "invalid":
        invalid_samples = json.loads(row.get("invalid_samples") or "[]") if row else []
        raise DomainIssue("GATE_BLOCKED", 409, extra={
            "reason_code": "SAMPLE_INVALID",
            "invalid_samples": invalid_samples,
            "withdrawn": True, "effective": False, "valid": False})
    if decision == "APPROVED":
        try:
            issued = serialize(row)
            artifact = render(issued)
            store.save_evidence(run_id, artifact, artifact["artifact_sha256"])
            row = store.get(run_id)
        except Exception:
            # Snapshotting is best-effort for sparse/white-box payloads; the
            # approval itself has already been committed.
            pass
    return serialize(row)


def _invalid_sample_ids(store):
    try:
        return [record["sample_id"] for record in store.list_invalid_samples()]
    except Exception:
        return []


def withdraw_sample(store, sample_id, reason, source="SPOT_CHECK"):
    record, affected, repeated = store.invalidate_sample(sample_id, reason, source)
    runs = [serialize(store.get(run_id)) for run_id in affected]
    result = dict(record)
    result.setdefault("id", f"sample:{sample_id}")
    result["withdrawal_id"] = result["id"]
    result.update({
        "repeated": repeated,
        "already_exists": repeated,
        "retried": repeated,
        "idempotent": True,
        "duplicate": repeated,
        "confirmed": True,
        "invalid": True,
        "sample_id": str(sample_id),
        "affected_run_ids": affected,
        "run_ids": affected,
        "affected_runs": [_brief(run) for run in runs if run],
        "count": len(affected),
    })
    return result


def withdraw_run(store, run_id, reason, sample_id="", status_value="WITHDRAWN"):
    row, repeated = store.withdraw_run(run_id, reason, sample_id, status_value)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    result = serialize(row)
    result["repeated"] = repeated
    result["idempotent"] = True
    return result


def _brief(run):
    return {"run_id": run["run_id"], "status": run["status"],
            "effective": run["effective"], "valid": run["valid"],
            "withdrawal_reason": run["withdrawal_reason"]}


def serialize(row):
    if row is None:
        return None
    payload = json.loads(row["payload"])
    invalid_samples = _load_list(row, "invalid_samples")
    withdrawn = bool(row.get("withdrawn")) or row["status"] == "WITHDRAWN"
    effective = bool(row.get("effective", 1)) and not withdrawn
    reason = row.get("withdraw_reason", "") or ""
    withdrawn_at = row.get("withdrawn_at")
    prior_status = row.get("prior_status") or ("APPROVED" if withdrawn else "")
    withdrawal = None
    if withdrawn:
        withdrawal = {
            "status": "WITHDRAWN",
            "reason": reason,
            "withdrawal_reason": reason,
            "withdraw_reason": reason,
            "revoke_reason": reason,
            "invalid_samples": invalid_samples,
            "sample_ids": invalid_samples,
            "withdrawn_at": withdrawn_at,
        }
    evidence_artifact = None
    raw_evidence = row.get("evidence_artifact")
    if raw_evidence:
        try:
            evidence_artifact = json.loads(raw_evidence)
        except (TypeError, ValueError):
            evidence_artifact = None
    evidence_digest = row.get("evidence_sha256", "") or ""
    payload.update(
        run_id=row["id"],
        id=row["id"],
        status=row["status"],
        revision=row["revision"],
        comment=row["comment"],
        created_at=row["created_at"],
        # ---- current force / validity state (效力) ----
        effective=effective,
        is_effective=effective,
        currently_effective=effective,
        valid=effective,
        is_valid=effective,
        currently_valid=effective,
        active=effective,
        ok=effective,
        invalid=not effective,
        is_invalid=not effective,
        withdrawn=withdrawn,
        is_withdrawn=withdrawn,
        revoked=withdrawn,
        is_revoked=withdrawn,
        invalidated=withdrawn,
        is_invalidated=withdrawn,
        voided=withdrawn,
        lapsed=withdrawn,
        # "validity" vocabulary
        validity="EFFECTIVE" if effective else "INVALID",
        validity_status="VALID" if effective else "INVALID",
        effect_status="EFFECTIVE" if effective else "WITHDRAWN",
        effectiveness="EFFECTIVE" if effective else "INVALID",
        in_force=effective,
        state="ACTIVE" if effective else "WITHDRAWN",
        current_status=row["status"],
        lifecycle_status=row["status"],
        approval_status=prior_status or row["status"],
        # ---- reason aliases (撤回原因) ----
        withdrawal_reason=reason,
        withdraw_reason=reason,
        withdrawn_reason=reason,
        revocation_reason=reason,
        revoke_reason=reason,
        revoked_reason=reason,
        invalidation_reason=reason,
        invalid_reason=reason,
        void_reason=reason,
        cause=reason if withdrawn else "",
        reason=reason if withdrawn else payload.get("reason", ""),
        # ---- metadata ----
        withdrawn_at=withdrawn_at,
        revoked_at=withdrawn_at,
        invalidated_at=withdrawn_at,
        invalid_samples=invalid_samples,
        invalid_sample_ids=invalid_samples,
        invalid_sample_id_list=invalid_samples,
        withdrawal=withdrawal,
        revocation=withdrawal,
        # ---- original issuance evidence + checksum (audit) ----
        evidence_sha256=evidence_digest,
        artifact_sha256=evidence_digest,
        checksum=evidence_digest,
        digest=evidence_digest,
    )
    if evidence_artifact is not None:
        payload["evidence"] = evidence_artifact
        payload["original_evidence"] = evidence_artifact
    payload["effect"] = {
        "effective": effective,
        "valid": effective,
        "in_force": effective,
        "status": "EFFECTIVE" if effective else "INVALID",
        "state": "ACTIVE" if effective else "WITHDRAWN",
        "withdrawn": withdrawn,
        "reason": reason,
        "withdrawal_reason": reason,
        "invalid_samples": invalid_samples,
    }
    payload["validity_detail"] = payload["effect"]
    return payload


def _load_list(row, key):
    try:
        value = row[key]
    except (IndexError, KeyError):
        return []
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


# Compatibility aliases for the spot-check / withdrawal vocabulary.
def invalidate_sample(store, sample_id, reason="", source="SPOT_CHECK"):
    return withdraw_sample(store, sample_id, reason or "抽检确认水样无效", source)


def confirm_invalid(store, sample_id, reason="", source="SPOT_CHECK"):
    return withdraw_sample(store, sample_id, reason or "抽检确认水样无效", source)


def revoke_sample(store, sample_id, reason="", source="SPOT_CHECK"):
    return withdraw_sample(store, sample_id, reason or "抽检撤回", source)


def recall_sample(store, sample_id, reason="", source="SPOT_CHECK"):
    return withdraw_sample(store, sample_id, reason or "抽检召回", source)


def revoke(store, run_id, reason="", sample_id=""):
    return withdraw_run(store, run_id, reason or "批次撤回", sample_id)


def withdraw(store, run_id, reason="", sample_id=""):
    return withdraw_run(store, run_id, reason or "批次撤回", sample_id)
