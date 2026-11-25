from app.catalog import SPEC
from app.domain.helpers import D, DAY, I, M, TS, optional
from app.intake.normalize import canonical


PARSERS = {
    "money": lambda value, code: M(value, code),
    "decimal": lambda value, code: D(value, code),
    "decimal_optional": lambda value, code: optional(D, value, code),
    "int": lambda value, code: I(value, code),
    "int_positive": lambda value, code: positive(I(value, code)),
    "date": lambda value, code: DAY(value, code),
    "date_optional": lambda value, code: optional(DAY, value, code),
    "datetime": lambda value, code: TS(value, code),
}


def positive(value):
    if value <= 0:
        raise ValueError("BAD_POSITIVE")
    return value


def _typed(rows):
    typed, issues = [], []
    for raw in rows:
        item = dict(raw)
        try:
            if not canonical(item.get(SPEC["key"], "")):
                raise ValueError("KEY_MISSING")
            for field, kind in SPEC["types"].items():
                code = "INVALID_" + field.upper()
                item[field] = PARSERS[kind](item.get(field), code)
            typed.append(item)
        except ValueError as exc:
            issues.append({"line": raw.get("_line", 0), "key": raw.get(SPEC["key"], ""), "code": str(exc)})
    return typed, issues


def prepare_rows(rows):
    typed, issues = _typed(rows)
    rows = typed
    items = []
    for item in typed:
        try:
            if not canonical(item.get(SPEC["key"], "")):
                raise ValueError("KEY_MISSING")
            unit = item["unit"].lower()
            if unit == "ug/l":
                item["concentration"] /= D("1000", "BAD_UNIT")
                if item["detection_limit"] is not None:
                    item["detection_limit"] /= D("1000", "BAD_UNIT")
            elif unit == "ng/l":
                item["concentration"] /= D("1000000", "BAD_UNIT")
                if item["detection_limit"] is not None:
                    item["detection_limit"] /= D("1000000", "BAD_UNIT")
            elif unit != "mg/l":
                raise ValueError("BAD_UNIT")
            item["unit"] = "mg/L"
            if item["flow_m3s"] < 0:
                raise ValueError("BAD_FLOW")
            if item["concentration"] < 0:
                raise ValueError("BAD_CONCENTRATION")
            qualifier = item["qualifier"].upper()
            if qualifier == "ND":
                if item["detection_limit"] is None or item["detection_limit"] <= 0:
                    raise ValueError("MISSING_LIMIT")
                item["concentration"] = item["detection_limit"] / D("2", "BAD_LIMIT")
            elif qualifier not in {"", "OK"}:
                raise ValueError("BAD_QUALIFIER")
            items.append(item)
        except ValueError as exc:
            issues.append({"line": item.get("_line", 0), "key": item.get(SPEC["key"], ""), "code": str(exc)})
    return items, issues
