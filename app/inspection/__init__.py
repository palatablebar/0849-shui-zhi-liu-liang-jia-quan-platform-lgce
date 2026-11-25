"""Spot-check (抽检) workflow package."""
from app.inspection.service import confirm_sample_invalid, record_spot_check

__all__ = ["confirm_sample_invalid", "record_spot_check"]
