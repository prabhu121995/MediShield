# Tests never write audit rows to the real database; tests that check audit rows set their own sink.
from app import audit

audit.set_sink(lambda event: None)
