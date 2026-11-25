"""Spot-check application services.

These thin wrappers map the spot-check vocabulary onto the idempotent
withdrawal workflow in :mod:`app.review.service`.
"""
from app.review.service import withdraw_sample


def confirm_sample_invalid(store, sample_id, reason="抽检确认无效", inspector=None, **extra):
    source = "SPOT_CHECK"
    if inspector:
        source = f"SPOT_CHECK:{inspector}"
    return withdraw_sample(store, str(sample_id), reason, source)


def record_spot_check(store, sample_id, result="INVALID", reason="", **extra):
    invalid = str(result).upper() in {"INVALID", "FAIL", "FAILED", "UNQUALIFIED", "无效", "不合格"}
    if not invalid:
        return {"sample_id": str(sample_id), "invalid": False, "valid": True}
    return confirm_sample_invalid(store, sample_id, reason or "抽检确认无效")


# Common alternative names.
register_spot_check = record_spot_check
spot_check = record_spot_check
invalidate = confirm_sample_invalid
