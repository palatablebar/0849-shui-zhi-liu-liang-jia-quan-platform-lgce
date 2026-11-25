from datetime import date, timedelta
from app.catalog import SPEC
from app.domain.helpers import *  # noqa: F401,F403


def calculate_rows(items):
    out = []
    rows = items
    stations = {}
    for item in items:
        month = item["sample_at"].strftime("%Y-%m")
        station = stations.setdefault((item["station_id"], month), {"numerator": D("0", "CALC"), "flow": D("0", "CALC"), "count": 0})
        station["numerator"] += item["concentration"] * item["flow_m3s"]
        station["flow"] += item["flow_m3s"]
        station["count"] += 1
    for (station_id, month), station in stations.items():
        if station["flow"] == 0:
            mean = None
            problem = "NO_FLOW"
        else:
            mean = station["numerator"] / station["flow"]
            problem = None
        out.append({"label": station_id, "month": month, "mean": mean, "flow_m3s": station["flow"],
                    "count": station["count"], "problem": problem,
                    "metric": station["flow"], "limit": D("1.0", "LIMIT")})
    for item in out:
        item.setdefault("label", str(item.get(SPEC["key"], "")))
    return out
