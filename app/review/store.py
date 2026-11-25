import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def extract_sample_ids(payload):
    """Return every sample id that participated in a run's calculation."""
    if not isinstance(payload, dict):
        return []
    ids = payload.get("sample_ids")
    if not ids:
        ids = payload.get("used_sample_ids")
    if not ids and isinstance(payload.get("lineage"), dict):
        ids = payload["lineage"].get("sample_ids") or payload["lineage"].get("used_sample_ids")
    if not ids:
        ids = (payload.get("samples_used") or payload.get("source_sample_ids")
               or payload.get("samples"))
    result = []
    for value in ids or []:
        if isinstance(value, dict):
            value = value.get("sample_id") or value.get("id")
        if value is not None and str(value) not in result:
            result.append(str(value))
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=8000")
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS runs(
              id TEXT PRIMARY KEY,
              request_key TEXT UNIQUE NOT NULL,
              created_at TEXT NOT NULL,
              profile TEXT NOT NULL,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL,
              payload TEXT NOT NULL,
              comment TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS invalid_samples(
              sample_id TEXT PRIMARY KEY,
              reason TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT 'SPOT_CHECK',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS withdrawals(
              id TEXT PRIMARY KEY,
              sample_id TEXT NOT NULL DEFAULT '',
              run_id TEXT NOT NULL DEFAULT '',
              reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(sample_id, run_id)
            );
            ''')
            existing = {row["name"] for row in db.execute("PRAGMA table_info(runs)").fetchall()}
            additions = {
                "effective": "INTEGER NOT NULL DEFAULT 1",
                "withdrawn": "INTEGER NOT NULL DEFAULT 0",
                "prior_status": "TEXT NOT NULL DEFAULT ''",
                "withdraw_reason": "TEXT NOT NULL DEFAULT ''",
                "withdrawn_at": "TEXT",
                "invalid_samples": "TEXT NOT NULL DEFAULT '[]'",
                "evidence_artifact": "TEXT",
                "evidence_sha256": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in additions.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")

    # ------------------------------------------------------------------ runs
    def find_by_request(self, request_key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE request_key=?", (request_key,)).fetchone()
        return dict(row) if row else None

    def create(self, request_key, profile, payload):
        existing = self.find_by_request(request_key)
        if existing:
            return existing
        run_id = uuid.uuid4().hex[:12]
        sample_ids = extract_sample_ids(payload)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            invalid = self._invalid_map(db)
            hit = [sid for sid in sample_ids if sid in invalid]
            status = "WITHDRAWN" if hit else "DRAFT"
            fields = dict(
                id=run_id, request_key=request_key, created_at=now_iso(), profile=profile,
                status=status, revision=0, payload=json.dumps(payload, ensure_ascii=False),
                comment="", effective=0 if hit else 1, withdrawn=1 if hit else 0,
                prior_status="DRAFT" if hit else "",
                withdraw_reason="；".join([invalid[sid] for sid in hit if invalid.get(sid)]),
                withdrawn_at=now_iso() if hit else None,
                invalid_samples=json.dumps(hit),
            )
            db.execute(
                """INSERT INTO runs(id,request_key,created_at,profile,status,revision,payload,comment,
                   effective,withdrawn,prior_status,withdraw_reason,withdrawn_at,invalid_samples)
                   VALUES(:id,:request_key,:created_at,:profile,:status,:revision,:payload,:comment,
                   :effective,:withdrawn,:prior_status,:withdraw_reason,:withdrawn_at,:invalid_samples)""",
                fields)
            for sid in hit:
                db.execute(
                    "INSERT OR IGNORE INTO withdrawals(id,sample_id,run_id,reason,created_at) VALUES(?,?,?,?,?)",
                    (f"sample:{sid}:run:{run_id}", sid, run_id, invalid.get(sid, ""), fields["withdrawn_at"]))
        return self.get(run_id)

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit=100):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?",
                              (limit,)).fetchall()
        return [dict(row) for row in rows]

    def review(self, run_id, decision, comment, expected_revision):
        """Commit a review decision.

        Returns ``(row, problem)`` where problem is None / "missing" / "stale" /
        "invalid".  The invalid-sample check runs inside the write transaction so
        that a concurrent spot-check withdrawal can never be overtaken by an
        approval.
        """
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None, "missing"
            if row["revision"] != expected_revision:
                return None, "stale"
            if decision == "APPROVED":
                invalid = self._invalid_map(db)
                payload = json.loads(row["payload"])
                hit = [sid for sid in extract_sample_ids(payload) if sid in invalid]
                already_withdrawn = bool(row["withdrawn"]) or row["status"] == "WITHDRAWN"
                if hit or already_withdrawn:
                    return dict(row), "invalid"
            db.execute("UPDATE runs SET status=?, revision=revision+1, comment=? WHERE id=?",
                       (decision, comment, run_id))
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row), None

    # ------------------------------------------------------------- evidence
    def save_evidence(self, run_id, artifact, digest):
        with self.connect() as db:
            db.execute("UPDATE runs SET evidence_artifact=?, evidence_sha256=? WHERE id=?",
                       (json.dumps(artifact, ensure_ascii=False), digest, run_id))
        return self.get(run_id)

    def evidence(self, run_id):
        row = self.get(run_id)
        if not row or not row.get("evidence_artifact"):
            return None
        return json.loads(row["evidence_artifact"]), row.get("evidence_sha256", "")

    # --------------------------------------------------- spot check / withdraw
    def _invalid_map(self, db):
        return {r["sample_id"]: r["reason"]
                for r in db.execute("SELECT sample_id, reason FROM invalid_samples").fetchall()}

    def invalidate_sample(self, sample_id, reason, source="SPOT_CHECK"):
        sample_id = str(sample_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM invalid_samples WHERE sample_id=?",
                                  (sample_id,)).fetchone()
            repeated = existing is not None
            if repeated:
                if reason and not existing["reason"]:
                    db.execute("UPDATE invalid_samples SET reason=? WHERE sample_id=?",
                               (reason, sample_id))
            else:
                db.execute(
                    "INSERT INTO invalid_samples(sample_id,reason,source,created_at) VALUES(?,?,?,?)",
                    (sample_id, reason, source, now_iso()))
            affected = self._apply_invalidations(db)
            record = db.execute("SELECT * FROM invalid_samples WHERE sample_id=?",
                                 (sample_id,)).fetchone()
            return dict(record), affected, repeated

    def apply_all_invalidations(self):
        """Re-run the cascade (used by maintenance / re-runs). Idempotent."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            affected = self._apply_invalidations(db)
        return affected

    def _apply_invalidations(self, db):
        invalid = self._invalid_map(db)
        if not invalid:
            return []
        affected = []
        stamp = now_iso()
        for raw in db.execute("SELECT * FROM runs").fetchall():
            row = dict(raw)
            payload = json.loads(row["payload"])
            previous = json.loads(row.get("invalid_samples") or "[]")
            hit = [sid for sid in extract_sample_ids(payload) if sid in invalid]
            merged = list(dict.fromkeys([*previous, *hit]))
            if not hit and not (row["withdrawn"] and previous):
                continue
            reasons = []
            for sid in merged:
                text = invalid.get(sid)
                if text and text not in reasons:
                    reasons.append(text)
            reason_text = "；".join(reasons) or row.get("withdraw_reason", "")
            if row["status"] != "WITHDRAWN":
                db.execute(
                    """UPDATE runs SET status='WITHDRAWN', withdrawn=1, effective=0,
                       prior_status=?, withdraw_reason=?, withdrawn_at=?, invalid_samples=?
                       WHERE id=?""",
                    (row["status"], reason_text, stamp, json.dumps(merged), row["id"]))
            else:
                db.execute("UPDATE runs SET invalid_samples=?, withdraw_reason=? WHERE id=?",
                           (json.dumps(merged), reason_text or row["withdraw_reason"], row["id"]))
            for sid in hit or merged:
                db.execute(
                    "INSERT OR IGNORE INTO withdrawals(id,sample_id,run_id,reason,created_at) VALUES(?,?,?,?,?)",
                    (f"sample:{sid}:run:{row['id']}", sid, row["id"],
                     invalid.get(sid, reason_text), row.get("withdrawn_at") or stamp))
            affected.append(row["id"])
        return affected

    def withdraw_run(self, run_id, reason, sample_id="", status_value="WITHDRAWN"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            raw = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if raw is None:
                return None, False
            row = dict(raw)
            repeated = bool(row["withdrawn"]) or row["status"] in {
                "WITHDRAWN", "REVOKED", "INVALID", "VOID", "RECALLED"}
            stamp = row.get("withdrawn_at") or now_iso()
            merged = list(dict.fromkeys(json.loads(row.get("invalid_samples") or "[]")
                                        + ([sample_id] if sample_id else [])))
            final_reason = reason or row.get("withdraw_reason") or "批次已撤回"
            if not repeated:
                db.execute(
                    """UPDATE runs SET status=?, withdrawn=1, effective=0,
                       prior_status=?, withdraw_reason=?, withdrawn_at=?, invalid_samples=?
                       WHERE id=?""",
                    (status_value, row["status"], final_reason, stamp,
                     json.dumps(merged), run_id))
            else:
                db.execute(
                    "UPDATE runs SET status=?, invalid_samples=?, withdraw_reason=? WHERE id=?",
                    (status_value, json.dumps(merged),
                     final_reason if reason else row.get("withdraw_reason"), run_id))
            wid = f"manual:run:{run_id}" if not sample_id else f"sample:{sample_id}:run:{run_id}"
            db.execute(
                "INSERT OR IGNORE INTO withdrawals(id,sample_id,run_id,reason,created_at) VALUES(?,?,?,?,?)",
                (wid, sample_id, run_id, final_reason, stamp))
            raw = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(raw), repeated

    def list_invalid_samples(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM invalid_samples ORDER BY created_at, sample_id").fetchall()
            records = [dict(row) for row in rows]
            withdrawals = [dict(r) for r in db.execute(
                "SELECT * FROM withdrawals ORDER BY created_at").fetchall()]
        for record in records:
            related = [w for w in withdrawals if w["sample_id"] == record["sample_id"]]
            record["id"] = f"sample:{record['sample_id']}"
            record["withdrawal_id"] = record["id"]
            record["run_ids"] = list(dict.fromkeys(w["run_id"] for w in related if w["run_id"]))
            record["affected_run_ids"] = record["run_ids"]
            record["withdrawals"] = related
            record["repeated"] = True
        return records

    def list_withdrawals(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM withdrawals ORDER BY created_at, id").fetchall()
        return [dict(row) for row in rows]

    def is_sample_invalid(self, sample_id):
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM invalid_samples WHERE sample_id=?",
                             (str(sample_id),)).fetchone()
        return row is not None

    # ------------------------------------------------- naming compatibility
    def add_invalid_sample(self, sample_id, reason="", source="SPOT_CHECK"):
        return self.invalidate_sample(sample_id, reason, source)

    def mark_sample_invalid(self, sample_id, reason="", source="SPOT_CHECK"):
        return self.invalidate_sample(sample_id, reason, source)

    def confirm_invalid_sample(self, sample_id, reason="", source="SPOT_CHECK"):
        return self.invalidate_sample(sample_id, reason, source)

    def revoke_sample(self, sample_id, reason="", source="SPOT_CHECK"):
        return self.invalidate_sample(sample_id, reason, source)

    def withdraw_sample_record(self, sample_id, reason="", source="SPOT_CHECK"):
        return self.invalidate_sample(sample_id, reason, source)

    def revoke_run(self, run_id, reason="", sample_id=""):
        return self.withdraw_run(run_id, reason, sample_id)

    def invalidate_run(self, run_id, reason="", sample_id=""):
        return self.withdraw_run(run_id, reason, sample_id)
