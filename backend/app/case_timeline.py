"""
One case's audit trail as numbered steps, oldest first: the single source for the Audit log's
Steps and Analytics tabs, the case CSV and the case PDF, so all four tell the same story.

Each agent writes two rows ("agent_started", "agent_completed"); they become one step: when the
agent was called, when it finished, how long it took and what it returned. Every other event
(claim submitted, decided, paused for review, a reviewer's action, ...) is a step of its own.
Steps are grouped into runs: the first run starts with the case, and a re-run (or a second
"claim_started") opens the next. Numbering is fixed on the whole trail, so a filtered view
still shows each step's real number.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from app import config, db
from app.audit_analytics import AGENT_ACTORS, DB_COLUMNS, HUMAN_DECISION_EVENTS, _detail

RUN_OPENERS = {"claim_rerun_requested"}
# One colour per agent, in pipeline order; the web Gantt uses the same values.
AGENT_COLORS = {"preprocess": "#7c8a9e", "classify": "#2a78d6", "extract": "#1baf7a", "ingest": "#0e9aa7",
                "plan": "#8f6bd1", "kyc": "#eda100", "claims": "#eb6834", "policy": "#e87ba4",
                "fraud": "#d6455d", "decide": "#4b5563", "human_review": "#0b8457"}
OTHER_COLOR = "#94a3b8"


def _ms(n: int | None) -> str:
    if n is None:
        return "-"
    return f"{n / 60000:.1f} min" if n >= 60000 else f"{n / 1000:.1f} s" if n >= 1000 else f"{n} ms"


def _result(event: str, d: dict) -> str:
    """What the step produced, in one line. An agent's note reads "<agent> on <file> took <t> and
    returned: <result>"; the step already says the rest, so only the result is kept."""
    note = str(d.get("note") or "")
    if note:
        i = note.find(" and returned: ")
        return note[i + len(" and returned: "):] if i >= 0 else note
    if event == "claim_decided" and d.get("decision"):
        conf = f" at {float(d['confidence']):.0%} confidence" if d.get("confidence") is not None else ""
        return f"{d['decision']}{conf}: {d.get('justification') or ''}".rstrip(": ")
    for key in ("justification", "error", "decision", "action", "reason"):
        if d.get(key):
            return f"{key}: {d[key]}"
    if isinstance(d.get("files"), list):
        return f"{len(d['files'])} documents: " + ", ".join(map(str, d["files"]))
    if d.get("documents"):
        return f"{d['documents']} documents"
    if d.get("format"):
        return f"downloaded as {str(d['format']).upper()}"
    return ""


def _kind(actor: str, agent_run: bool) -> str:
    return "agent" if agent_run else "system" if actor in AGENT_ACTORS else "person"


def _rows(case_id: str) -> list[dict]:
    clause, args = db.audit_case_scope(case_id)
    return db._fetch(f"SELECT {', '.join(DB_COLUMNS)} FROM audit_event WHERE {clause} ORDER BY id LIMIT %s",
                     (*args, config.LOG_EXPORT_MAX_ROWS))


def build_steps(rows: list[dict]) -> list[dict]:
    """Audit rows (oldest first) -> numbered steps, agent start/finish pairs merged."""
    steps: list[dict] = []
    open_runs: dict[tuple, dict] = {}            # (run, agent, document) -> the step still running
    run, run_started = 0, False
    for r in rows:
        if run == 0 or r["event"] in RUN_OPENERS or (r["event"] == "claim_started" and run_started):
            run, run_started = run + 1, False
        if r["event"] == "claim_started":
            run_started = True
        d = _detail(r["detail"])
        key = (run, r["actor"], r["doc_id"] or d.get("file_name"))
        if r["event"] == "agent_completed" and key in open_runs:
            s = open_runs.pop(key)
            s.update(finished_at=r["created_at"], duration_ms=r["duration_ms"], status=r["status"],
                     result=_result(r["event"], d), detail={k: v for k, v in d.items() if k != "note"}, id=r["id"])
            continue
        agent_run = r["event"] in ("agent_started", "agent_completed")
        dur = r["duration_ms"] if r["event"] == "agent_completed" else None
        start = r["created_at"] - timedelta(milliseconds=dur) if dur is not None else r["created_at"]
        s = {"id": r["id"], "run": run, "actor": r["actor"], "kind": _kind(r["actor"], agent_run),
             "event": "agent_run" if agent_run else r["event"], "worked_on": d.get("file_name") or "",
             "started_at": start, "finished_at": r["created_at"] if r["event"] != "agent_started" else None,
             "duration_ms": dur, "status": "running" if r["event"] == "agent_started" else r["status"],
             "result": "" if r["event"] == "agent_started" else _result(r["event"], d),
             "detail": {k: v for k, v in d.items() if k != "note"}}
        if r["event"] == "agent_started":
            open_runs[key] = s
        steps.append(s)
    # Number the steps within each run, in the order they were called.
    counters: dict[int, int] = {}
    for s in steps:
        counters[s["run"]] = counters.get(s["run"], 0) + 1
        s["step"] = counters[s["run"]]
    return steps


def _runs(steps: list[dict]) -> list[dict]:
    out = []
    for n in sorted({s["run"] for s in steps}):
        ss = [s for s in steps if s["run"] == n]
        start = min(s["started_at"] for s in ss)
        end = max(s["finished_at"] or s["started_at"] for s in ss)
        agents = [s for s in ss if s["kind"] == "agent"]
        decided = next((s for s in reversed(ss) if s["event"] == "claim_decided"), None)
        reviewed = next((s for s in reversed(ss) if s["event"] in HUMAN_DECISION_EVENTS), None)
        first = ss[0]
        out.append({
            "run": n, "started_at": start, "finished_at": end,
            "wall_ms": int((end - start).total_seconds() * 1000),
            "agent_ms": sum(int(s["duration_ms"] or 0) for s in agents),
            "steps": len(ss), "agent_steps": len(agents), "errors": sum(s["status"] == "error" for s in ss),
            "running": any(s["status"] == "running" for s in ss),
            "trigger": f"{first['event'].replace('_', ' ')} by {first['actor']}",
            "decision": (decided or {}).get("detail", {}).get("decision"),
            "confidence": (decided or {}).get("detail", {}).get("confidence"),
            "reviewer_decision": (reviewed or {}).get("detail", {}).get("decision"),
            "reviewer": (reviewed or {}).get("actor"),
        })
    return out


def _by_agent(steps: list[dict]) -> list[dict]:
    """Agent time per agent, in the order the agents were first called."""
    order: dict[str, dict] = {}
    for s in steps:
        if s["kind"] != "agent":
            continue
        a = order.setdefault(s["actor"], {"agent": s["actor"], "calls": 0, "total_ms": 0, "max_ms": 0, "errors": 0,
                                          "color": AGENT_COLORS.get(s["actor"], OTHER_COLOR)})
        ms = int(s["duration_ms"] or 0)
        a["calls"] += 1
        a["total_ms"] += ms
        a["max_ms"] = max(a["max_ms"], ms)
        a["errors"] += s["status"] == "error"
    total = sum(a["total_ms"] for a in order.values()) or 1
    for a in order.values():
        a["avg_ms"] = round(a["total_ms"] / a["calls"])
        a["share"] = round(a["total_ms"] / total, 4)
    return list(order.values())


def case_timeline(case_id: str) -> dict | None:
    """Everything the Audit log shows for one case. None when the audit log has nothing for it."""
    rows = _rows(case_id)
    bundle = db.get_bundle(case_id, with_result=False)
    if not rows and bundle is None:
        return None
    steps = build_steps(rows)
    runs = _runs(steps)
    run_start = {r["run"]: r["started_at"] for r in runs}
    for s in steps:                                  # where the step sits in its run, for the Gantt
        s["offset_ms"] = int((s["started_at"] - run_start[s["run"]]).total_seconds() * 1000)
    agent_ms = sum(int(s["duration_ms"] or 0) for s in steps if s["kind"] == "agent")
    last = runs[-1] if runs else {}
    return {
        "case": {"case_id": case_id, "title": (bundle or {}).get("title") or case_id[:8],
                 "status": (bundle or {}).get("status") or "DELETED"},
        "totals": {"runs": len(runs), "steps": len(steps), "agent_steps": sum(s["kind"] == "agent" for s in steps),
                   "people_steps": sum(s["kind"] == "person" for s in steps), "agent_ms": agent_ms,
                   "wall_ms": int((runs[-1]["finished_at"] - runs[0]["started_at"]).total_seconds() * 1000) if runs else 0,
                   "errors": sum(s["status"] == "error" for s in steps),
                   "decision": last.get("decision"), "reviewer_decision": last.get("reviewer_decision")},
        "runs": runs,
        "by_agent": _by_agent(steps),
        "steps": steps,
        "colors": AGENT_COLORS,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def to_json(t: dict) -> dict:
    """Datetimes as ISO strings (UTC, 'Z') with milliseconds, for the API: steps of a run are
    often less than a second apart."""
    def conv(v):
        return v.isoformat(timespec="milliseconds") + "Z" if isinstance(v, datetime) else v
    return {**t, "runs": [{k: conv(v) for k, v in r.items()} for r in t["runs"]],
            "steps": [{k: conv(v) for k, v in s.items()} for s in t["steps"]]}


# ---- CSV: one row per step, in order --------------------------------------------------------

CSV_COLUMNS = ["case", "run", "step", "started_at", "finished_at", "duration_ms", "duration", "who", "kind",
               "event", "worked_on", "status", "result", "detail"]


def _t(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] if dt else ""


def steps_csv(t: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for s in t["steps"]:
        w.writerow([t["case"]["title"], s["run"], s["step"], _t(s["started_at"]), _t(s["finished_at"]),
                    "" if s["duration_ms"] is None else s["duration_ms"], _ms(s["duration_ms"]) if s["duration_ms"] is not None else "",
                    s["actor"], s["kind"], s["event"].replace("_", " "), s["worked_on"], s["status"], s["result"],
                    json.dumps(s["detail"], ensure_ascii=False, default=str) if s["detail"] else ""])
    return "﻿" + buf.getvalue()                 # BOM: Excel opens it as UTF-8


# ---- PDF: the case, its runs, each run's steps in order ---------------------------------------

def _label(s: dict) -> str:
    what = s["actor"] if s["kind"] == "agent" else f"{s['actor']}: {s['event'].replace('_', ' ')}"
    return f"{s['step']}. {what}" + (f" - {s['worked_on']}" if s["worked_on"] else "")


def report_pdf(t: dict) -> bytes:
    from app.pdf_report import Report

    c, tot = t["case"], t["totals"]
    bundle = db.get_bundle(c["case_id"]) or {}
    patient = db.get_patient(bundle["patient_id"]) if bundle.get("patient_id") else None
    r = Report(f"MediShield - Case audit: {c['title']}",
               f"Case {c['case_id']}   Patient: {patient['full_name'] if patient else 'none'}   "
               f"Status: {str(c['status']).replace('_', ' ').lower()}   "
               f"Generated: {t['generated_at'][:16].replace('T', ' ')} UTC")
    r.tiles([("Runs", str(tot["runs"])), ("Agent steps", str(tot["agent_steps"])),
             ("Agent time", _ms(tot["agent_ms"])), ("Start to end", _ms(tot["wall_ms"])),
             ("Agents decided", str(tot["decision"] or "-")), ("Reviewer", str(tot["reviewer_decision"] or "-"))])
    docs = db.bundle_documents(c["case_id"]) if bundle else []
    if docs:
        r.entries("The case", [(f"Documents ({len(docs)})", "; ".join(
            f"{d['file_name']} ({str(d.get('doc_type') or 'unclassified').lower()})" for d in docs))])
    r.table("Runs", ["Run", "Started", "Trigger", "Steps", "Agent time", "Start to end", "Agents", "Reviewer"],
            [0.06, 0.17, 0.25, 0.08, 0.11, 0.11, 0.11, 0.11],
            [[x["run"], _t(x["started_at"])[:19], x["trigger"], x["steps"], _ms(x["agent_ms"]), _ms(x["wall_ms"]),
              x["decision"] or "-", x["reviewer_decision"] or "-"] for x in t["runs"]])
    r.table("Time by agent (in the order they were called)", ["Agent", "Calls", "Total", "Average", "Longest", "Share", "Errors"],
            [0.22, 0.1, 0.14, 0.14, 0.14, 0.13, 0.13],
            [[a["agent"], a["calls"], _ms(a["total_ms"]), _ms(a["avg_ms"]), _ms(a["max_ms"]), f"{a['share']:.0%}",
              a["errors"]] for a in t["by_agent"]])
    for x in t["runs"]:
        ss = [s for s in t["steps"] if s["run"] == x["run"]]
        r.timeline(f"Run {x['run']}: when each step ran ({x['trigger']}, {_t(x['started_at'])[:19]} UTC)",
                   [(_label(s), s["offset_ms"], s["duration_ms"] or 0,
                     AGENT_COLORS.get(s["actor"], OTHER_COLOR) if s["kind"] == "agent" else OTHER_COLOR,
                     _ms(s["duration_ms"]) if s["duration_ms"] is not None else "")
                    for s in ss], max(x["wall_ms"], 1))
        r.table(f"Run {x['run']}: steps in order ({len(ss)})",
                ["#", "Started", "Finished", "Who", "What / worked on", "Took", "Status"],
                [0.05, 0.13, 0.13, 0.14, 0.33, 0.11, 0.11],
                [[s["step"], _t(s["started_at"])[11:], _t(s["finished_at"])[11:] or "-", s["actor"],
                  (s["event"].replace("_", " ") if s["kind"] != "agent" else "agent run")
                  + (f" - {s['worked_on']}" if s["worked_on"] else ""),
                  _ms(s["duration_ms"]) if s["duration_ms"] is not None else "-", s["status"]] for s in ss])
        r.entries(f"Run {x['run']}: what each step returned",
                  [(f"Step {s['step']}  ·  {s['actor']}" + (f" on {s['worked_on']}" if s["worked_on"] else "")
                    + (f"  ·  took {_ms(s['duration_ms'])}" if s["duration_ms"] is not None else "")
                    + ("  ·  FAILED" if s["status"] == "error" else ""), s["result"])
                   for s in ss if s["result"]])
    r.note("Source: MySQL table audit_event. Times are UTC. A step is one agent call (its start and finish rows merged) "
           "or one event. 'Took' is the agent's own measured time; 'Agent time' adds up every agent step; "
           "'Start to end' runs from the first event to the last, including waits for people.")
    return r.output()
