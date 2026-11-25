from app.errors import DomainIssue
from app.evidence.manifest import stable_digest


def render(run):
    if run is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if run["status"] != "APPROVED":
        if run.get("withdrawn") or run["status"] == "WITHDRAWN" or run.get("effective") is False:
            raise DomainIssue("EVIDENCE_WITHDRAWN", 409, extra={
                "reason_code": "WITHDRAWN",
                "withdrawal_reason": run.get("withdrawal_reason", ""),
                "withdraw_reason": run.get("withdraw_reason", ""),
                "invalid_samples": run.get("invalid_samples", []),
                "effective": False, "valid": False, "withdrawn": True, "revoked": True})
        raise DomainIssue("NOT_APPROVED", 409)
    artifact = {
        "schema": "workbench-evidence/v1",
        "run_id": run["run_id"],
        "project": run["project"],
        "rule": run["rule"],
        "profile": run["profile"],
        "source": run["source"],
        "counts": run["counts"],
        "ledger": run["ledger"],
        "summary": run["summary"],
        "review": {"status": run["status"], "revision": run["revision"], "comment": run["comment"]},
    }
    artifact["artifact_sha256"] = stable_digest(artifact)
    return artifact


def digest_of(artifact):
    """Recompute the checksum embedded in a previously issued artifact."""
    if not isinstance(artifact, dict):
        return None
    body = {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    return stable_digest(body)
