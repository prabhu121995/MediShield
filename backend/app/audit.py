"""
Audit log (MySQL table audit_event): one timestamped row per agent step, pipeline event and
human action. Claims use record(); other entities (patient, policy, case, user) use
record_entity().

Agents write through `record()` as they finish, so the trail is visible while a claim is
still running. The sink is swappable, so tests capture events in memory instead of MySQL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger("medishield.audit")

Sink = Callable[[dict[str, Any]], None]
_sink: Sink | None = None


def _db_sink(event: dict[str, Any]) -> None:
    from app import db
    db.insert_audit_event(event)


def set_sink(sink: Sink | None) -> None:
    global _sink
    _sink = sink


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def record(claim_id: str | None, actor: str, event: str, status: str = "ok",
           detail: dict[str, Any] | None = None, doc_id: str | None = None,
           duration_ms: int | None = None) -> None:
    """A claim event. Never raises: a failed audit write is logged, it does not fail the claim."""
    if claim_id:
        record_entity("claim", claim_id, actor, event, status, detail, doc_id, duration_ms)


def record_entity(entity_type: str, entity_id: str | None, actor: str, event: str, status: str = "ok",
                  detail: dict[str, Any] | None = None, doc_id: str | None = None,
                  duration_ms: int | None = None) -> None:
    """Any audited action. Never raises."""
    if not entity_id:
        return
    row = {"entity_type": entity_type, "entity_id": entity_id,
           "claim_id": entity_id if entity_type == "claim" else None, "doc_id": doc_id, "actor": actor,
           "event": event, "status": status, "detail": detail or {}, "duration_ms": duration_ms,
           "created_at": now()}
    try:
        (_sink or _db_sink)(row)
    except Exception:
        log.exception("audit write failed for %s %s %s", entity_type, entity_id, event)
