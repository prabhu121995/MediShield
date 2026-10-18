"""MySQLLogHandler: records are buffered and written to app_log in batches; a DB failure is swallowed."""

from __future__ import annotations

import logging
import unittest
from unittest import mock

from app import db
from app.logdb import MySQLLogHandler


class LogHandlerTests(unittest.TestCase):
    def setUp(self):
        self.handler = MySQLLogHandler("test")
        self.logger = logging.getLogger("medishield.test-logdb")
        self.logger.propagate = False
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)
        self.addCleanup(self.logger.removeHandler, self.handler)
        self.addCleanup(self.handler.close)

    def test_records_are_written_with_level_source_and_traceback(self):
        written = []
        with mock.patch.object(db, "insert_logs", side_effect=lambda rows: written.extend(rows)):
            self.logger.info("claim %s queued", "c1")
            try:
                raise ValueError("boom")
            except ValueError:
                self.logger.exception("claim failed")
            self.handler.flush()
        self.assertEqual([(r["level"], r["message"], r["source"]) for r in written],
                         [("INFO", "claim c1 queued", "test"), ("ERROR", "claim failed", "test")])
        self.assertIn("ValueError: boom", written[1]["exc_text"])

    def test_database_failure_is_swallowed(self):
        with mock.patch.object(db, "insert_logs", side_effect=ConnectionError("down")):
            self.logger.warning("still fine")
            self.handler.flush()                       # must not raise


if __name__ == "__main__":
    unittest.main()
