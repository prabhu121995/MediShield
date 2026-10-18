"""
Link stored claims that have no patient to the member their pages name, the same way a
finishing claim run does (service.link_patient). Safe to re-run: linked claims are skipped.

    cd backend && ../.venv/Scripts/python.exe -m scripts.link_patients
"""

from __future__ import annotations

from app import db
from app.claim.service import link_patient


def main() -> None:
    for b in db.list_bundles(limit=100000):
        if b.get("patient_id"):
            continue
        result = (db.get_bundle(b["bundle_id"]) or {}).get("result") or {}
        patient_id = link_patient(b["bundle_id"], result.get("documents") or [],
                                  (result.get("kyc") or {}).get("member"))
        print(f"{b['bundle_id']}  {b['title']}  ->  {patient_id or 'no member found'}")


if __name__ == "__main__":
    main()
