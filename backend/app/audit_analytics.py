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
from datetime import datetime, timezone

from app import config, db
from app.log_analytics import RANGES, bucket_plan, fill_buckets, since_of  # noqa: F401  (RANGES re-exported)

ENTITY_TYPES = ["claim", "case", "patient", "policy", "user"]
# Categorical palette, fixed order (validated: adjacent CVD dE >= 9.1, normal-vision >= 19.6).
ENTITY_COLORS = {"claim": "#2a78d6", "case": "#eb6834", "patient": "#1baf7a", "policy": "#eda100", "user": "#e87ba4"}
AGENT_ACTORS = {"preprocess", "classify", "extract", "ingest", "plan", "kyc", "claims", "policy", "fraud", "decide",
                "human_review", "orchestrator", "system"}
HUMAN_DECISION_EVENTS = ("human_override", "human_review_resumed")
DECISIONS = ["APPROVE", "REJECT", "ESCALATE"]


def _where(since: datetime | None, entity_type: str | None, actor: str | None) -> tuple[str, list]:
    clauses, args = [], []
    if since:
        clauses.append("created_at >= %s")
        args.append(since)
    if entity_type:
        clauses.append("entity_type = %s")
        args.append(entity_type)
    if actor:
        clauses.append("actor = %s")
        args.append(actor)
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


def stats(range_key: str = "24h", entity_type: str | None = None, actor: str | None = None) -> dict:
    since = since_of(range_key)
    where, args = _where(since, entity_type, actor)
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
        "colors": ENTITY_COLORS,
    }


# ---- CSV export of the raw events -------------------------------------------------------

CSV_COLUMNS = ["id", "created_at", "entity_type", "entity_id", "doc_id", "actor", "event", "status", "duration_ms", "detail"]


def export_csv(range_key: str, entity_type: str | None, actor: str | None) -> str:
    where, args = _where(since_of(range_key), entity_type, actor)
    rows = db._fetch(f"SELECT {', '.join(CSV_COLUMNS)} FROM audit_event {where} ORDER BY id DESC LIMIT %s",
                     (*args, config.LOG_EXPORT_MAX_ROWS))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        detail = r["detail"] if isinstance(r["detail"], str) else json.dumps(r["detail"] or {}, ensure_ascii=False)
        writer.writerow([r["id"], r["created_at"].isoformat(timespec="milliseconds"), r["entity_type"], r["entity_id"],
                         r["doc_id"] or "", r["actor"], r["event"], r["status"],
                         "" if r["duration_ms"] is None else r["duration_ms"], detail])
    return "﻿" + buf.getvalue()


# ---- PDF report of the analysis ---------------------------------------------------------

def report_pdf(range_key: str, entity_type: str | None, actor: str | None) -> bytes:
    from app.pdf_report import Report

    s = stats(range_key, entity_type, actor)
    t = s["totals"]
    r = Report("MediShield - Audit log analysis",
               f"Range: {range_key}   Entity: {entity_type or 'all'}   Actor: {actor or 'all'}   "
               f"Generated: {s['generated_at']} UTC")
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
    r.note("Source: MySQL table audit_event. Agents are the pipeline's own steps; every other actor is a person.")
    return r.output()
