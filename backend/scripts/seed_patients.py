"""
Create one patient per patient in dataset/metadata.json.

    cd backend && ../.venv/Scripts/python.exe -m scripts.seed_patients

- metadata.json gives the patient id (used as the insurance member id) and policy number.
- Name and date of birth come from the member table, which seed_members filled from the
  same patients' ID documents. Run seed_members first.
- No duplicates: a member id that already has a patient is skipped, so this is safe to
  run again.
"""

from __future__ import annotations

import logging

import json
import uuid
from typing import Any

from app import config, db
from app import logdb

SEED_USER = "seed"


log = logging.getLogger("medishield.seed_patients")


def plan_patients(documents: list[dict[str, Any]], members: dict[str, dict],
                  registered: set[str]) -> tuple[list[dict], list[str], list[str]]:
    """Returns (patients to create, member ids skipped as already registered, ids with no member row)."""
    policies: dict[str, str] = {}
    for doc in documents:
        pid = doc.get("patient_id")
        if pid and pid not in policies:
            policies[pid] = doc.get("policy_number")

    create, skipped, missing = [], [], []
    for pid in sorted(policies):
        if pid in registered:
            skipped.append(pid)
            continue
        member = members.get(pid)
        if member is None:
            missing.append(pid)
            continue
        dob = member.get("date_of_birth")
        create.append({
            "member_id": pid,
            "full_name": member["full_name"],
            "date_of_birth": dob.isoformat() if hasattr(dob, "isoformat") else dob,
            "policy_number": policies[pid] or member.get("policy_number"),
            "gender": None, "phone": None, "email": None, "address": None,
        })
        registered.add(pid)                          # guards against repeats inside one run
    return create, skipped, missing


def main() -> None:
    logdb.install("seed_patients")      # logs go to MySQL (app_log)
    db.init_db()
    documents = json.loads((config.DATASET_DIR / "metadata.json").read_text(encoding="utf-8"))
    members = {m["member_id"]: m for m in db._fetch(
        "SELECT member_id, full_name, date_of_birth, policy_number FROM member")}
    registered = {r["member_id"] for r in db._fetch(
        "SELECT member_id FROM patient WHERE member_id IS NOT NULL")}

    create, skipped, missing = plan_patients(documents, members, registered)
    for patient in create:
        db.insert_patient(str(uuid.uuid4()), patient, created_by=SEED_USER)

    log.info(f"created {len(create)} patients, skipped {len(skipped)} already registered, "
          f"{len(missing)} with no member record")
    if missing:
        log.info("no member record (run scripts.seed_members first): " + ", ".join(missing))


if __name__ == "__main__":
    main()
