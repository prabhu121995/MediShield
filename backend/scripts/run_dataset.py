"""
Run the whole dataset through the claim pipeline, one claim per case cluster, and score it.

Each case cluster in dataset/metadata.json (ID, claim form, discharge summary, prescription,
amendment of one patient) is uploaded as ONE claim through the API, exactly as the UI's
single upload button does: no document types are sent, the classifier decides. Each of
the out-of-distribution documents in dataset/unknown/ is its own claim.

Expected claim decision: each page's label is derived from its metadata flags with the
orchestrator's precedence (dataset_summary.md section 7.2), and the claim takes the
strongest one, ESCALATE > REJECT > APPROVE.

    cd backend
    ../.venv/Scripts/python.exe -m scripts.run_dataset                 # all 34 claims
    ../.venv/Scripts/python.exe -m scripts.run_dataset --clusters C_001 C_005
    ../.venv/Scripts/python.exe -m scripts.run_dataset --keep-history  # do not clear stored claims first

Writes storage/dataset_run/claims.csv, documents.csv and scorecard.md.
"""

from __future__ import annotations

import logging

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from app import config, db
from app import logdb

ORDER = {"APPROVE": 0, "REJECT": 1, "ESCALATE": 2}
DOC_TYPE_BY_CATEGORY = {"claim_forms": "CLAIM_FORM", "id_documents": "ID_DOCUMENT",
                        "discharge_summaries": "DISCHARGE_SUMMARY", "prescriptions": "PRESCRIPTION",
                        "policy_amendments": "POLICY_AMENDMENT", "unknown": "UNKNOWN"}
OUT_DIR = config.STORAGE_DIR / "dataset_run"


log = logging.getLogger("medishield.run_dataset")


def expected_for_doc(m: dict) -> tuple[str, str]:
    """(decision, reason) for one page, from its metadata flags."""
    flags = m.get("edge_flags") or []
    if m["category"] == "unknown":
        return "ESCALATE", "out of distribution"
    if m.get("fraud_label"):
        return "ESCALATE", f"fraud: {m.get('fraud_reason')}"
    if "tampered_id" in flags:
        return "ESCALATE", "tampered ID"
    if m.get("blur_simulated"):
        return "ESCALATE", "blurry scan"
    for flag, why in (("expired_id", "expired ID"), ("uncovered_procedure", "uncovered procedure"),
                      ("missing_fields", "missing fields")):
        if flag in flags:
            return "REJECT", why
    if any(f.startswith("duplicate_of") for f in flags):
        return "REJECT", "duplicate submission"
    return "APPROVE", "clean"


def build_claims(meta: list[dict], only: set[str] | None) -> list[dict]:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for m in meta:
        key = m.get("case_cluster_id") or m["doc_id"]          # unknown docs: one claim each
        clusters[key].append(m)
    claims = []
    for key in sorted(clusters, key=lambda k: (not k.startswith("C_"), k)):
        if only and key not in only:
            continue
        docs = sorted(clusters[key], key=lambda m: (m["doc_id"].endswith("_dup"), m["doc_id"]))
        labels = [expected_for_doc(m) for m in docs]
        decision = max((d for d, _ in labels), key=ORDER.get)
        reasons = sorted({r for d, r in labels if d == decision and r != "clean"})
        claims.append({"key": key, "patient": docs[0].get("patient_id") or "-", "docs": docs,
                       "expected": decision, "expected_reason": ", ".join(reasons) or "clean",
                       "fraud": sorted({m["fraud_reason"] for m in docs if m.get("fraud_label")})})
    return claims


def file_of(m: dict) -> Path:
    # metadata stores absolute Windows paths; resolve from the dataset folder instead.
    folder = "unknown" if m["category"] == "unknown" else m["category"]
    return config.DATASET_DIR / folder / f"{m['doc_id']}.png"


def login(client: httpx.Client, user: str, password: str) -> None:
    res = client.post("/api/auth/login", json={"username": user, "password": password})
    res.raise_for_status()
    client.headers["Authorization"] = f"Bearer {res.json()['token']}"


def submit(client: httpx.Client, claim: dict) -> str:
    files = [("files", (file_of(m).name, file_of(m).read_bytes(), "image/png")) for m in claim["docs"]]
    res = client.post("/api/claim-bundles", data={"title": f"{claim['key']} · {claim['patient']}"}, files=files)
    res.raise_for_status()
    return res.json()["bundle_id"]


def wait(client: httpx.Client, bundle_id: str, timeout_s: int) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            body = client.get(f"/api/claim-bundles/{bundle_id}").json()
        except httpx.TransportError:                 # API restarting: the claim is requeued, keep waiting
            time.sleep(5)
            continue
        if body.get("status") not in ("QUEUED", "PROCESSING"):
            return body
        time.sleep(1.5)
    raise TimeoutError(f"claim {bundle_id} still running after {timeout_s}s")


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}% ({n}/{d})" if d else "n/a"


def scorecard(rows: list[dict], doc_rows: list[dict]) -> str:
    n = len(rows)
    correct = sum(r["match"] for r in rows)
    lines = ["# Dataset run: claim pipeline scorecard", "",
             f"Run at {time.strftime('%Y-%m-%d %H:%M')}. One claim per case cluster, uploaded through "
             "`POST /api/claim-bundles` with no document types (the classifier decides).", "",
             "## Headline", "",
             "| Measure | Result |", "|---|---|",
             f"| Claim decision accuracy | {pct(correct, n)} |"]
    docs_known = [d for d in doc_rows]
    lines.append(f"| Page classification accuracy | {pct(sum(d['match'] for d in docs_known), len(docs_known))} |")
    fraud_rows = [r for r in rows if r["expected_fraud"]]
    hit = sum(1 for r in fraud_rows if set(r["expected_fraud"].split(", ")) <= set(r["predicted_tags"].split(", ")))
    lines.append(f"| Fraud tag recall (injected fraud clusters) | {pct(hit, len(fraud_rows))} |")
    always_approve = sum(1 for r in rows if r["expected"] == "APPROVE")
    lines.append(f"| Baseline: always APPROVE | {pct(always_approve, n)} |")
    lines += ["", "## Per-class recall (the imbalance trap: read these, not only the headline)", "",
              "| Expected | Recall | Predicted as |", "|---|---|---|"]
    for cls in ("APPROVE", "REJECT", "ESCALATE"):
        mine = [r for r in rows if r["expected"] == cls]
        spread = Counter(r["predicted"] for r in mine)
        lines.append(f"| {cls} | {pct(spread.get(cls, 0), len(mine))} | "
                     + ", ".join(f"{k} {v}" for k, v in spread.most_common()) + " |")
    lines += ["", "## Confusion matrix (rows expected, columns predicted)", "",
              "| | APPROVE | REJECT | ESCALATE |", "|---|---|---|---|"]
    for exp in ("APPROVE", "REJECT", "ESCALATE"):
        c = Counter(r["predicted"] for r in rows if r["expected"] == exp)
        lines.append(f"| **{exp}** | {c.get('APPROVE', 0)} | {c.get('REJECT', 0)} | {c.get('ESCALATE', 0)} |")
    lines += ["", "## Classification by document type", "", "| Type | Accuracy |", "|---|---|"]
    for t in sorted({d["expected_type"] for d in doc_rows}):
        mine = [d for d in doc_rows if d["expected_type"] == t]
        lines.append(f"| {t} | {pct(sum(d['match'] for d in mine), len(mine))} |")
    lines += ["", "## Every claim", "",
              "| Claim | Expected | Why | Predicted | Conf. | Fraud tags | Justification |",
              "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "" if r["match"] else " ✗"
        lines.append(f"| {r['claim']} | {r['expected']} | {r['expected_reason']} | **{r['predicted']}**{mark} | "
                     f"{r['confidence']} | {r['predicted_tags'] or '-'} | {r['justification'][:220]} |")
    misses = [d for d in doc_rows if not d["match"]]
    if misses:
        lines += ["", "## Misclassified pages", "", "| Page | Expected | Predicted |", "|---|---|---|"]
        lines += [f"| {d['doc_id']} | {d['expected_type']} | {d['predicted_type']} |" for d in misses]
    return "\n".join(lines) + "\n"


def main() -> None:
    logdb.install("run_dataset")      # logs go to MySQL (app_log)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=config.API_URL)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default=os.getenv("ADMIN_PASSWORD"))
    ap.add_argument("--clusters", nargs="*", help="only these cluster ids (or unknown doc ids)")
    ap.add_argument("--keep-history", action="store_true",
                    help="keep stored claims; by default they are cleared so a re-run is not flagged as a duplicate")
    ap.add_argument("--timeout", type=int, default=config.DATASET_RUN_TIMEOUT_S, help="seconds to wait for one claim")
    args = ap.parse_args()
    if not args.password:
        sys.exit("set ADMIN_PASSWORD in .env or pass --password")

    meta = json.loads((config.DATASET_DIR / "metadata.json").read_text(encoding="utf-8"))
    claims = build_claims(meta, set(args.clusters) if args.clusters else None)
    if not args.keep_history:
        db.clear_claim_history()
        log.info("cleared stored claim history")

    with httpx.Client(base_url=args.api, timeout=60) as client:
        login(client, args.user, args.password)
        for c in claims:                                        # queue everything; one worker runs them in order
            c["bundle_id"] = submit(client, c)
            log.info(f"queued {c['key']:28} {len(c['docs'])} page(s) -> {c['bundle_id']}")
        rows, doc_rows = [], []
        for c in claims:
            body = wait(client, c["bundle_id"], args.timeout)
            result = body.get("result") or {}
            out = result.get("decision") or {}
            predicted = body.get("decision") or "FAILED"
            tags = (result.get("fraud") or {}).get("tags") or []
            rows.append({"claim": c["key"], "patient": c["patient"], "bundle_id": c["bundle_id"],
                         "pages": len(c["docs"]), "expected": c["expected"], "expected_reason": c["expected_reason"],
                         "predicted": predicted, "match": predicted == c["expected"],
                         "confidence": out.get("confidence"), "expected_fraud": ", ".join(c["fraud"]),
                         "predicted_tags": ", ".join(tags),
                         "justification": (out.get("justification") or result.get("error") or "").replace("\n", " ")})
            by_name = {d["file_name"]: d for d in body.get("documents") or []}
            for m in c["docs"]:
                d = by_name.get(file_of(m).name) or {}
                exp_type = DOC_TYPE_BY_CATEGORY[m["category"]]
                doc_rows.append({"claim": c["key"], "doc_id": m["doc_id"], "expected_type": exp_type,
                                 "predicted_type": d.get("doc_type"), "match": d.get("doc_type") == exp_type})
            mark = "ok " if rows[-1]["match"] else "MISS"
            log.info(f"{mark} {c['key']:28} expected {c['expected']:8} got {predicted:8} "
                  f"{rows[-1]['justification'][:110]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, data in (("claims.csv", rows), ("documents.csv", doc_rows)):
        with open(OUT_DIR / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    (OUT_DIR / "scorecard.md").write_text(scorecard(rows, doc_rows), encoding="utf-8")
    correct = sum(r["match"] for r in rows)
    log.info(f"claim decisions {correct}/{len(rows)} correct; report: {OUT_DIR / 'scorecard.md'}")


if __name__ == "__main__":
    main()
