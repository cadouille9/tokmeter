from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from . import db

log = logging.getLogger("tokmeter.writer")
_SENTINEL = object()


class UsageWriter:
    def __init__(self, path: Path):
        self.path = path
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="tokmeter-writer")

    def start(self) -> None:
        self._thread.start()

    def write(self, record: db.UsageRecord) -> None:
        # Never blocks the proxy; capture is best-effort.
        self._q.put(record)

    def stop(self) -> None:
        self._q.put(_SENTINEL)
        self._thread.join()

    def _run(self) -> None:
        conn = db.connect(self.path)
        db.init_db(conn)
        while True:
            item = self._q.get()
            if item is _SENTINEL:
                break
            try:
                db.insert_record(conn, item)
            except Exception:  # best-effort: log and keep going
                log.exception("failed to persist usage record")
        conn.close()
