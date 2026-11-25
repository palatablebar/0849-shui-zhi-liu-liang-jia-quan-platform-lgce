def approval_allowed(payload):
    """Gate used by the review workflow.

    Besides the original REJECT-count gate, an explicitly invalid/withdrawn
    payload can never be approved.  Plain pipeline payloads do not carry those
    markers, so the historical behaviour is unchanged.
    """
    if not payload:
        return False
    if int(payload.get("summary", {}).get("REJECT", 0)) != 0:
        return False
    if payload.get("withdrawn") is True or payload.get("revoked") is True:
        return False
    if payload.get("effective") is False or payload.get("valid") is False or payload.get("is_valid") is False:
        return False
    invalid_samples = payload.get("invalid_samples") or payload.get("invalid_sample_ids")
    if isinstance(invalid_samples, list) and invalid_samples and payload.get("status") == "WITHDRAWN":
        return False
    return True


def batch_effective(payload, invalid_sample_ids=None):
    """A batch is effective when it was issued and none of its calculation
    samples were confirmed invalid."""
    if not payload:
        return False
    if payload.get("status") not in (None, "APPROVED"):
        return False
    if payload.get("effective") is False or payload.get("valid") is False:
        return False
    if invalid_sample_ids:
        used = set(payload.get("sample_ids") or payload.get("used_sample_ids") or [])
        if used.intersection(str(sid) for sid in invalid_sample_ids):
            return False
    return True


can_approve = approval_allowed
can_issue = approval_allowed
is_effective = batch_effective
