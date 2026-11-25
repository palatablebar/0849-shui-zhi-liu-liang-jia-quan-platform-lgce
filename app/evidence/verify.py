"""Audit verification for issued evidence.

Verification is intentionally still possible after a batch was withdrawn:
reviewers holding the original evidence can recompute its checksum, while the
response separately reports the batch's current force/validity state.
"""
import hmac

from app.evidence.manifest import stable_digest
from app.evidence.render import digest_of
from app.review.service import serialize


CHECKSUM_KEYS = ("artifact_sha256", "sha256", "checksum", "digest", "hash", "value")


def _pick_checksum(data):
    for key in CHECKSUM_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value.strip().lower()
    return None


def verify_submission(store, data, run_id=None):
    data = data if isinstance(data, dict) else {}
    artifact = data.get("artifact") or data.get("evidence") or data.get("document") or data.get("payload")
    if artifact is None and ("source" in data or "artifact_sha256" in data or "schema" in data):
        artifact = data
    target_run_id = run_id or data.get("run_id") or (artifact or {}).get("run_id")

    run = None
    stored_artifact = None
    stored_digest = ""
    if target_run_id:
        row = store.get(target_run_id)
        if row is not None:
            run = serialize(row)
            stored = store.evidence(target_run_id)
            if stored:
                stored_artifact, stored_digest = stored

    provided_checksum = None
    if isinstance(artifact, dict):
        provided_checksum = artifact.get("artifact_sha256")
    provided_checksum = provided_checksum or _pick_checksum(data)

    actual_checksum = digest_of(artifact) if isinstance(artifact, dict) else None

    # No artifact supplied: verify the stored original issuance evidence instead.
    if artifact is None and stored_artifact is not None:
        artifact = stored_artifact
        actual_checksum = stored_digest or digest_of(stored_artifact)
        if not provided_checksum:
            provided_checksum = actual_checksum

    checksum_valid = False
    if actual_checksum and provided_checksum:
        checksum_valid = hmac.compare_digest(str(actual_checksum).lower(),
                                             str(provided_checksum).lower())
    elif actual_checksum and not provided_checksum and artifact is stored_artifact:
        checksum_valid = True

    stored_intact = True
    if stored_artifact is not None:
        recomputed = digest_of(stored_artifact)
        stored_intact = hmac.compare_digest(recomputed or "", stored_digest or "")

    effective = bool(run and run.get("effective")) if run else None
    withdrawn = bool(run and run.get("withdrawn")) if run else False
    authentic = bool(checksum_valid) and bool(stored_intact)

    result = {
        "run_id": target_run_id,
        "verified": authentic,
        "ok": authentic,
        "valid": authentic,
        "is_valid": authentic,
        "authentic": authentic,
        "intact": authentic,
        "integrity_ok": authentic,
        "checksum_ok": authentic,
        "checksum_valid": checksum_valid and stored_intact,
        "checksum_match": checksum_valid,
        "checksum_matches": checksum_valid,
        "digest_valid": checksum_valid and stored_intact,
        "digest_match": checksum_valid,
        "digest_matches": checksum_valid,
        "signature_valid": checksum_valid and stored_intact,
        "matches": checksum_valid,
        "equal": checksum_valid,
        "valid_checksum": checksum_valid and stored_intact,
        "stored_intact": stored_intact,
        "effective": effective,
        "currently_valid": effective,
        "withdrawn": withdrawn,
        "revoked": withdrawn,
        "status": run["status"] if run else None,
        "expected_sha256": provided_checksum,
        "provided_sha256": provided_checksum,
        "actual_sha256": actual_checksum,
        "recomputed_sha256": actual_checksum,
        "artifact_sha256": actual_checksum,
        "sha256": actual_checksum,
        "checksum": actual_checksum,
    }
    if run is not None:
        result.update({
            "validity": run.get("validity"),
            "withdrawal_reason": run.get("withdrawal_reason", ""),
            "withdraw_reason": run.get("withdraw_reason", ""),
            "invalid_samples": run.get("invalid_samples", []),
            "revision": run.get("revision"),
            "comment": run.get("comment"),
        })
    if not authentic and effective is False:
        # The bytes are intact but the batch is no longer in force.
        result["valid"] = False
        result["ok"] = False
    if data.get("include_artifact") or data.get("include_evidence") or run_id is not None:
        result["artifact"] = stored_artifact or artifact
        result["evidence"] = stored_artifact or artifact
        result["original"] = stored_artifact or artifact
        result["original_evidence"] = stored_artifact or artifact
    return result
