"""
Log analytics over the MySQL table app_log: aggregates for the System logs page, a CSV export
of the raw rows, and a PDF report of the analysis (fpdf2, drawn server-side).

Time ranges: 24h | 7d | 30d | all. Hourly buckets up to 48 hours, daily beyond. Request
statistics are parsed from uvicorn access lines ('ip - "GET /path HTTP/1.1" 200'); ids in
paths are folded to {id} so one endpoint is one row. Limits come from .env
(LOG_EXPORT_MAX_ROWS, LOG_STATS_SCAN_MAX, LOG_TOP_N).
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app import config, db

RANGES = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}
LEVELS = ["ERROR", "WARNING", "INFO", "DEBUG"]            # CRITICAL is counted with ERROR
ACCESS_RE = re.compile(r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<status>\d{3})')
ID_RE = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+|PT_\d+)(?=/|$)", re.I)
NOISE_RE = re.compile(r"\b(?:[0-9a-f]{8}-[0-9a-f-]{27}|\d+(?:\.\d+)?)\b", re.I)

# Level colours: the status palette for ERROR / WARNING, series blue for INFO, a neutral for DEBUG.
LEVEL_COLORS = {"ERROR": "#d03b3b", "WARNING": "#fab219", "INFO": "#2a78d6", "DEBUG": "#898781"}


def since_of(range_key: str) -> datetime | None:
    span = RANGES.get(range_key)
    return (datetime.now(timezone.utc) - span).replace(tzinfo=None) if span else None


def bucket_plan(table: str, since: datetime | None, where: str, args: list) -> tuple[bool, str]:
    """(hourly?, DATE_FORMAT pattern): hourly buckets up to 48 hours of data, daily beyond."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if since:
        hours = (now - since).total_seconds() / 3600
    else:
        first = db._fetch(f"SELECT MIN(created_at) AS t FROM {table} {where}", tuple(args))[0]["t"]
        hours = (now - first).total_seconds() / 3600 if first else 0
    hourly = hours <= 48
    return hourly, ("%Y-%m-%d %H:00" if hourly else "%Y-%m-%d")


def fill_buckets(series: dict, since: datetime | None, hourly: bool, keys: list[str]) -> list[dict]:
    """A continuous time axis: every hour (or day) in range, zero where nothing happened."""
    if series:
        step = timedelta(hours=1) if hourly else timedelta(days=1)
        parse = "%Y-%m-%d %H:00" if hourly else "%Y-%m-%d"
        first = min(datetime.strptime(b, parse) for b in series)
        if since:
            first = min(first, since.replace(minute=0, second=0, microsecond=0) if hourly
                        else since.replace(hour=0, minute=0, second=0, microsecond=0))
        last = datetime.now(timezone.utc).replace(tzinfo=None)
        cursor = first
        while cursor <= last:
            series.setdefault(cursor.strftime(parse), {k: 0 for k in keys})
            cursor += step
    return [{"bucket": b, **{k: counts.get(k, 0) for k in keys}} for b, counts in sorted(series.items())]


def _where(since: datetime | None, source: str | None, level: str | None, q: str | None = None) -> tuple[str, list]:
    clauses, args = [], []
    if since:
        clauses.append("created_at >= %s")
        args.append(since)
    if source:
        clauses.append("source = %s")
        args.append(source)
    if level:
        clauses.append("level IN ('ERROR','CRITICAL')" if level == "ERROR" else "level = %s")
        if level != "ERROR":
            args.append(level)
    if q:
        clauses.append("message LIKE %s")
        args.append(f"%{q}%")
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", args


def _norm_level(level: str) -> str:
    return "ERROR" if level in ("ERROR", "CRITICAL") else level if level in LEVELS else "DEBUG"


def stats(range_key: str = "24h", source: str | None = None, level: str | None = None) -> dict:
    since = since_of(range_key)
    where, args = _where(since, source, level)
    hourly, fmt = bucket_plan("app_log", since, where, args)

    rows = db._fetch(f"SELECT DATE_FORMAT(created_at, %s) AS bucket, level, COUNT(*) AS n FROM app_log {where} "
                     "GROUP BY bucket, level ORDER BY bucket", (fmt, *args))
    series: dict[str, dict[str, int]] = defaultdict(lambda: {lv: 0 for lv in LEVELS})
    totals = Counter()
    for r in rows:
        lv = _norm_level(r["level"])
        series[r["bucket"]][lv] += int(r["n"])
        totals[lv] += int(r["n"])
    volume = fill_buckets(series, since, hourly, LEVELS)

    by_source = [{"source": r["source"], "count": int(r["n"])} for r in
                 db._fetch(f"SELECT source, COUNT(*) AS n FROM app_log {where} GROUP BY source ORDER BY n DESC", tuple(args))]

    # Requests, from uvicorn access lines.
    access_where, access_args = _where(since, source, None)
    access_where += (" AND " if access_where else "WHERE ") + "logger = 'uvicorn.access'"
    lines = db._fetch(f"SELECT message FROM app_log {access_where} ORDER BY id DESC LIMIT %s",
                      (*access_args, config.LOG_STATS_SCAN_MAX))
    status_classes = Counter()
    endpoints: dict[str, Counter] = defaultdict(Counter)
    for line in lines:
        m = ACCESS_RE.search(line["message"])
        if not m:
            continue
        cls = f"{m['status'][0]}xx"
        status_classes[cls] += 1
        key = f"{m['method']} {ID_RE.sub('/{id}', m['path'].split('?')[0])}"
        endpoints[key]["requests"] += 1
        endpoints[key][cls] += 1
    top_endpoints = sorted(({"endpoint": k, "requests": c["requests"], "4xx": c["4xx"], "5xx": c["5xx"]}
                            for k, c in endpoints.items()), key=lambda e: -e["requests"])[:config.LOG_TOP_N]

    # Recurring errors, with ids and numbers folded so one problem is one row.
    err_where, err_args = _where(since, source, "ERROR")
    errors = db._fetch(f"SELECT message, source, created_at FROM app_log {err_where} ORDER BY id DESC LIMIT %s",
                       (*err_args, config.LOG_STATS_SCAN_MAX))
    grouped: dict[str, dict] = {}
    for e in errors:
        key = NOISE_RE.sub("#", e["message"].splitlines()[0])[:160]
        g = grouped.setdefault(key, {"message": key, "count": 0, "sources": set(),
                                     "last_seen": e["created_at"].isoformat(timespec="seconds") + "Z"})
        g["count"] += 1
        g["sources"].add(e["source"])
    top_errors = sorted(({**g, "sources": sorted(g["sources"])} for g in grouped.values()),
                        key=lambda g: -g["count"])[:config.LOG_TOP_N]

    total = sum(totals.values())
    requests = sum(status_classes.values())
    return {
        "range": range_key, "source": source, "level": level, "bucket": "hour" if hourly else "day",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {"records": total, **{lv: totals[lv] for lv in LEVELS},
                   "error_rate": round(totals["ERROR"] / total, 4) if total else 0.0,
                   "requests": requests, "server_errors": status_classes["5xx"], "sources": len(by_source)},
        "volume": volume,
        "by_source": by_source,
        "status_classes": [{"class": c, "count": status_classes[c]} for c in ("2xx", "3xx", "4xx", "5xx")],
        "top_endpoints": top_endpoints,
        "top_errors": top_errors,
        "requests_scanned": len(lines),
        "colors": LEVEL_COLORS,
    }


# ---- CSV export of the raw rows ---------------------------------------------------------

CSV_COLUMNS = ["id", "created_at", "level", "source", "logger", "message", "exc_text"]


def export_csv(range_key: str, source: str | None, level: str | None, q: str | None) -> str:
    where, args = _where(since_of(range_key), source, level, q)
    rows = db._fetch(f"SELECT {', '.join(CSV_COLUMNS)} FROM app_log {where} ORDER BY id DESC LIMIT %s",
                     (*args, config.LOG_EXPORT_MAX_ROWS))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for r in rows:
        writer.writerow([r["id"], r["created_at"].isoformat(timespec="milliseconds"), r["level"], r["source"],
                         r["logger"], r["message"], r["exc_text"] or ""])
    return "﻿" + buf.getvalue()            # BOM so Excel opens UTF-8 correctly


# ---- PDF report of the analysis ---------------------------------------------------------

def report_pdf(range_key: str, source: str | None, level: str | None) -> bytes:
    from app.pdf_report import Report

    s = stats(range_key, source, level)
    t = s["totals"]
    r = Report("MediShield - System log analysis",
               f"Range: {range_key}   Source: {source or 'all'}   Level: {level or 'all'}   Generated: {s['generated_at']} UTC")
    r.tiles([("Records", f"{t['records']:,}"), ("Errors", f"{t['ERROR']:,}"), ("Warnings", f"{t['WARNING']:,}"),
             ("Error rate", f"{t['error_rate'] * 100:.1f}%"), ("Requests", f"{t['requests']:,}"),
             ("5xx responses", f"{t['server_errors']:,}")])
    r.stacked_columns(f"Log volume per {s['bucket']}, by level", s["volume"],
                      [(lv, lv.title(), LEVEL_COLORS[lv]) for lv in reversed(LEVELS)], t, label_of=lambda b: b[5:])
    r.table("Records per source", ["Source", "Records", "Share"], [0.6, 0.2, 0.2],
            [[x["source"], f"{x['count']:,}", f"{x['count'] / t['records'] * 100:.1f}%" if t["records"] else "-"]
             for x in s["by_source"]])
    r.table("HTTP responses by status class", ["Class", "Responses"], [0.6, 0.4],
            [[x["class"], f"{x['count']:,}"] for x in s["status_classes"]])
    r.table("Top endpoints", ["Endpoint", "Requests", "4xx", "5xx"], [0.58, 0.14, 0.14, 0.14],
            [[e["endpoint"], f"{e['requests']:,}", e["4xx"], e["5xx"]] for e in s["top_endpoints"]])
    r.table("Recurring errors", ["Message", "Count", "Sources", "Last seen"], [0.52, 0.1, 0.18, 0.2],
            [[g["message"], g["count"], ", ".join(g["sources"]), g["last_seen"].replace("T", " ")[:16]]
             for g in s["top_errors"]])
    r.note(f"Source: MySQL table app_log. Requests parsed from the latest {s['requests_scanned']:,} access lines "
           "in range. Ids and numbers in error messages are folded (#).")
    return r.output()
