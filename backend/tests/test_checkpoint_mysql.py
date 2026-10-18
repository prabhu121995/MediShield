"""
MySQLSaver: a run paused for human review must survive a restart and resume from MySQL.
The db functions are replaced by in-memory tables, so this runs offline.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_checkpoint_mysql -v
"""

from __future__ import annotations

import operator
import unittest
from typing import Annotated, TypedDict
from unittest import mock

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app import checkpoint_mysql
from app.checkpoint_mysql import MySQLSaver


class FakeTables:
    """The three lg_checkpoint* tables, in memory."""
    def __init__(self):
        self.checkpoints, self.blobs, self.writes = {}, {}, {}

    def checkpoint_rows(self, thread_id):
        return [dict(checkpoint_ns=k[1], checkpoint_id=k[2], **v) for k, v in self.checkpoints.items() if k[0] == thread_id]

    def checkpoint_blob_rows(self, thread_id):
        return [dict(checkpoint_ns=k[1], channel=k[2], version=k[3], **v) for k, v in self.blobs.items() if k[0] == thread_id]

    def checkpoint_write_rows(self, thread_id):
        return [dict(checkpoint_ns=k[1], checkpoint_id=k[2], task_id=k[3], idx=k[4], **v)
                for k, v in self.writes.items() if k[0] == thread_id]

    def save_checkpoint(self, thread_id, ns, checkpoint_id, parent_id, checkpoint, metadata, blobs):
        self.checkpoints[(thread_id, ns, checkpoint_id)] = {"parent_id": parent_id, "type": checkpoint[0],
                                                            "checkpoint": checkpoint[1], "meta_type": metadata[0],
                                                            "metadata": metadata[1]}
        for channel, version, kind, value in blobs:
            self.blobs[(thread_id, ns, channel, version)] = {"type": kind, "value": value}

    def save_checkpoint_writes(self, thread_id, ns, checkpoint_id, writes):
        for task_id, idx, channel, kind, value, path in writes:
            self.writes[(thread_id, ns, checkpoint_id, task_id, idx)] = {"channel": channel, "type": kind,
                                                                          "value": value, "task_path": path}

    def delete_checkpoint_thread(self, thread_id):
        for table in (self.checkpoints, self.blobs, self.writes):
            for k in [k for k in table if k[0] == thread_id]:
                del table[k]


class S(TypedDict, total=False):
    steps: Annotated[list[str], operator.add]
    answer: str


def build(saver):
    g = StateGraph(S)
    g.add_node("work", lambda s: {"steps": ["work"]})
    g.add_node("ask", lambda s: {"answer": interrupt({"question": "approve?"}), "steps": ["ask"]})
    g.add_edge(START, "work")
    g.add_edge("work", "ask")
    g.add_edge("ask", END)
    return g.compile(checkpointer=saver)


class MySQLSaverTests(unittest.TestCase):
    def setUp(self):
        self.tables = FakeTables()
        patcher = mock.patch.object(checkpoint_mysql, "db", self.tables)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_paused_run_survives_a_restart_and_resumes(self):
        cfg = {"configurable": {"thread_id": "claim-1:abc"}}
        first = build(MySQLSaver())
        out = first.invoke({"steps": []}, cfg)
        self.assertIn("__interrupt__", out)
        self.assertTrue(self.tables.checkpoints)                      # written through to "MySQL"

        restarted = build(MySQLSaver())                               # new process: empty memory
        self.assertEqual(restarted.get_state(cfg).next, ("ask",))     # loaded from the tables
        final = restarted.invoke(Command(resume="APPROVE"), cfg)
        self.assertEqual(final["answer"], "APPROVE")
        self.assertEqual(final["steps"], ["work", "ask"])             # "work" did not run again
        self.assertEqual(build(MySQLSaver()).get_state(cfg).next, ())

    def test_delete_thread_removes_rows(self):
        cfg = {"configurable": {"thread_id": "t-del"}}
        saver = MySQLSaver()
        build(saver).invoke({"steps": []}, cfg)
        saver.delete_thread("t-del")
        self.assertFalse(any(k[0] == "t-del" for k in self.tables.checkpoints))


if __name__ == "__main__":
    unittest.main()
