"""
Application logs go to MySQL (table app_log), not to files.

install(source) attaches a handler to the root logger and to uvicorn's loggers. Records are
buffered and written in batches by a background thread (LOG_BATCH_SIZE, LOG_FLUSH_S in .env),
so logging never waits on the database. A failed write is dropped, never raised and never
logged again (no loops). Console output is kept so a terminal still shows what happens.
"""

from __future__ import annotations

import atexit
import logging
import threading
import traceback
from datetime import datetime, timezone

from app import config

UVICORN_LOGGERS = ("uvicorn", "uvicorn.access")      # uvicorn.error passes its records up to "uvicorn"


class MySQLLogHandler(logging.Handler):
    def __init__(self, source: str):
        super().__init__(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
        self.source = source
        self._buffer: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="log-to-mysql", daemon=True)
        self._thread.start()
        atexit.register(self.flush)

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("pymysql"):                 # never log the log writer itself
            return
        try:
            row = {
                "created_at": datetime.fromtimestamp(record.created, timezone.utc).replace(tzinfo=None),
                "level": record.levelname,
                "logger": record.name[:128],
                "message": record.getMessage()[:65000],
                "source": self.source,
                "exc_text": "".join(traceback.format_exception(*record.exc_info))[:65000] if record.exc_info else None,
            }
        except Exception:
            return
        with self._lock:
            self._buffer.append(row)
            full = len(self._buffer) >= config.LOG_BATCH_SIZE
        if full:
            threading.Thread(target=self.flush, daemon=True).start()

    def flush(self) -> None:
        with self._lock:
            batch, self._buffer = self._buffer, []
        if not batch:
            return
        try:
            from app import db
            db.insert_logs(batch)
        except Exception:
            pass                                              # the database is down: drop, do not loop

    def _run(self) -> None:
        while not self._stop.wait(config.LOG_FLUSH_S):
            self.flush()

    def close(self) -> None:
        self._stop.set()
        self.flush()
        super().close()


_handler: MySQLLogHandler | None = None


def install(source: str, console: bool = True) -> MySQLLogHandler:
    """Send every log record of this process to MySQL. Safe to call more than once."""
    global _handler
    if _handler is not None:
        return _handler
    _handler = MySQLLogHandler(source)
    root = logging.getLogger()
    root.setLevel(_handler.level)
    root.addHandler(_handler)
    if console and not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)
    for name in UVICORN_LOGGERS:                             # uvicorn does not propagate to root
        logging.getLogger(name).addHandler(_handler)
    return _handler
