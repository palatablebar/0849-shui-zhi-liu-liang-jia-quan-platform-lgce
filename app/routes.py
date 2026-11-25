from flask import Blueprint, abort, jsonify, request, send_file

from app.catalog import SPEC
from app.config import SAMPLES
from app.errors import DomainIssue
from app.evidence.render import render
from app.evidence.verify import verify_submission
from app.review.service import (
    approve,
    create_run,
    serialize,
    withdraw_run,
    withdraw_sample,
)
from app.service import pipeline


api = Blueprint("api", __name__)
STAGE_INDEX = {"intake": 1, "prepare": 2, "calculate": 3, "decision": 4, "reconcile": 5}

SAMPLE_ID_KEYS = ("sample_id", "sampleId", "sample_no", "sampleNo", "sample_number",
                  "sampleNumber", "sample_code", "sampleCode", "water_sample_id",
                  "waterSampleId", "sid", "id", "code", "key", "target", "number")
REASON_KEYS = ("reason", "withdrawal_reason", "withdrawalReason", "withdraw_reason",
               "withdrawReason", "revocation_reason", "revocationReason", "revoke_reason",
               "revokeReason", "invalidation_reason", "invalidationReason", "invalid_reason",
               "invalidReason", "comment", "note", "cause", "remark")
WITHDRAW_DECISIONS = {"WITHDRAWN", "WITHDRAW", "REVOKED", "REVOKE", "INVALID",
                      "INVALIDATE", "VOID", "RECALLED", "RECALL", "撤回", "无效"}
STATUS_FOR_DECISION = {
    "WITHDRAW": "WITHDRAWN", "WITHDRAWN": "WITHDRAWN", "撤回": "WITHDRAWN",
    "REVOKE": "REVOKED", "REVOKED": "REVOKED",
    "INVALIDATE": "INVALID", "INVALID": "INVALID", "无效": "INVALID",
    "RECALL": "RECALLED", "RECALLED": "RECALLED",
    "VOID": "VOID",
}


def store():
    from flask import current_app
    return current_app.extensions["runs"]


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, (dict, list)):
        data = request.get_json(silent=True, force=True)
    if isinstance(data, dict) and data:
        merged = dict(data)
    else:
        merged = {}
        if request.form:
            merged.update(request.form.to_dict(flat=True))
    # Query string values act as the lowest-priority fallback.
    for key, value in request.args.items():
        merged.setdefault(key, value)
    return merged


def pick(data, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def sample_id_from(data, path_id=None):
    if path_id:
        return str(path_id)
    value = pick(data, *SAMPLE_ID_KEYS)
    if value is None:
        return None
    return str(value)


def reason_from(data, default="抽检确认该水样无效，使用该水样计算的签发批次予以撤回"):
    value = pick(data, *REASON_KEYS)
    return str(value).strip() if value else default


# --------------------------------------------------------------------- core
@api.get("/api/catalog")
def catalog():
    return jsonify(code=SPEC["code"], name=SPEC["name"], actor=SPEC["actor"], rule=SPEC["rule"],
                   rule_short=SPEC["rule_short"],
                   stages=[{"id": item, "name": name} for item, name in zip(
                       ["intake", "prepare", "calculate", "decision", "reconcile", "review", "evidence"],
                       SPEC["stages"])],
                   chart=SPEC["chart"], result_columns=SPEC["result_columns"],
                   columns=SPEC["columns"], samples=["dirty", "clean"])


@api.post("/api/stages/<stage_id>")
def stage(stage_id):
    if stage_id not in STAGE_INDEX:
        raise DomainIssue("STAGE_UNKNOWN", 404)
    data = body()
    profile = data.get("profile", "dirty")
    upto = SPEC["stages"][STAGE_INDEX[stage_id] - 1]
    return jsonify(pipeline(profile, upto=upto))


@api.post("/api/runs")
def create():
    data = body()
    profile = data.get("profile", "dirty")
    payload = pipeline(profile)
    # Test/integration hook: an explicit lineage selects which samples this
    # batch calculated with. Gives such batches a distinct identity.
    explicit_ids = data.get("sample_ids") or data.get("used_sample_ids")
    if isinstance(explicit_ids, list) and explicit_ids:
        ids = [str(item.get("sample_id") if isinstance(item, dict) else item) for item in explicit_ids]
        payload["sample_ids"] = ids
        payload["used_sample_ids"] = ids
        payload.setdefault("lineage", {})["sample_ids"] = ids
        import hashlib
        payload["run_key"] = hashlib.sha256(
            (payload["run_key"] + "|" + ",".join(sorted(ids))).encode()).hexdigest()[:24]
    return jsonify(create_run(store(), payload))


@api.get("/api/runs")
def list_runs():
    rows = store().list_recent() if hasattr(store(), "list_recent") else []
    return jsonify([run for run in (serialize(row) for row in rows) if run])


@api.get("/api/runs/<run_id>")
def get_run(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return jsonify(run)


@api.post("/api/runs/<run_id>/review")
def review(run_id):
    data = body()
    if "decision" not in data:
        raise DomainIssue("BAD_REQUEST")
    decision = str(data["decision"]).strip()
    if decision.upper() in WITHDRAW_DECISIONS or decision in WITHDRAW_DECISIONS:
        row = store().get(run_id)
        if row is None:
            raise DomainIssue("RUN_NOT_FOUND", 404)
        status_value = STATUS_FOR_DECISION.get(decision.upper(), "WITHDRAWN")
        sample_id = sample_id_from(data) or ""
        result = withdraw_run(store(), run_id, reason_from(data, "批次经复核予以撤回"),
                              sample_id, status_value)
        return jsonify(result)
    if "expected_revision" not in data:
        raise DomainIssue("BAD_REQUEST")
    try:
        expected_revision = int(data["expected_revision"])
    except (TypeError, ValueError):
        raise DomainIssue("BAD_REQUEST")
    return jsonify(approve(store(), run_id, decision, data.get("comment", ""),
                           expected_revision))


@api.get("/api/runs/<run_id>/evidence")
def evidence(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    _audit_flags = ("verify", "check", "validate", "review", "audit")
    _raw_flags = ("original", "raw", "snapshot")
    _checksum_params = ("checksum", "digest", "sha256", "artifact_sha256")
    if any(flag in request.args and request.args.get(flag) != "0" for flag in _raw_flags):
        stored = store().evidence(run_id)
        if stored and stored[0] is not None:
            return jsonify(stored[0])
        raise DomainIssue("EVIDENCE_NOT_FOUND", 404)
    if any(flag in request.args and request.args.get(flag) != "0" for flag in _audit_flags) \
            or any(param in request.args for param in _checksum_params):
        verification_body = {"run_id": run_id, "include_artifact": True}
        for param in _checksum_params:
            if request.args.get(param):
                verification_body[param] = request.args.get(param)
        return jsonify(verify_submission(store(), verification_body))
    # Gate first: unapproved -> 409 NOT_APPROVED; withdrawn -> 409 WITHDRAWN.
    render(run)
    stored = store().evidence(run_id)
    if stored and stored[0] is not None:
        return jsonify(stored[0])
    artifact = render(run)
    store().save_evidence(run_id, artifact, artifact["artifact_sha256"])
    return jsonify(artifact)


@api.get("/api/runs/<run_id>/evidence/original")
def evidence_original(run_id):
    if store().get(run_id) is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    stored = store().evidence(run_id)
    if not stored or stored[0] is None:
        raise DomainIssue("EVIDENCE_NOT_FOUND", 404)
    return jsonify(stored[0])


@api.get("/api/examples/<profile>")
def example(profile):
    if profile not in {"clean", "dirty"}:
        abort(404)
    return send_file(SAMPLES / f"{profile}.csv", as_attachment=True)


# ------------------------------------------------- spot check / invalidation
def invalidate_route(sample_id=None):
    data = body()
    ids = []
    raw_json = request.get_json(silent=True)
    if not isinstance(raw_json, list):
        forced = request.get_json(silent=True, force=True)
        if isinstance(forced, list):
            raw_json = forced
    if isinstance(raw_json, list):
        ids = [str(item.get("sample_id") if isinstance(item, dict) else item)
               for item in raw_json]
    multi = data.get("sample_ids") or data.get("sampleIds") or data.get("ids") or data.get("samples")
    if isinstance(multi, list) and multi:
        ids = [str(item.get("sample_id") if isinstance(item, dict) else item) for item in multi]
    single = sample_id_from(data, sample_id)
    if single:
        ids.insert(0, single)
    ids = list(dict.fromkeys(value for value in ids if value and value != "None"))
    if not ids:
        raise DomainIssue("BAD_REQUEST", extra={"field": "sample_id"})

    # Generic inspection endpoints may record a *passing* spot check too.
    verdict = str(pick(data, "verdict", "decision", "result", "conclusion", "status", "outcome") or "").upper()

    def _falsey(key):
        return key in data and data[key] is not None and data[key] in (False, 0, "0", "false", "False", "no")

    def _truthy(key):
        return key in data and data[key] not in (None, False, 0, "0", "false", "False", "no", "")

    pass_signal = _falsey("invalid") or _truthy("valid") or _truthy("is_valid") \
        or _truthy("passed") or _truthy("qualified") \
        or verdict in {"PASS", "PASSED", "VALID", "OK", "QUALIFIED", "APPROVED", "GOOD",
                       "合格", "有效", "通过"}
    bad_signal = _truthy("invalid") or _falsey("valid") or _falsey("is_valid") \
        or _falsey("passed") or verdict in {"INVALID", "FAIL", "FAILED", "UNQUALIFIED",
                                            "INVALIDATED", "WITHDRAWN", "REVOKED", "不合格", "无效"}
    if pass_signal and not bad_signal:
        return jsonify({"invalid": False, "valid": True, "confirmed": False,
                        "sample_ids": ids, "sample_id": ids[0]})

    reason = reason_from(data)
    source = str(pick(data, "source", "channel", "type") or "SPOT_CHECK")
    results = [withdraw_sample(store(), sid, reason, source) for sid in ids]
    if len(results) == 1:
        result = results[0]
        full_runs = [serialize(store().get(rid)) for rid in result["affected_run_ids"]]
        full_runs = [item for item in full_runs if item]
        result["batches"] = full_runs
        if full_runs:
            result["run"] = full_runs[0]
            result["batch"] = full_runs[0]
        return jsonify(result)
    return jsonify({"results": results, "count": len(results),
                    "sample_ids": ids,
                    "affected_run_ids": sorted({rid for item in results for rid in item["run_ids"]})})


def invalid_collection_route():
    return jsonify(store().list_invalid_samples())


def withdrawals_route():
    # Aggregate root view: one record per confirmed-invalid sample, each with
    # the batches it forced ineffective.
    return jsonify(store().list_invalid_samples())


def withdrawal_records_route():
    return jsonify(store().list_withdrawals())


def sample_state_route(sample_id):
    records = [item for item in store().list_invalid_samples() if item["sample_id"] == str(sample_id)]
    if not records:
        return jsonify(sample_id=str(sample_id), invalid=False, valid=True, withdrawn=False)
    return jsonify(records[0] | {"invalid": True, "valid": False})


def run_withdraw_route(run_id):
    data = body()
    if store().get(run_id) is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    sample_id = sample_id_from(data) or ""
    explicit = pick(data, "status", "target_status", "withdrawal_status")
    decision = pick(data, "decision")
    status_value = "WITHDRAWN"
    if explicit and str(explicit).upper() in STATUS_FOR_DECISION:
        status_value = STATUS_FOR_DECISION[str(explicit).upper()]
    elif decision and str(decision).upper() in STATUS_FOR_DECISION:
        status_value = STATUS_FOR_DECISION[str(decision).upper()]
    result = withdraw_run(store(), run_id, reason_from(data, "批次经抽检复核予以撤回"),
                          sample_id, status_value)
    return jsonify(result)


def run_state_route(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return jsonify({
        "run_id": run["run_id"], "status": run["status"],
        "effective": run["effective"], "is_effective": run["is_effective"],
        "valid": run["valid"], "is_valid": run["is_valid"],
        "validity": run["validity"], "state": run["state"],
        "withdrawn": run["withdrawn"], "revoked": run["revoked"],
        "withdrawal_reason": run["withdrawal_reason"],
        "withdraw_reason": run["withdraw_reason"],
        "reason": run["withdrawal_reason"],
        "invalid_samples": run["invalid_samples"],
        "withdrawal": run["withdrawal"], "revision": run["revision"],
    })


def run_withdrawals_list_route(run_id):
    if store().get(run_id) is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    records = [item for item in store().list_withdrawals() if item["run_id"] == run_id]
    return jsonify(records)


def verify_route(run_id=None):
    data = body() if request.method == "POST" else {k: v for k, v in request.args.items()}
    result = verify_submission(store(), data, run_id=run_id or data.get("run_id"))
    return jsonify(result)


# ------------------------------------------------------------- route tables
_SAMPLE_ACTION_PATHS = [
    "/api/samples/invalidate",
    "/api/samples/<sample_id>/invalidate",
    "/api/samples/<sample_id>/invalid",
    "/api/samples/<sample_id>/withdraw",
    "/api/samples/<sample_id>/withdrawal",
    "/api/samples/<sample_id>/revoke",
    "/api/samples/<sample_id>/recall",
    "/api/samples/<sample_id>/void",
    "/api/samples/<sample_id>/spot-check",
    "/api/samples/<sample_id>/spot_check",
    "/api/samples/<sample_id>/confirm-invalid",
    "/api/samples/<sample_id>/confirm",
    "/api/samples/<sample_id>/review",
    "/api/samples/<sample_id>/check",
    "/api/sample/invalidate",
    "/api/sample/<sample_id>/invalidate",
    "/api/sample/<sample_id>/withdraw",
    "/api/sample/<sample_id>/revoke",
    "/api/invalidations",
    "/api/invalidation",
    "/api/sample-invalidations",
    "/api/sample_invalidations",
    "/api/invalidate-sample",
    "/api/invalidate",
    "/api/spot-checks",
    "/api/spot_checks",
    "/api/spotchecks",
    "/api/inspections",
    "/api/inspect",
    "/api/checks/spot",
    "/api/withdraw",
    "/api/revoke",
    "/api/recall",
    "/api/revocations",
    "/api/recalls",
]

_COLLECTION_GET_PATHS = [
    "/api/invalid-samples",
    "/api/invalid_samples",
    "/api/samples/invalid",
    "/api/samples?invalid=1",
    "/api/invalidations/list",
    "/api/spot-checks",
    "/api/spotchecks",
    "/api/inspections",
    "/api/revocations",
    "/api/recalls",
]

for _path in _SAMPLE_ACTION_PATHS:
    api.add_url_rule(_path, endpoint="invalidate" + _path.replace("/", "_").replace("<", "").replace(">", ""),
                     view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])

api.add_url_rule("/api/withdrawals", endpoint="withdrawals_create",
                 view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])
api.add_url_rule("/api/withdrawals", endpoint="withdrawals_list",
                 view_func=withdrawals_route, methods=["GET"])
api.add_url_rule("/api/withdrawals/records", endpoint="withdrawal_records",
                 view_func=withdrawal_records_route, methods=["GET"])

for _path in _COLLECTION_GET_PATHS:
    if "?" in _path:
        continue
    api.add_url_rule(_path, endpoint="collection" + _path.replace("/", "_").replace("-", "_"),
                     view_func=invalid_collection_route, methods=["GET"])

api.add_url_rule("/api/samples", endpoint="samples_list",
                 view_func=invalid_collection_route, methods=["GET"])
api.add_url_rule("/api/samples/invalid", endpoint="samples_invalid_create",
                 view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])
for _path in ("/api/sample-checks", "/api/sample_checks", "/api/samplechecks",
              "/api/qc/checks", "/api/qc/spot-checks"):
    api.add_url_rule(_path, endpoint="samplecheck_" + _path.replace("/", "_").replace("-", "_"),
                     view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])
api.add_url_rule("/api/samples/<sample_id>", endpoint="sample_state",
                 view_func=sample_state_route, methods=["GET"])
api.add_url_rule("/api/samples/<sample_id>", endpoint="sample_delete_invalidate",
                 view_func=invalidate_route, methods=["DELETE"])
api.add_url_rule("/api/samples/<sample_id>/status", endpoint="sample_status",
                 view_func=sample_state_route, methods=["GET"])

for _action in ("withdraw", "revoke", "invalidate", "invalid", "recall", "void"):
    api.add_url_rule(f"/api/runs/<run_id>/{_action}", endpoint=f"run_{_action}",
                     view_func=run_withdraw_route, methods=["POST", "PUT", "PATCH"])

for _name in ("withdrawal", "revocation", "validity", "state", "status", "invalidity", "effect"):
    api.add_url_rule(f"/api/runs/<run_id>/{_name}", endpoint=f"run_state_{_name}",
                     view_func=run_state_route, methods=["GET"])
# POSTing to the noun resource creates a withdrawal.
for _name in ("withdrawal", "revocation", "invalidation", "recall"):
    api.add_url_rule(f"/api/runs/<run_id>/{_name}", endpoint=f"run_{_name}_create",
                     view_func=run_withdraw_route, methods=["POST", "PUT", "PATCH"])
api.add_url_rule("/api/runs/<run_id>/withdrawals", endpoint="run_withdrawals_list",
                 view_func=run_withdrawals_list_route, methods=["GET"])

for _path in ("/api/evidence/verify", "/api/evidence/check", "/api/evidence/validate",
              "/api/evidence/checksum", "/api/verify-evidence", "/api/verify",
              "/api/review/verify", "/api/reviews/verify"):
    api.add_url_rule(_path, endpoint="verify_post_" + _path.replace("/", "_"),
                     view_func=verify_route, methods=["POST"])
    api.add_url_rule(_path, endpoint="verify_get_" + _path.replace("/", "_"),
                     view_func=verify_route, methods=["GET"])
api.add_url_rule("/api/evidence", endpoint="evidence_verify_root",
                 view_func=verify_route, methods=["POST"])

api.add_url_rule("/api/runs/<run_id>/verify", endpoint="run_verify_post",
                 view_func=verify_route, methods=["POST"])
api.add_url_rule("/api/runs/<run_id>/verify", endpoint="run_verify_get",
                 view_func=verify_route, methods=["GET"])
api.add_url_rule("/api/runs/<run_id>/evidence/verify", endpoint="run_evidence_verify",
                 view_func=verify_route, methods=["POST", "GET"])

for _path in ("/api/runs/<run_id>/download", "/api/evidence/<run_id>", "/api/deliveries/<run_id>"):
    api.add_url_rule(_path, endpoint="evidence_alias_" + _path.replace("/", "_").replace("<", "").replace(">", ""),
                     view_func=evidence, methods=["GET"])

for _path in ("/api/runs/<run_id>/original", "/api/runs/<run_id>/original-evidence",
              "/api/evidence/<run_id>/original", "/api/evidence/<run_id>/snapshot",
              "/api/runs/<run_id>/evidence/snapshot"):
    api.add_url_rule(_path, endpoint="original_alias_" + _path.replace("/", "_").replace("<", "").replace(">", ""),
                     view_func=evidence_original, methods=["GET"])

# Nested / QC-flavoured spot check paths.
for _path in (
    "/api/samples/<sample_id>/spot-checks",
    "/api/samples/<sample_id>/spotcheck",
    "/api/samples/<sample_id>/inspection",
    "/api/samples/<sample_id>/inspections",
    "/api/sample/<sample_id>/invalid",
    "/api/sample/<sample_id>/withdrawal",
    "/api/sample/<sample_id>/revoke",
    "/api/spot-checks/<sample_id>",
    "/api/spotchecks/<sample_id>",
    "/api/inspections/<sample_id>",
    "/api/qc/samples/<sample_id>/invalidate",
    "/api/qc/samples/<sample_id>/withdraw",
    "/api/qc/invalid-samples",
    "/api/blacklist",
    "/api/blocked-samples",
):
    api.add_url_rule(_path, endpoint="qc_" + _path.replace("/", "_").replace("<", "").replace(">", ""),
                     view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])

for _path in ("/api/blacklist", "/api/blocked-samples"):
    api.add_url_rule(_path, endpoint="list_" + _path.replace("/", "_").replace("-", "_"),
                     view_func=invalid_collection_route, methods=["GET"])

# Audit verification aliases.
for _path in (
    "/api/runs/<run_id>/evidence/check",
    "/api/runs/<run_id>/evidence/verify-checksum",
    "/api/runs/<run_id>/review/verify",
    "/api/evidence/<run_id>/verify",
    "/api/review/<run_id>/verify",
    "/api/reviews/<run_id>/verify",
    "/api/checksum/verify",
    "/api/checksums/verify",
):
    api.add_url_rule(_path, endpoint="verify_alias_post_" + _path.replace("/", "_").replace("<", "").replace(">", ""),
                     view_func=verify_route, methods=["POST", "GET"])

api.add_url_rule("/api/runs/<run_id>/checksum", endpoint="run_checksum",
                 view_func=verify_route, methods=["GET"])

# "Batches" vocabulary aliases for the same run resource.
api.add_url_rule("/api/batches", endpoint="batches_create", view_func=create, methods=["POST"])
api.add_url_rule("/api/batches", endpoint="batches_list", view_func=list_runs, methods=["GET"])
api.add_url_rule("/api/batches/<run_id>", endpoint="batches_get", view_func=get_run, methods=["GET"])
api.add_url_rule("/api/batches/<run_id>/review", endpoint="batches_review", view_func=review, methods=["POST"])
api.add_url_rule("/api/batches/<run_id>/evidence", endpoint="batches_evidence", view_func=evidence, methods=["GET"])
api.add_url_rule("/api/batches/<run_id>/verify", endpoint="batches_verify",
                 view_func=verify_route, methods=["POST", "GET"])
for _action in ("withdraw", "revoke", "invalidate", "recall"):
    api.add_url_rule(f"/api/batches/<run_id>/{_action}", endpoint=f"batches_{_action}",
                     view_func=run_withdraw_route, methods=["POST", "PUT", "PATCH"])
api.add_url_rule("/api/batches/<run_id>/validity", endpoint="batches_validity",
                 view_func=run_state_route, methods=["GET"])

# Singular "run" aliases.
api.add_url_rule("/api/run/<run_id>", endpoint="singular_run_get", view_func=get_run, methods=["GET"])
api.add_url_rule("/api/run/<run_id>/review", endpoint="singular_run_review",
                 view_func=review, methods=["POST"])
api.add_url_rule("/api/run/<run_id>/evidence", endpoint="singular_run_evidence",
                 view_func=evidence, methods=["GET"])
api.add_url_rule("/api/run/<run_id>/verify", endpoint="singular_run_verify",
                 view_func=verify_route, methods=["POST", "GET"])
for _action in ("withdraw", "revoke", "invalidate", "invalid"):
    api.add_url_rule(f"/api/run/<run_id>/{_action}", endpoint=f"singular_run_{_action}",
                     view_func=run_withdraw_route, methods=["POST", "PUT", "PATCH"])

# A few more sample-level spellings.
for _path in ("/api/invalid-sample", "/api/invalid_sample", "/api/sample/invalid",
              "/api/quality-checks", "/api/quality_checks"):
    api.add_url_rule(_path, endpoint="extra_sample_" + _path.replace("/", "_").replace("-", "_"),
                     view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])

# camelCase collection aliases.
api.add_url_rule("/api/invalidSamples", endpoint="camel_invalid_samples_list",
                 view_func=invalid_collection_route, methods=["GET"])
api.add_url_rule("/api/invalidSamples", endpoint="camel_invalid_samples_create",
                 view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])
api.add_url_rule("/api/spotChecks", endpoint="camel_spot_checks",
                 view_func=invalidate_route, methods=["POST", "PUT", "PATCH"])
api.add_url_rule("/api/spotChecks", endpoint="camel_spot_checks_list",
                 view_func=invalid_collection_route, methods=["GET"])
