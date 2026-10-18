"""
LangGraph checkpointer that keeps every checkpoint in MySQL (tables lg_checkpoint,
lg_checkpoint_blob, lg_checkpoint_write), so a claim paused for human review survives restarts
and all run state sits in the same database as the claims.

It reuses LangGraph's InMemorySaver for the checkpoint logic and writes every change through to
MySQL. A thread that is not in memory (after a restart) is loaded from MySQL the first time it
is read. One API process owns the pipeline, so the write-through copy stays consistent.
"""

from __future__ import annotations

import threading
from typing import Any, Iterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata, CheckpointTuple
from langgraph.checkpoint.memory import InMemorySaver

from app import db


class MySQLSaver(InMemorySaver):
    def __init__(self) -> None:
        super().__init__()
        self._loaded: set[str] = set()
        self._lock = threading.RLock()

    # ---- load a thread from MySQL ----------------------------------------------------

    def _ensure_loaded(self, thread_id: str) -> None:
        if thread_id in self._loaded:
            return
        with self._lock:
            if thread_id in self._loaded:
                return
            for r in db.checkpoint_rows(thread_id):
                self.storage[thread_id][r["checkpoint_ns"]][r["checkpoint_id"]] = (
                    (r["type"], bytes(r["checkpoint"])), (r["meta_type"], bytes(r["metadata"])), r["parent_id"])
            for r in db.checkpoint_blob_rows(thread_id):
                self.blobs[(thread_id, r["checkpoint_ns"], r["channel"], r["version"])] = (r["type"], bytes(r["value"] or b""))
            for r in db.checkpoint_write_rows(thread_id):
                key = (thread_id, r["checkpoint_ns"], r["checkpoint_id"])
                self.writes[key][(r["task_id"], r["idx"])] = (r["task_id"], r["channel"], (r["type"], bytes(r["value"])),
                                                              r["task_path"])
            self._loaded.add(thread_id)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        self._ensure_loaded(config["configurable"]["thread_id"])
        return super().get_tuple(config)

    def list(self, config: RunnableConfig | None, **kwargs: Any) -> Iterator[CheckpointTuple]:
        if config:
            self._ensure_loaded(config["configurable"]["thread_id"])
        return super().list(config, **kwargs)

    def get_delta_channel_history(self, *, config: RunnableConfig, channels: Sequence[str]):
        self._ensure_loaded(config["configurable"]["thread_id"])
        return super().get_delta_channel_history(config=config, channels=channels)

    # ---- write through ---------------------------------------------------------------

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata,
            new_versions: ChannelVersions) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"]["checkpoint_ns"]
        with self._lock:
            self._ensure_loaded(thread_id)
            out = super().put(config, checkpoint, metadata, new_versions)
            ckpt, meta, parent = self.storage[thread_id][ns][checkpoint["id"]]
            db.save_checkpoint(thread_id, ns, checkpoint["id"], parent, ckpt, meta,
                               [(ch, str(v), *self.blobs[(thread_id, ns, ch, v)]) for ch, v in new_versions.items()])
        return out

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._lock:
            self._ensure_loaded(thread_id)
            super().put_writes(config, writes, task_id, task_path)
            stored = self.writes.get((thread_id, ns, checkpoint_id), {})
            db.save_checkpoint_writes(thread_id, ns, checkpoint_id,
                                      [(tid, idx, ch, typed[0], typed[1], path)
                                       for (tid, idx), (_, ch, typed, path) in stored.items() if tid == task_id])

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            super().delete_thread(thread_id)
            db.delete_checkpoint_thread(thread_id)
            self._loaded.discard(thread_id)
