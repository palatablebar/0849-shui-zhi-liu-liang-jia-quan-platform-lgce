from app.domain.helpers import D


def decide_rows(items):
    for item in items:
        if item.get("problem"):
            item["verdict"] = "REVIEW"
            item["reason"] = item["problem"]
        elif item["mean"] is None:
            item["verdict"] = "REVIEW"
            item["reason"] = "NO_MEAN"
        elif item["mean"] > D("5.0", "LIMIT"):
            item["verdict"] = "REJECT"
            item["reason"] = "MEAN_OVER_5"
        elif item["mean"] > D("1.0", "LIMIT"):
            item["verdict"] = "REVIEW"
            item["reason"] = "MEAN_OVER_LIMIT"
        else:
            item["verdict"] = "PASS"
            item["reason"] = "WITHIN_LIMIT"
        item.setdefault("verdict", "PASS")
        item.setdefault("reason", "OK")
    return items
