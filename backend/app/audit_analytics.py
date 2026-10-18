"""
Audit-log analytics over the MySQL table audit_event: aggregates for the Audit log page, a
CSV export of the raw events, and a PDF report of the analysis.

An actor is an agent when it is one of the pipeline's own steps (AGENT_ACTORS); anyone else is
a person. Agent performance comes from "agent_completed" rows (duration_ms). Decisions are
read from "claim_decided" (the agents) and "human_override" / "human_review_resumed" (people).
Filters: range (24h | 7d | 30d | all), entity type, actor. Limits come from .env.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app import config, db
from app.log_analytics import RANGES, bucket_plan, fill_buckets, since_of  # noqa: F401  (RANGES re-exported)

ENTITY_TYPES = ["claim", "case", "patient", "policy", "user"]
# Categorical palette, fixed order (validated: adjacent CVD dE >= 9.1, normal-vision >= 19.6).
ENTITY_COLORS = {"claim": "#2a78d6", "case": "#eb6834", "patient": "#1baf7a", "policy": "#eda100", "user": "#e87ba4"}
AGENT_ACTORS = {"preprocess", "classify", "extract", "ingest", "plan", "kyc", "claims", "policy", "fraud", "decide",
                "human_review", "orchestrator", "system"}
HUMAN_DECISION_EVENTS = ("human_override", "human_review_resumed")
DECISIONS = ["APPROVE", "REJECT", "ESCALATE"]


def _where(since: datetime | None, entity_type: str | None, actor: str | None,
           event: str | None = None, entity_id: str | None = None) -> tuple[str, list]:
    clauses, args = [], []
    if since:
        clauses.append("created_at >= %s")
        args.append(since)
    for col, value in (("entity_type", entity_type), ("actor", actor), ("event", event), ("entity_id", entity_id)):
        if value:
            clauses.append(f"{col} = %s")
            args.append(value)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", args


def _and(where: str, clause: str) -> str:
    return f"{where} AND {clause}" if where else f"WHERE {clause}"


def _detail(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds") + "Z"


def started_at(finished: datetime, duration_ms) -> datetime | None:
    """When an agent step started: its agent_completed row is written as it finishes, with the duration."""
    return None if duration_ms is None else finished - timedelta(milliseconds=int(duration_ms))


def investigation_notes(where: str, args: list, limit: int) -> list[dict]:
    """The newest investigation notes: one per finished agent step (agent_completed), saying what
    the agent worked on, when it started and finished, how long it took and what it returned."""
    completed = _and(where, "event = 'agent_completed'")
    rows = db._fetch(f"SELECT created_at, entity_type, entity_id, actor, status, duration_ms, detail "
                     f"FROM audit_event {completed} ORDER BY id DESC LIMIT %s", (*args, limit))
    return [{"started_at": _iso(start) if (start := started_at(r["created_at"], r["duration_ms"])) else None,
             "created_at": _iso(r["created_at"]), "entity_type": r["entity_type"], "entity_id": r["entity_id"],
             "actor": r["actor"], "status": r["status"], "duration_ms": r["duration_ms"],
             "note": str(_detail(r["detail"]).get("note") or "")}
            for r in rows]


def investigations(where: str, args: list, limit: int) -> list[dict]:
    """One row per claim investigated in the filter, newest first: agent steps, total agent time,
    failed steps, the agents' decision and the reviewer's, and the orchestrator's note."""
    completed = _and(_and(where, "event = 'agent_completed'"), "entity_type = 'claim'")
    rows = db._fetch(f"SELECT entity_id, COUNT(*) AS steps, COALESCE(SUM(duration_ms), 0) AS total_ms, "
                     f"SUM(status='error') AS errors, MAX(created_at) AS last_at FROM audit_event {completed} "
                     "GROUP BY entity_id ORDER BY last_at DESC LIMIT %s", (*args, limit))
    if not rows:
        return []
    ids = [r["entity_id"] for r in rows]
    marks = ", ".join(["%s"] * len(ids))
    titles = {b["bundle_id"]: b["title"]
              for b in db._fetch(f"SELECT bundle_id, title FROM claim_bundle WHERE bundle_id IN ({marks})", tuple(ids))}
    agent_dec, human_dec, summary = {}, {}, {}
    for r in db._fetch("SELECT entity_id, actor, event, detail FROM audit_event WHERE entity_type = 'claim' "
                       f"AND entity_id IN ({marks}) AND (event IN ('claim_decided', 'human_override', "
                       "'human_review_resumed') OR (event = 'agent_completed' AND actor = 'decide')) ORDER BY id",
                       tuple(ids)):
        d = _detail(r["detail"])
        if r["event"] == "agent_completed":
            summary[r["entity_id"]] = str(d.get("note") or "")
        elif d.get("decision"):                               # ordered by id: the latest one wins
            (human_dec if r["event"] in HUMAN_DECISION_EVENTS else agent_dec)[r["entity_id"]] = str(d["decision"]).upper()
    return [{"claim_id": r["entity_id"], "title": titles.get(r["entity_id"], ""), "steps": int(r["steps"]),
             "total_ms": int(r["total_ms"] or 0), "errors": int(r["errors"] or 0), "last_at": _iso(r["last_at"]),
             "agent_decision": agent_dec.get(r["entity_id"]), "reviewer_decision": human_dec.get(r["entity_id"]),
             "summary": summary.get(r["entity_id"], "")} for r in rows]


def stats(range_key: str = "24h", entity_type: str | None = None, actor: str | None = None,
          event: str | None = None, claims_limit: int | None = None, entity_id: str | None = None) -> dict:
    since = since_of(range_key)
    where, args = _where(since, entity_type, actor, event, entity_id)
    hourly, fmt = bucket_plan("audit_event", since, where, args)

    series: dict[str, dict[str, int]] = defaultdict(lambda: {e: 0 for e in ENTITY_TYPES})
    per_type = Counter()
    for r in db._fetch(f"SELECT DATE_FORMAT(created_at, %s) AS bucket, entity_type, COUNT(*) AS n FROM audit_event "
                       f"{where} GROUP BY bucket, entity_type", (fmt, *args)):
        et = r["entity_type"] if r["entity_type"] in ENTITY_TYPES else "claim"
        series[r["bucket"]][et] += int(r["n"])
        per_type[et] += int(r["n"])
    volume = fill_buckets(series, since, hourly, ENTITY_TYPES)

    actors = db._fetch(f"SELECT actor, COUNT(*) AS n, SUM(status='error') AS errors FROM audit_event {where} "
                       "GROUP BY actor ORDER BY n DESC", tuple(args))
    people = [a for a in actors if a["actor"] not in AGENT_ACTORS]
    events = db._fetch(f"SELECT event, COUNT(*) AS n FROM audit_event {where} GROUP BY event ORDER BY n DESC LIMIT %s",
                       (*args, config.LOG_TOP_N))

    durations: dict[str, list[int]] = defaultdict(list)
    agent_errors = Counter()
    completed = _and(where, "event = 'agent_completed'")
    for r in db._fetch(f"SELECT actor, status, duration_ms FROM audit_event {completed} ORDER BY id DESC LIMIT %s",
                       (*args, config.LOG_STATS_SCAN_MAX)):
        if r["duration_ms"] is not None:
            durations[r["actor"]].append(int(r["duration_ms"]))
        if r["status"] == "error":
            agent_errors[r["actor"]] += 1
    agents = sorted(({"agent": a, "runs": len(v), "avg_ms": round(sum(v) / len(v)), "p95_ms": _p95(v),
                      "max_ms": max(v), "errors": agent_errors[a]} for a, v in durations.items() if v),
                    key=lambda x: -x["avg_ms"])

    agent_decisions, human_decisions = Counter(), Counter()
    decided = _and(where, "event IN ('claim_decided', 'human_override', 'human_review_resumed')")
    for r in db._fetch(f"SELECT event, detail FROM audit_event {decided}",
                       tuple(args)):
        decision = str(_detail(r["detail"]).get("decision") or "").upper()
        if decision in DECISIONS:
            (human_decisions if r["event"] in HUMAN_DECISION_EVENTS else agent_decisions)[decision] += 1

    failed = _and(where, "status = 'error'")
    errors = [{"created_at": r["created_at"].isoformat(timespec="seconds") + "Z", "entity_type": r["entity_type"],
               "entity_id": r["entity_id"], "actor": r["actor"], "event": r["event"],
               "detail": str(_detail(r["detail"]).get("error") or "")[:200]}
              for r in db._fetch(f"SELECT created_at, entity_type, entity_id, actor, event, detail FROM audit_event "
                                 f"{failed} ORDER BY id DESC LIMIT %s",
                                 (*args, config.LOG_TOP_N))]

    totals_row = db._fetch(f"SELECT COUNT(*) AS n, COUNT(DISTINCT actor) AS actors, SUM(status='error') AS errors, "
                           "COUNT(DISTINCT CASE WHEN entity_type='claim' THEN entity_id END) AS claims "
                           f"FROM audit_event {where}", tuple(args))[0]
    human_events = sum(int(a["n"]) for a in people)
    total = int(totals_row["n"] or 0)
    return {
        "range": range_key, "entity_type": entity_type, "actor": actor, "bucket": "hour" if hourly else "day",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {"events": total, "actors": int(totals_row["actors"] or 0), "people": len(people),
                   "human_events": human_events, "agent_events": total - human_events,
                   "errors": int(totals_row["errors"] or 0), "claims": int(totals_row["claims"] or 0),
                   "human_decisions": sum(human_decisions.values()), **{e: per_type[e] for e in ENTITY_TYPES}},
        "volume": volume,
        "by_actor": [{"actor": a["actor"], "count": int(a["n"]), "errors": int(a["errors"] or 0),
                      "kind": "agent" if a["actor"] in AGENT_ACTORS else "person"} for a in actors[:config.LOG_TOP_N]],
        "by_event": [{"event": e["event"], "count": int(e["n"])} for e in events],
        "agents": agents,
        "decisions": [{"decision": d, "agents": agent_decisions[d], "people": human_decisions[d]} for d in DECISIONS],
        "errors": errors,
        "investigations": investigations(where, args, claims_limit or config.LOG_TOP_N),
        "notes": investigation_notes(where, args, config.LOG_TOP_N),
        "colors": ENTITY_COLORS,
    }


# ---- CSV export of the raw events -------------------------------------------------------

DB_COLUMNS = ["id", "created_at", "entity_type", "entity_id", "doc_id", "actor", "event", "status", "duration_ms", "detail"]
# For an agent step: "started_at" (created_at is when it finished) and "investigation_note"
# (detail.note pulled out, so the notes read without parsing the JSON).
CSV_COLUMNS = ["id", "started_at", "created_at", *DB_COLUMNS[2:-1], "investigation_note", "detail"]


def export_csv(range_key: str, entity_type: str | None, actor: str | None, event: str | None = None,
               entity_id: str | None = None) -> str:
    where, args = _where(since_of(range_key), entity_type, actor, event, entity_id)
    return _write_csv(db._fetch(f"SELECT {', '.join(DB_COLUMNS)} FROM audit_event {where} ORDER BY id DESC LIMIT %s",
                                (*args, config.LOG_EXPORT_MAX_ROWS)))


def _write_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        detail = r["detail"] if isinstance(r["detail"], str) else json.dumps(r["detail"] or {}, ensure_ascii=False)
        start = started_at(r["created_at"], r["duration_ms"]) if r["event"] == "agent_completed" else None
        writer.writerow([r["id"], start.isoformat(timespec="milliseconds") if start else "",
                         r["created_at"].isoformat(timespec="milliseconds"), r["entity_type"], r["entity_id"],
                         r["doc_id"] or "", r["actor"], r["event"], r["status"],
                         "" if r["duration_ms"] is None else r["duration_ms"],
                         str(_detail(r["detail"]).get("note") or ""), detail])
    return "﻿" + buf.getvalue()


# ---- one claim: its whole investigation --------------------------------------------------

def audit_claims(limit: int) -> list[dict]:
    """Every claim the audit log knows, newest activity first, for the Audit log's claim picker.
    Read from the audit trail, so a claim deleted since can still be picked."""
    rows = db._fetch("SELECT entity_id, MAX(created_at) AS last_at, COUNT(*) AS events FROM audit_event "
                     "WHERE entity_type = 'claim' GROUP BY entity_id ORDER BY last_at DESC LIMIT %s", (limit,))
    if not rows:
        return []
    marks = ", ".join(["%s"] * len(rows))
    known = {b["bundle_id"]: b for b in db._fetch(f"SELECT bundle_id, title, status FROM claim_bundle "
                                                   f"WHERE bundle_id IN ({marks})", tuple(r["entity_id"] for r in rows))}
    return [{"claim_id": r["entity_id"], "title": known.get(r["entity_id"], {}).get("title"),
             "status": known.get(r["entity_id"], {}).get("status") or "DELETED",
             "last_at": _iso(r["last_at"]), "events": int(r["events"])} for r in rows]


def _claim_events(claim_id: str) -> list[dict]:
    """Every audit row of one claim, oldest first (all runs, including re-runs)."""
    return db._fetch(f"SELECT {', '.join(DB_COLUMNS)} FROM audit_event WHERE entity_type = 'claim' "
                     "AND entity_id = %s ORDER BY id", (claim_id,))


def claim_csv(claim_id: str) -> str:
    """The raw audit trail of one claim, oldest first, in the same columns as the full export."""
    return _write_csv(_claim_events(claim_id))


def _ts(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if dt else "-"


def _event_text(d: dict) -> str:
    """One readable line for any claim event (agent runs carry their own note)."""
    if d.get("note"):
        return str(d["note"])
    for key in ("justification", "error", "decision", "action", "file_name"):
        if d.get(key):
            return f"{key}: {d[key]}"
    if isinstance(d.get("files"), list):
        return "files: " + ", ".join(map(str, d["files"]))
    return json.dumps(d, ensure_ascii=False, default=str)[:300] if d else ""


def _run_head(i: int, x: dict) -> str:
    d = _detail(x["detail"])
    return (f"{i}. {x['actor']}" + (f" on {d['file_name']}" if d.get("file_name") else "")
            + f"  ·  started {_ts(started_at(x['created_at'], x['duration_ms']))}  ·  finished {_ts(x['created_at'])}"
            + f"  ·  took {_ms(int(x['duration_ms'] or 0))}" + ("  ·  FAILED" if x["status"] == "error" else ""))


def claim_report_pdf(claim_id: str) -> bytes | None:
    """Everything that happened to one claim: which agent ran, on what, when it started and
    finished, how long it took and what it returned; plus every human action. None if no claim."""
    from app.pdf_report import Report

    claim = db.get_bundle(claim_id)
    rows = _claim_events(claim_id)
    if claim is None and not rows:
        return None
    if claim is None:                       # deleted since: the audit trail outlives it, so report from that alone
        claim = {"title": f"{claim_id[:8]} (claim no longer exists)", "status": "deleted", "created_by": rows[0]["actor"]}
    runs = [r for r in rows if r["event"] == "agent_completed"]
    patient = db.get_patient(claim["patient_id"]) if claim.get("patient_id") else None
    docs = db.bundle_documents(claim_id)
    decision = (claim.get("result") or {}).get("decision") or {}
    override = claim.get("override") or {}
    agent_ms = sum(int(r["duration_ms"] or 0) for r in runs)
    wall_ms = int((rows[-1]["created_at"] - rows[0]["created_at"]).total_seconds() * 1000) if rows else 0

    r = Report(f"MediShield - Claim investigation: {claim['title']}",
               f"Claim {claim_id}   Patient: {patient['full_name'] if patient else 'none'}   "
               f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')} UTC")
    r.tiles([("Status", str(claim.get("status") or "-").replace("_", " ").lower()),
             ("Agents decided", str(decision.get("decision") or claim.get("decision") or "-")),
             ("Reviewer decided", str(override.get("decision") or "-")),
             ("Agent steps", str(len(runs))), ("Agent time", _ms(agent_ms)), ("Start to end", _ms(wall_ms))])
    claim_lines = [
        # From the audit trail (UTC), like every other time here; claim_bundle.created_at is server-local time.
        ("Submitted", f"by {claim.get('created_by')} on {_ts(rows[0]['created_at']) if rows else '-'} UTC"),
        (f"Documents ({len(docs)})", "; ".join(f"{d['file_name']} ({str(d.get('doc_type') or 'unclassified').lower()}, "
                                               f"{str(d.get('status') or '').lower()})" for d in docs) or "none"),
        ("Orchestrator", f"{decision.get('decision', '-')} at "
                         + (f"{float(decision['confidence']):.0%}" if decision.get("confidence") is not None else "-")
                         + " confidence; reasons: "
                         + ("; ".join(decision.get("reasons") or []) or "none")),
    ]
    if override:
        claim_lines.append(("Reviewer", f"{override.get('decision')} by {override.get('reviewer')}: {override.get('note')}"))
    r.entries("The claim", claim_lines)
    r.table(f"Agent runs, in order ({len(runs)})", ["#", "Agent", "Worked on", "Started", "Finished", "Took", "Status"],
            [0.05, 0.13, 0.24, 0.19, 0.19, 0.1, 0.1],
            [[i, x["actor"], _detail(x["detail"]).get("file_name") or "claim",
              _ts(started_at(x["created_at"], x["duration_ms"]))[11:], _ts(x["created_at"])[11:],
              _ms(int(x["duration_ms"] or 0)), x["status"]] for i, x in enumerate(runs, 1)])
    r.entries("What each agent returned",
              [(_run_head(i, x), str(_detail(x["detail"]).get("note") or "")) for i, x in enumerate(runs, 1)])
    r.entries(f"Every event ({len(rows)})",
              [(f"{_ts(x['created_at'])}  ·  {x['actor']}  ·  {x['event'].replace('_', ' ')}"
                + (f"  ·  took {_ms(int(x['duration_ms']))}" if x["duration_ms"] is not None else "")
                + ("  ·  ERROR" if x["status"] == "error" else ""),
                _event_text(_detail(x["detail"]))) for x in rows if x["event"] != "agent_started"])
    r.note("Source: MySQL table audit_event. Times are UTC. A step's start is its finish time minus its duration. "
           "'Agent time' adds up every agent step; 'Start to end' runs from the first event to the last, "
           "including waits for people. The raw CSV has every field of every event.")
    return r.output()


# ---- PDF report of the analysis ---------------------------------------------------------

def _ms(n: int) -> str:
    return f"{n / 60000:.1f} min" if n >= 60000 else f"{n / 1000:.1f} s" if n >= 1000 else f"{n} ms"


def report_pdf(range_key: str, entity_type: str | None, actor: str | None, event: str | None = None,
               entity_id: str | None = None) -> bytes:
    from app.pdf_report import Report

    # Every claim investigated in the range (bounded by the stats scan), not just the top few.
    s = stats(range_key, entity_type, actor, event, claims_limit=config.LOG_STATS_SCAN_MAX, entity_id=entity_id)
    t = s["totals"]
    r = Report("MediShield - Audit log analysis",
               f"Range: {range_key}   Entity: {entity_type or 'all'}   Actor: {actor or 'all'}   "
               + (f"Event: {event}   " if event else "") + (f"Claim: {entity_id}   " if entity_id else "")
               + f"Generated: {s['generated_at']} UTC")
    r.tiles([("Events", f"{t['events']:,}"), ("Claims touched", f"{t['claims']:,}"),
             ("People", f"{t['people']:,}"), ("Human actions", f"{t['human_events']:,}"),
             ("Human decisions", f"{t['human_decisions']:,}"), ("Errors", f"{t['errors']:,}")])
    r.stacked_columns(f"Audit events per {s['bucket']}, by entity", s["volume"],
                      [(e, e.title(), ENTITY_COLORS[e]) for e in ENTITY_TYPES], t, label_of=lambda b: b[5:])
    r.table("Decisions: agents vs people", ["Decision", "By the agents", "By a reviewer"], [0.4, 0.3, 0.3],
            [[d["decision"], d["agents"], d["people"]] for d in s["decisions"]])
    r.table("Agent performance", ["Agent", "Runs", "Avg ms", "p95 ms", "Max ms", "Errors"],
            [0.3, 0.14, 0.14, 0.14, 0.14, 0.14],
            [[a["agent"], a["runs"], f"{a['avg_ms']:,}", f"{a['p95_ms']:,}", f"{a['max_ms']:,}", a["errors"]]
             for a in s["agents"]])
    r.table("Most active actors", ["Actor", "Kind", "Events", "Errors"], [0.4, 0.2, 0.2, 0.2],
            [[a["actor"], a["kind"], f"{a['count']:,}", a["errors"]] for a in s["by_actor"]])
    r.table("Most frequent events", ["Event", "Count"], [0.7, 0.3],
            [[e["event"].replace("_", " "), f"{e['count']:,}"] for e in s["by_event"]])
    r.table("Recent errors", ["Time", "Entity", "Actor", "Event / error"], [0.18, 0.22, 0.15, 0.45],
            [[e["created_at"].replace("T", " ")[:16], f"{e['entity_type']} {e['entity_id'][:8]}", e["actor"],
              f"{e['event']}: {e['detail']}"] for e in s["errors"]])
    r.table(f"Claim investigations ({len(s['investigations'])})",
            ["Last step", "Claim", "Steps", "Agent time", "Failed", "Agents", "Reviewer"],
            [0.15, 0.33, 0.08, 0.12, 0.08, 0.12, 0.12],
            [[c["last_at"].replace("T", " ")[:16], f"{c['claim_id'][:8]} {c['title']}", c["steps"], _ms(c["total_ms"]),
              c["errors"], c["agent_decision"] or "-", c["reviewer_decision"] or "-"] for c in s["investigations"]])
    r.entries("Orchestrator notes per claim",
              [(f"{c['claim_id'][:8]} {c['title']}", c["summary"]) for c in s["investigations"] if c["summary"]])
    r.entries("Latest investigation notes",
              [(f"{n['entity_type']} {n['entity_id'][:8]}  ·  {n['actor']}  ·  "
                f"started {(n['started_at'] or '-').replace('T', ' ')[:19]}  ·  finished {n['created_at'].replace('T', ' ')[:19]}"
                + (f"  ·  took {_ms(n['duration_ms'])}" if n["duration_ms"] is not None else "")
                + ("  ·  ERROR" if n["status"] == "error" else ""), n["note"]) for n in s["notes"]])
    r.note("Source: MySQL table audit_event. Agents are the pipeline's own steps; every other actor is a person.")
    return r.output()
