"""
Seed the member and kyc tables from the dataset's own documents.

Each patient in dataset/metadata.json gets one member row. The policy number and patient
id come from metadata.json; the plan from the policy prefix (rules: plans_by_policy_prefix).
Name, date of birth and ID expiry are read from the patient's ID document by the same vision
model the pipeline uses (transcribe, then extract); date of birth falls back to the policy amendment's policyholder
record when the ID prints none. Reads are cached, so this is fast after warm_vision.

    cd backend && ../.venv/Scripts/python.exe -m scripts.seed_members
"""

from __future__ import annotations

import json

from app import config, db
from app.claim.vision import extract, transcribe


def plan_of(policy: str) -> str | None:
    return next((plan for prefix, plan in config.RULES["plans_by_policy_prefix"].items()
                 if policy.startswith(prefix)), None)


def _fields(path, doc_type: str) -> dict:
    return extract(transcribe(str(path))["text"], doc_type)["fields"] if path.is_file() else {}


def main() -> None:
    db.init_db()
    entries = json.loads((config.DATASET_DIR / "metadata.json").read_text(encoding="utf-8"))
    patients = {}
    for e in entries:
        if e.get("patient_id") and e.get("policy_number"):   # skip out-of-distribution docs
            patients.setdefault(e["patient_id"], e["policy_number"])

    for pid, policy in sorted(patients.items()):
        id_fields = _fields(config.DATASET_DIR / "id_documents" / f"id_{pid}.png", "ID_DOCUMENT")
        amend = _fields(config.DATASET_DIR / "policy_amendments" / f"amend_{pid}.png", "POLICY_AMENDMENT")
        name = id_fields.get("patient_name") or amend.get("patient_name")
        if not name:
            print(f"skip {pid}: no readable name")
            continue
        dob = id_fields.get("date_of_birth") or amend.get("date_of_birth")
        db._execute(
            "INSERT INTO member (member_id, full_name, date_of_birth, policy_number, plan) "
            "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE full_name=VALUES(full_name), "
            "date_of_birth=VALUES(date_of_birth), policy_number=VALUES(policy_number), plan=VALUES(plan)",
            (pid, name, dob, policy, plan_of(policy)),
        )
        db._execute(
            "INSERT INTO kyc (member_id, id_type, id_expiry_date, id_status) VALUES (%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE id_type=VALUES(id_type), id_expiry_date=VALUES(id_expiry_date)",
            (pid, id_fields.get("id_kind") or "ID_DOCUMENT", id_fields.get("id_expiry_date"), "ACTIVE"),
        )
        print(f"seeded {pid}: {name} dob={dob} expiry={id_fields.get('id_expiry_date')} policy={policy}", flush=True)


if __name__ == "__main__":
    main()
