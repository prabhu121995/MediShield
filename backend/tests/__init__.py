# Tests never send traces to LangSmith. Set before app.config loads .env (which does not override).
import os

os.environ["LANGSMITH_TRACING"] = "false"

# Tests never write audit rows to the real database; tests that check audit rows set their own sink.
from app import audit

audit.set_sink(lambda event: None)

# ...nor log rows: install() sees a handler already in place and does nothing.
import logging

from app import logdb

logdb._handler = logging.NullHandler()
