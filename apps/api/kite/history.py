"""One process-local historical request budget per owner, including shared readers.

Kite documents 3 historical calls/s. Reserve starts 350ms apart, including
failed requests. Background bootstrap and foreground history use the same gate.
No candle data, tokens or credentials live in this module.
"""
import threading
import time


class HistoryGate:
    def __init__(self, *, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.next_at = {}

    def fetch(self, owner_id, client, token, interval, start, end):
        with self.lock:
            now = self.clock()
            wait = max(0., self.next_at.get(owner_id, now) - now)
            self.next_at[owner_id] = now + wait + .35
        if wait:
            self.sleep(wait)
        return client.historical(token, interval, start, end)


gate = HistoryGate()
