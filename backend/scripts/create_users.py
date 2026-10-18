"""
Create or reset the two built-in accounts. Passwords come from ADMIN_PASSWORD and
AGENT_PASSWORD in .env. If unset, a random password is generated and printed once.

    cd backend && ../.venv/Scripts/python.exe -m scripts.create_users
"""

from __future__ import annotations

import os
import secrets

from app import db
from app.auth import hash_password

ACCOUNTS = [
    ("admin", "admin", "ADMIN_PASSWORD", "Administrator"),
    ("agent", "agent", "AGENT_PASSWORD", "Claims Agent"),
]


def main() -> None:
    db.init_db()
    for username, role, env_key, full_name in ACCOUNTS:
        password = os.getenv(env_key)
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)
        db.upsert_user(username, hash_password(password), role, full_name)
        note = "generated, shown once" if generated else "from .env"
        print(f"{username} ({role}) password {note}: {password if generated else '********'}")


if __name__ == "__main__":
    main()
