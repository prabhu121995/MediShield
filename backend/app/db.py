"""
MySQL access (pymysql). One short-lived connection per call keeps things simple;
move to a pool when load requires it.

Tables: member, kyc, claim, clinical_event, case_record, patient, policy_document,
app_user. Audit columns (created_by/created_at, updated_by/updated_at) are on
patient and case_record.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Protocol

import pymysql
from pymysql.cursors import DictCursor

from app import config

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS member (
        member_id     VARCHAR(32) PRIMARY KEY,
        full_name     VARCHAR(120) NOT NULL,
        date_of_birth DATE NULL,
        policy_number VARCHAR(32) NOT NULL,
        plan          VARCHAR(16) NULL,
        INDEX ix_member_policy (policy_number)
    )""",
    """CREATE TABLE IF NOT EXISTS kyc (
        member_id      VARCHAR(32) PRIMARY KEY,
        id_type        VARCHAR(32) NULL,
        id_expiry_date DATE NULL,
        id_status      VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
    )""",
    """CREATE TABLE IF NOT EXISTS claim (
        case_id        VARCHAR(36) PRIMARY KEY,
        member_id      VARCHAR(32) NULL,
        patient_name   VARCHAR(120) NULL,
        service_date   DATE NULL,
        claim_amount   DECIMAL(12,2) NULL,
        icd10_codes    JSON NULL,
        cpt_codes      JSON NULL,
        provider_npi   VARCHAR(10) NULL,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX ix_claim_member (member_id)
    )""",
    """CREATE TABLE IF NOT EXISTS clinical_event (
        id                INT AUTO_INCREMENT PRIMARY KEY,
        case_id           VARCHAR(36) NOT NULL,
        member_id         VARCHAR(32) NULL,
        admission_date    DATE NULL,
        discharge_date    DATE NULL,
        prescription_date DATE NULL,
        diagnosis         VARCHAR(255) NULL,
        INDEX ix_clinical_member (member_id)
    )""",
    """CREATE TABLE IF NOT EXISTS case_record (
        case_id      VARCHAR(36) PRIMARY KEY,
        file_name    VARCHAR(255) NOT NULL,
        doc_type     VARCHAR(32) NULL,
        status       VARCHAR(24) NOT NULL,
        decision     VARCHAR(12) NULL,
        confidence   DECIMAL(4,3) NULL,
        needs_review BOOLEAN NOT NULL DEFAULT FALSE,
        result       JSON NULL,
        override     JSON NULL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS app_user (
        username      VARCHAR(64) PRIMARY KEY,
        password_hash VARCHAR(255) NOT NULL,
        role          ENUM('admin','agent') NOT NULL,
        full_name     VARCHAR(120) NOT NULL,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS patient (
        patient_id    VARCHAR(36) PRIMARY KEY,
        member_id     VARCHAR(32) NULL,
        full_name     VARCHAR(120) NOT NULL,
        date_of_birth DATE NULL,
        gender        VARCHAR(16) NULL,
        phone         VARCHAR(32) NULL,
        email         VARCHAR(255) NULL,
        address       VARCHAR(255) NULL,
        policy_number VARCHAR(32) NULL,
        created_by    VARCHAR(64) NOT NULL,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by    VARCHAR(64) NULL,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX ix_patient_name (full_name),
        INDEX ix_patient_policy (policy_number)
    )""",
    """CREATE TABLE IF NOT EXISTS claim_bundle (
        bundle_id   VARCHAR(36) PRIMARY KEY,
        title       VARCHAR(160) NOT NULL,
        patient_id  VARCHAR(36) NULL,
        created_by  VARCHAR(64) NOT NULL,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_by  VARCHAR(64) NULL,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX ix_bundle_patient (patient_id)
    )""",
    """CREATE TABLE IF NOT EXISTS audit_event (
        id          BIGINT AUTO_INCREMENT PRIMARY KEY,
        entity_type VARCHAR(16) NOT NULL DEFAULT 'claim',
        entity_id   VARCHAR(64) NOT NULL DEFAULT '',
        claim_id    VARCHAR(36) NULL,
        doc_id      VARCHAR(36) NULL,
        actor       VARCHAR(64) NOT NULL,
        event       VARCHAR(48) NOT NULL,
        status      VARCHAR(16) NOT NULL,
        detail      JSON NULL,
        duration_ms INT NULL,
        created_at  DATETIME(3) NOT NULL,
        INDEX ix_audit_claim (claim_id, id)
    )""",
    # LangGraph checkpoints (MySQLSaver): an escalated claim waits here for a human.
    """CREATE TABLE IF NOT EXISTS lg_checkpoint (
        thread_id     VARCHAR(80) NOT NULL,
        checkpoint_ns VARCHAR(255) NOT NULL DEFAULT '',
        checkpoint_id VARCHAR(64) NOT NULL,
        parent_id     VARCHAR(64) NULL,
        type          VARCHAR(32) NOT NULL,
        checkpoint    LONGBLOB NOT NULL,
        meta_type     VARCHAR(32) NOT NULL,
        metadata      LONGBLOB NOT NULL,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
    )""",
    """CREATE TABLE IF NOT EXISTS lg_checkpoint_blob (
        thread_id     VARCHAR(80) NOT NULL,
        checkpoint_ns VARCHAR(255) NOT NULL DEFAULT '',
        channel       VARCHAR(128) NOT NULL,
        version       VARCHAR(64) NOT NULL,
        type          VARCHAR(32) NOT NULL,
        value         LONGBLOB NULL,
        PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
    )""",
    """CREATE TABLE IF NOT EXISTS lg_checkpoint_write (
        thread_id     VARCHAR(80) NOT NULL,
        checkpoint_ns VARCHAR(255) NOT NULL DEFAULT '',
        checkpoint_id VARCHAR(64) NOT NULL,
        task_id       VARCHAR(64) NOT NULL,
        idx           INT NOT NULL,
        channel       VARCHAR(128) NOT NULL,
        type          VARCHAR(32) NOT NULL,
        value         LONGBLOB NOT NULL,
        task_path     VARCHAR(255) NOT NULL DEFAULT '',
        PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
    )""",
    # Model outputs per page (transcription, document type, fields), keyed by input + model + prompt.
    """CREATE TABLE IF NOT EXISTS model_cache (
        cache_key  CHAR(80) PRIMARY KEY,
        kind       VARCHAR(16) NOT NULL,
        model      VARCHAR(128) NOT NULL,
        payload    JSON NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS policy_document (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        filename    VARCHAR(255) NOT NULL,
        chunks      INT NOT NULL,
        ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
]

# Columns added after a table already existed. CREATE TABLE IF NOT EXISTS does not
# alter an existing table, so init_db() adds each missing column once.
MIGRATIONS = [
    ("case_record", "patient_id", "patient_id VARCHAR(36) NULL, ADD INDEX ix_case_patient (patient_id)"),
    ("case_record", "created_by", "created_by VARCHAR(64) NULL"),
    ("case_record", "updated_by", "updated_by VARCHAR(64) NULL"),
    ("case_record", "bundle_id", "bundle_id VARCHAR(36) NULL, ADD INDEX ix_case_bundle (bundle_id)"),
    # Claim-level outcome: one decision for all documents of a claim (claim graph).
    ("claim_bundle", "status", "status VARCHAR(24) NULL, ADD INDEX ix_bundle_status (status)"),
    ("claim_bundle", "decision", "decision VARCHAR(12) NULL"),
    ("claim_bundle", "confidence", "confidence DECIMAL(4,3) NULL"),
    ("claim_bundle", "needs_review", "needs_review BOOLEAN NOT NULL DEFAULT FALSE"),
    ("claim_bundle", "result", "result JSON NULL"),
    ("claim_bundle", "override", "override JSON NULL"),
    # The audit log covers every entity, not only claims.
    ("audit_event", "entity_type", "entity_type VARCHAR(16) NOT NULL DEFAULT 'claim' AFTER id"),
    ("audit_event", "entity_id", "entity_id VARCHAR(64) NOT NULL DEFAULT '' AFTER entity_type, "
                                 "ADD INDEX ix_audit_entity (entity_type, entity_id, id)"),
]
# Statements run after the column migrations (each is idempotent).
POST_MIGRATIONS = [
    "UPDATE audit_event SET entity_id = claim_id WHERE entity_id = '' AND claim_id IS NOT NULL",
    "ALTER TABLE audit_event MODIFY claim_id VARCHAR(36) NULL",
]


def _connect(database: str | None) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT, user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD, database=database, autocommit=True,
        cursorclass=DictCursor, charset="utf8mb4", connect_timeout=config.MYSQL_CONNECT_TIMEOUT_S,
    )


def init_db() -> None:
    conn = _connect(None)
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{config.MYSQL_DB}` CHARACTER SET utf8mb4")
    conn.close()
    conn = _connect(config.MYSQL_DB)
    with conn.cursor() as cur:
        for stmt in SCHEMA:
            cur.execute(stmt)
        for table, column, ddl in MIGRATIONS:
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                        (config.MYSQL_DB, table, column))
            if cur.fetchone()["n"] == 0:
                cur.execute(f"ALTER TABLE `{table}` ADD COLUMN {ddl}")
        for stmt in POST_MIGRATIONS:
            cur.execute(stmt)
    conn.close()


def _fetch(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    conn = _connect(config.MYSQL_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def _execute(sql: str, params: tuple = ()) -> None:
    conn = _connect(config.MYSQL_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    finally:
        conn.close()


def _json_list(value: Any) -> list:
    if value is None:
        return []
    return json.loads(value) if isinstance(value, str) else list(value)


class Repository(Protocol):
    def find_member(self, member_id: str) -> dict | None: ...
    def find_member_by_policy(self, policy_number: str) -> dict | None: ...
    def find_member_by_name_dob(self, full_name: str, dob: date) -> dict | None: ...
    def find_members_by_name(self, full_name: str) -> list[dict]: ...
    def kyc_record(self, member_id: str) -> dict | None: ...
    def claims_for_member(self, member_id: str, exclude_case: str | None) -> list[dict]: ...
    def clinical_events_for_member(self, member_id: str, exclude_case: str | None) -> list[dict]: ...


class MySQLRepository:
    _MEMBER = "member_id, full_name, date_of_birth, policy_number, plan"

    def find_member(self, member_id):
        rows = _fetch(f"SELECT {self._MEMBER} FROM member WHERE member_id=%s", (member_id,))
        return rows[0] if rows else None

    def find_member_by_policy(self, policy_number):
        rows = _fetch(f"SELECT {self._MEMBER} FROM member WHERE policy_number=%s",
                      (policy_number,))
        return rows[0] if rows else None

    def find_member_by_name_dob(self, full_name, dob):
        rows = _fetch(f"SELECT {self._MEMBER} FROM member "
                      "WHERE LOWER(full_name)=LOWER(%s) AND date_of_birth=%s", (full_name, dob))
        return rows[0] if len(rows) == 1 else None      # ambiguous -> no match

    def find_members_by_name(self, full_name):
        """Members whose name has the same words, ignoring case and middle initials."""
        from app.agents._util import name_tokens
        wanted = name_tokens(full_name)
        if not wanted:
            return []
        longest = max(wanted, key=len)
        rows = _fetch(f"SELECT {self._MEMBER} FROM member WHERE LOWER(full_name) LIKE %s", (f"%{longest}%",))
        return [r for r in rows if name_tokens(r["full_name"]) == wanted]

    def kyc_record(self, member_id):
        rows = _fetch("SELECT member_id, id_type, id_expiry_date, id_status "
                      "FROM kyc WHERE member_id=%s", (member_id,))
        return rows[0] if rows else None

    def claims_for_member(self, member_id, exclude_case):
        rows = _fetch("SELECT case_id, member_id, service_date, claim_amount, cpt_codes, "
                      "icd10_codes, provider_npi FROM claim "
                      "WHERE member_id=%s AND case_id<>%s", (member_id, exclude_case or ""))
        for r in rows:
            r["cpt_codes"] = _json_list(r["cpt_codes"])
            r["icd10_codes"] = _json_list(r["icd10_codes"])
            if r["claim_amount"] is not None:
                r["claim_amount"] = float(r["claim_amount"])
        return rows

    def clinical_events_for_member(self, member_id, exclude_case):
        return _fetch("SELECT case_id, admission_date, discharge_date, prescription_date, "
                      "diagnosis FROM clinical_event WHERE member_id=%s AND case_id<>%s",
                      (member_id, exclude_case or ""))


def save_claim(case_id: str, data: dict) -> None:
    _execute(
        "INSERT INTO claim (case_id, member_id, patient_name, service_date, claim_amount, "
        "icd10_codes, cpt_codes, provider_npi) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE member_id=VALUES(member_id), service_date=VALUES(service_date), "
        "claim_amount=VALUES(claim_amount), icd10_codes=VALUES(icd10_codes), "
        "cpt_codes=VALUES(cpt_codes), provider_npi=VALUES(provider_npi)",
        (case_id, data.get("member_id"), data.get("patient_name"), data.get("service_date"),
         data.get("claim_amount"), json.dumps(data.get("icd10_codes") or []),
         json.dumps([c["code"] for c in data.get("cpt_codes") or []]),
         data.get("provider_npi")),
    )


def save_clinical_event(case_id: str, data: dict) -> None:
    _execute("DELETE FROM clinical_event WHERE case_id=%s", (case_id,))
    _execute(
        "INSERT INTO clinical_event (case_id, member_id, admission_date, discharge_date, "
        "prescription_date, diagnosis) VALUES (%s,%s,%s,%s,%s,%s)",
        (case_id, data.get("member_id"), data.get("admission_date"),
         data.get("discharge_date"), data.get("prescription_date"), data.get("diagnosis")),
    )


def save_case(record: dict) -> None:
    # patient_id and created_by are set once, on insert. updated_by only moves when a
    # human (or the retry path) supplies one, so a pipeline re-run keeps the reviewer.
    _execute(
        "INSERT INTO case_record (case_id, file_name, doc_type, status, decision, confidence, "
        "needs_review, result, override, patient_id, created_by, updated_by, bundle_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE doc_type=VALUES(doc_type), status=VALUES(status), "
        "decision=VALUES(decision), confidence=VALUES(confidence), "
        "needs_review=VALUES(needs_review), result=VALUES(result), override=VALUES(override), "
        "updated_by=COALESCE(VALUES(updated_by), updated_by)",
        (record["case_id"], record["file_name"], record.get("doc_type"), record["status"],
         record.get("decision"), record.get("confidence"), record.get("needs_review", False),
         json.dumps(record.get("result"), default=str),
         json.dumps(record.get("override"), default=str) if record.get("override") else None,
         record.get("patient_id"), record.get("created_by"), record.get("updated_by"),
         record.get("bundle_id")),
    )


def _case_row(row: dict) -> dict:
    for key in ("result", "override"):
        if isinstance(row.get(key), str):
            row[key] = json.loads(row[key])
    row["needs_review"] = bool(row["needs_review"])
    if row.get("confidence") is not None:
        row["confidence"] = float(row["confidence"])
    return row


def get_case(case_id: str) -> dict | None:
    rows = _fetch("SELECT * FROM case_record WHERE case_id=%s", (case_id,))
    return _case_row(rows[0]) if rows else None


def list_cases(limit: int = 200, decision: str | None = None,
               status: str | None = None, patient_id: str | None = None,
               bundle_id: str | None = None) -> list[dict]:
    clauses, args = [], []
    if decision:
        clauses.append("decision=%s"); args.append(decision)
    if status:
        clauses.append("status=%s"); args.append(status)
    if patient_id:
        clauses.append("patient_id=%s"); args.append(patient_id)
    if bundle_id:
        clauses.append("bundle_id=%s"); args.append(bundle_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params = [*args, limit]
    rows = _fetch("SELECT case_id, file_name, doc_type, status, decision, confidence, "
                  f"needs_review, patient_id, bundle_id, created_by, created_at, updated_at FROM case_record {where} "
                  "ORDER BY created_at DESC LIMIT %s", tuple(params))
    for r in rows:
        r["needs_review"] = bool(r["needs_review"])
        r["confidence"] = float(r["confidence"]) if r["confidence"] is not None else None
    return rows


def dashboard_stats() -> dict:
    """Aggregates for the dashboard KPI cards. One query, so the numbers agree with each other."""
    row = _fetch(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(DATE(created_at)=CURDATE()), 0) AS today, "
        "COALESCE(SUM(status='PROCESSING'), 0) AS processing, "
        "COALESCE(SUM(status='FAILED'), 0) AS failed, "
        "COALESCE(SUM(decision='APPROVE'), 0) AS approved, "
        "COALESCE(SUM(decision='REJECT'), 0) AS rejected, "
        "COALESCE(SUM(decision='ESCALATE'), 0) AS escalated, "
        "COALESCE(SUM(status='REVIEWED'), 0) AS reviewed, "
        "AVG(confidence) AS avg_confidence "
        "FROM case_record"
    )[0]
    patients = _fetch("SELECT COUNT(*) AS n FROM patient")[0]["n"]
    decided = int(row["approved"]) + int(row["rejected"]) + int(row["escalated"])
    return {
        "total": int(row["total"]),
        "today": int(row["today"]),
        "processing": int(row["processing"]),
        "failed": int(row["failed"]),
        "approved": int(row["approved"]),
        "rejected": int(row["rejected"]),
        "escalated": int(row["escalated"]),
        "reviewed": int(row["reviewed"]),
        "decided": decided,
        "approval_rate": round(int(row["approved"]) / decided, 3) if decided else None,
        "avg_confidence": round(float(row["avg_confidence"]), 3) if row["avg_confidence"] is not None else None,
        "patients": int(patients),
        "review_queue": review_queue_count(),
        "claims_by_status": count_bundles_by_status(),
    }


# ---- case bundles (one case holding several documents) ---------------------

def insert_bundle(bundle_id: str, title: str, patient_id: str | None, created_by: str) -> None:
    _execute("INSERT INTO claim_bundle (bundle_id, title, patient_id, created_by, updated_by) "
             "VALUES (%s,%s,%s,%s,%s)", (bundle_id, title, patient_id, created_by, created_by))


_BUNDLE_COLS = ("bundle_id, title, patient_id, status, decision, confidence, needs_review, "
                "created_by, created_at, updated_by, updated_at")


def _bundle_row(row: dict) -> dict:
    for key in ("result", "override"):
        if isinstance(row.get(key), str):
            row[key] = json.loads(row[key])
    if "needs_review" in row:
        row["needs_review"] = bool(row["needs_review"])
    if row.get("confidence") is not None:
        row["confidence"] = float(row["confidence"])
    return row


def get_bundle(bundle_id: str, with_result: bool = True) -> dict | None:
    cols = _BUNDLE_COLS + (", result, override" if with_result else "")
    rows = _fetch(f"SELECT {cols} FROM claim_bundle WHERE bundle_id=%s", (bundle_id,))
    return _bundle_row(rows[0]) if rows else None


def list_bundles(limit: int = 200, status: str | None = None, decision: str | None = None,
                 oldest_first: bool = False) -> list[dict]:
    """Claims, newest first, with the queue fields read straight from the stored result
    (justification, reasons, fraud score) so a list never loads full agent outputs."""
    clauses, args = [], []
    if status:
        clauses.append("status=%s"); args.append(status)
    if decision:
        clauses.append("decision=%s"); args.append(decision)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = _fetch(
        f"SELECT {_BUNDLE_COLS}, override, "
        "JSON_UNQUOTE(JSON_EXTRACT(result, '$.decision.justification')) AS justification, "
        "JSON_EXTRACT(result, '$.decision.reasons') AS reasons, "
        "JSON_EXTRACT(result, '$.fraud.fraud_score') AS fraud_score "
        f"FROM claim_bundle {where} ORDER BY created_at {'ASC' if oldest_first else 'DESC'} LIMIT %s",
        (*args, limit))
    for r in rows:
        r["reasons"] = json.loads(r["reasons"]) if r.get("reasons") else []
        r["fraud_score"] = float(r["fraud_score"]) if r.get("fraud_score") not in (None, "null") else None
        if r["justification"] == "null":
            r["justification"] = None
    return [_bundle_row(r) for r in rows]


def count_bundles_by_status() -> dict[str, int]:
    return {r["status"]: int(r["n"]) for r in
            _fetch("SELECT status, COUNT(*) AS n FROM claim_bundle WHERE status IS NOT NULL GROUP BY status")}


def cases_for_bundles(bundle_ids: list[str]) -> dict[str, list[dict]]:
    """The pages of many claims in one query, keyed by claim id."""
    out: dict[str, list[dict]] = {b: [] for b in bundle_ids}
    if not bundle_ids:
        return out
    marks = ", ".join(["%s"] * len(bundle_ids))
    rows = _fetch("SELECT case_id, file_name, doc_type, status, decision, confidence, needs_review, patient_id, "
                  f"bundle_id, created_by, created_at, updated_at FROM case_record WHERE bundle_id IN ({marks}) "
                  "ORDER BY created_at, file_name", tuple(bundle_ids))
    for r in rows:
        r["needs_review"] = bool(r["needs_review"])
        r["confidence"] = float(r["confidence"]) if r["confidence"] is not None else None
        out[r["bundle_id"]].append(r)
    return out


def update_bundle(bundle_id: str, status: str, decision: str | None = None,
                  confidence: float | None = None, needs_review: bool = False,
                  result: dict | None = None, override: dict | None = None,
                  updated_by: str | None = None, keep_result: bool = False) -> None:
    """Set the claim outcome. keep_result leaves the stored agent outputs as they are."""
    sets = ["status=%s", "decision=%s", "confidence=%s", "needs_review=%s",
            "updated_by=COALESCE(%s, updated_by)"]
    args: list = [status, decision, confidence, needs_review, updated_by]
    if not keep_result:
        sets.append("result=%s")
        args.append(json.dumps(result, default=str) if result is not None else None)
    if override is not None:
        sets.append("override=%s")
        args.append(json.dumps(override, default=str))
    _execute(f"UPDATE claim_bundle SET {', '.join(sets)} WHERE bundle_id=%s", (*args, bundle_id))


def bundle_documents(bundle_id: str) -> list[dict]:
    """The pages of a claim in upload order, with their stored result (file path, ingest output)."""
    rows = _fetch("SELECT * FROM case_record WHERE bundle_id=%s ORDER BY created_at, file_name", (bundle_id,))
    return [_case_row(r) for r in rows]


def review_queue_count() -> int:
    return int(_fetch("SELECT COUNT(*) AS n FROM claim_bundle WHERE status='NEEDS_REVIEW'")[0]["n"])


# ---- audit log -----------------------------------------------------------------------

def insert_audit_event(e: dict) -> None:
    created = str(e["created_at"]).replace("T", " ").split("+")[0]
    entity_type = e.get("entity_type") or "claim"
    entity_id = e.get("entity_id") or e.get("claim_id") or ""
    _execute("INSERT INTO audit_event (entity_type, entity_id, claim_id, doc_id, actor, event, status, detail, "
             "duration_ms, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
             (entity_type, entity_id, e.get("claim_id") if entity_type == "claim" else None, e.get("doc_id"),
              e["actor"], e["event"], e["status"], json.dumps(e.get("detail") or {}, default=str),
              e.get("duration_ms"), created))


def _audit_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        if isinstance(r.get("detail"), str):
            r["detail"] = json.loads(r["detail"])
        r["created_at"] = r["created_at"].isoformat(timespec="milliseconds") + "Z"
    return rows


_AUDIT_COLS = "id, entity_type, entity_id, claim_id, doc_id, actor, event, status, detail, duration_ms, created_at"


def list_audit_events(claim_id: str) -> list[dict]:
    return _audit_rows(_fetch(f"SELECT {_AUDIT_COLS} FROM audit_event WHERE entity_type='claim' AND entity_id=%s "
                              "ORDER BY id", (claim_id,)))


def search_audit_events(entity_type: str | None = None, entity_id: str | None = None, actor: str | None = None,
                        limit: int = 200) -> list[dict]:
    """The audit log across every entity, newest first."""
    clauses, args = [], []
    for col, value in (("entity_type", entity_type), ("entity_id", entity_id), ("actor", actor)):
        if value:
            clauses.append(f"{col}=%s")
            args.append(value)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return _audit_rows(_fetch(f"SELECT {_AUDIT_COLS} FROM audit_event {where} ORDER BY id DESC LIMIT %s",
                              (*args, limit)))


# ---- LangGraph checkpoints (app.checkpoint_mysql.MySQLSaver) ---------------------------

def checkpoint_rows(thread_id: str) -> list[dict]:
    return _fetch("SELECT checkpoint_ns, checkpoint_id, parent_id, type, checkpoint, meta_type, metadata "
                  "FROM lg_checkpoint WHERE thread_id=%s", (thread_id,))


def checkpoint_blob_rows(thread_id: str) -> list[dict]:
    return _fetch("SELECT checkpoint_ns, channel, version, type, value FROM lg_checkpoint_blob WHERE thread_id=%s",
                  (thread_id,))


def checkpoint_write_rows(thread_id: str) -> list[dict]:
    return _fetch("SELECT checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value, task_path "
                  "FROM lg_checkpoint_write WHERE thread_id=%s", (thread_id,))


def save_checkpoint(thread_id: str, ns: str, checkpoint_id: str, parent_id: str | None,
                    checkpoint: tuple[str, bytes], metadata: tuple[str, bytes],
                    blobs: list[tuple[str, str, str, bytes]]) -> None:
    conn = _connect(config.MYSQL_DB)
    try:
        with conn.cursor() as cur:
            cur.execute("REPLACE INTO lg_checkpoint (thread_id, checkpoint_ns, checkpoint_id, parent_id, type, "
                        "checkpoint, meta_type, metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (thread_id, ns, checkpoint_id, parent_id, checkpoint[0], checkpoint[1], metadata[0], metadata[1]))
            for channel, version, kind, value in blobs:
                cur.execute("REPLACE INTO lg_checkpoint_blob (thread_id, checkpoint_ns, channel, version, type, value) "
                            "VALUES (%s,%s,%s,%s,%s,%s)", (thread_id, ns, channel, version, kind, value))
    finally:
        conn.close()


def save_checkpoint_writes(thread_id: str, ns: str, checkpoint_id: str,
                           writes: list[tuple[str, int, str, str, bytes, str]]) -> None:
    conn = _connect(config.MYSQL_DB)
    try:
        with conn.cursor() as cur:
            for task_id, idx, channel, kind, value, path in writes:
                cur.execute("REPLACE INTO lg_checkpoint_write (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, "
                            "channel, type, value, task_path) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (thread_id, ns, checkpoint_id, task_id, idx, channel, kind, value, path))
    finally:
        conn.close()


def delete_checkpoint_thread(thread_id: str) -> None:
    for table in ("lg_checkpoint", "lg_checkpoint_blob", "lg_checkpoint_write"):
        _execute(f"DELETE FROM {table} WHERE thread_id=%s", (thread_id,))


# ---- model output cache ---------------------------------------------------------------

def cache_get(cache_key: str) -> dict | None:
    rows = _fetch("SELECT payload FROM model_cache WHERE cache_key=%s", (cache_key,))
    if not rows:
        return None
    payload = rows[0]["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


def cache_put(cache_key: str, kind: str, model: str, payload: dict) -> None:
    _execute("REPLACE INTO model_cache (cache_key, kind, model, payload) VALUES (%s,%s,%s,%s)",
             (cache_key, kind, model, json.dumps(payload, ensure_ascii=False)))


# ---- claim history used by the fraud agent -------------------------------------------

def save_claim_events(claim_id: str, member_id: str | None, events: list[dict]) -> None:
    """Replace the stored stays and prescriptions of one claim."""
    _execute("DELETE FROM clinical_event WHERE case_id=%s", (claim_id,))
    for ev in events:
        _execute("INSERT INTO clinical_event (case_id, member_id, admission_date, discharge_date, "
                 "prescription_date, diagnosis) VALUES (%s,%s,%s,%s,%s,%s)",
                 (claim_id, member_id, ev.get("admission_date"), ev.get("discharge_date"),
                  ev.get("prescription_date"), (ev.get("diagnosis") or "")[:255] or None))


def clear_claim_history() -> None:
    """Forget stored claims and stays (demo and dataset runs only), so a re-run of the same
    documents is not flagged as a duplicate of the previous run."""
    _execute("DELETE FROM claim")
    _execute("DELETE FROM clinical_event")


# ---- patients -------------------------------------------------------------

PATIENT_COLUMNS = ("full_name", "date_of_birth", "gender", "phone", "email", "address",
                   "policy_number", "member_id")
_PATIENT_SELECT = ("SELECT patient_id, member_id, full_name, date_of_birth, gender, phone, email, "
                   "address, policy_number, created_by, created_at, updated_by, updated_at FROM patient")


def _patient_row(row: dict) -> dict:
    if row.get("date_of_birth") is not None:
        row["date_of_birth"] = row["date_of_birth"].isoformat()
    return row


def insert_patient(patient_id: str, data: dict, created_by: str) -> None:
    cols = ", ".join(PATIENT_COLUMNS)
    marks = ", ".join(["%s"] * len(PATIENT_COLUMNS))
    _execute(f"INSERT INTO patient (patient_id, {cols}, created_by, updated_by) "
             f"VALUES (%s, {marks}, %s, %s)",
             (patient_id, *[data.get(c) for c in PATIENT_COLUMNS], created_by, created_by))


def update_patient(patient_id: str, data: dict, updated_by: str) -> None:
    sets = ", ".join(f"{c}=%s" for c in PATIENT_COLUMNS)
    _execute(f"UPDATE patient SET {sets}, updated_by=%s WHERE patient_id=%s",
             (*[data.get(c) for c in PATIENT_COLUMNS], updated_by, patient_id))


def find_patient_by_member(member_id: str, exclude: str | None = None) -> dict | None:
    """The patient already registered under this insurance member ID, other than `exclude`."""
    rows = _fetch("SELECT patient_id, full_name FROM patient WHERE member_id=%s AND patient_id<>%s",
                  (member_id, exclude or ""))
    return rows[0] if rows else None


def get_patient(patient_id: str) -> dict | None:
    rows = _fetch(f"{_PATIENT_SELECT} WHERE patient_id=%s", (patient_id,))
    return _patient_row(rows[0]) if rows else None


def list_patients(search: str | None = None, limit: int = 200) -> list[dict]:
    if search:
        like = f"%{search}%"
        rows = _fetch(f"{_PATIENT_SELECT} WHERE full_name LIKE %s OR policy_number LIKE %s "
                      "ORDER BY full_name LIMIT %s", (like, like, limit))
    else:
        rows = _fetch(f"{_PATIENT_SELECT} ORDER BY full_name LIMIT %s", (limit,))
    return [_patient_row(r) for r in rows]


def add_policy_document(filename: str, chunks: int) -> None:
    _execute("INSERT INTO policy_document (filename, chunks) VALUES (%s,%s)", (filename, chunks))


def get_user(username: str) -> dict | None:
    rows = _fetch("SELECT username, password_hash, role, full_name FROM app_user WHERE username=%s",
                  (username,))
    return rows[0] if rows else None


def upsert_user(username: str, password_hash: str, role: str, full_name: str) -> None:
    _execute("INSERT INTO app_user (username, password_hash, role, full_name) VALUES (%s,%s,%s,%s) "
             "ON DUPLICATE KEY UPDATE password_hash=VALUES(password_hash), role=VALUES(role), "
             "full_name=VALUES(full_name)", (username, password_hash, role, full_name))


def list_policy_documents() -> list[dict]:
    return _fetch("SELECT id, filename, chunks, ingested_at FROM policy_document "
                  "ORDER BY ingested_at DESC")


_repository: Repository | None = None


def get_repository() -> Repository:
    global _repository
    if _repository is None:
        _repository = MySQLRepository()
    return _repository


def set_repository(repo: Repository | None) -> None:
    global _repository
    _repository = repo
