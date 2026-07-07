from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id                INTEGER PRIMARY KEY,
  ts                TEXT NOT NULL,
  model             TEXT,
  endpoint          TEXT,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  total_tokens      INTEGER,
  duration_ms       INTEGER,
  tokens_per_sec    REAL,
  stream            INTEGER,
  status            INTEGER,
  upstream          TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_model ON requests(model);
"""


@dataclass
class UsageRecord:
    ts: str
    model: str | None
    endpoint: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    duration_ms: int | None
    tokens_per_sec: float | None
    stream: int
    status: int
    upstream: str | None


def connect(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_record(conn: sqlite3.Connection, record: UsageRecord) -> None:
    d = asdict(record)
    cols = ", ".join(d.keys())
    placeholders = ", ".join(f":{k}" for k in d.keys())
    conn.execute(f"INSERT INTO requests ({cols}) VALUES ({placeholders})", d)
    conn.commit()


def _where(since=None, until=None, model=None):
    clauses, params = [], {}
    if since:
        clauses.append("date(ts) >= date(:since)")
        params["since"] = since
    if until:
        clauses.append("date(ts) <= date(:until)")
        params["until"] = until
    if model:
        clauses.append("model = :model")
        params["model"] = model
    sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def aggregate_by_model(conn, since=None, until=None, model=None) -> list[dict]:
    where, params = _where(since, until, model)
    rows = conn.execute(
        f"""
        SELECT model,
               COUNT(*) AS requests,
               COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens),0) AS completion_tokens,
               COALESCE(SUM(total_tokens),0) AS total_tokens
        FROM requests{where}
        GROUP BY model
        ORDER BY total_tokens DESC
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def aggregate_by_day(conn, since=None, until=None, model=None) -> list[dict]:
    where, params = _where(since, until, model)
    rows = conn.execute(
        f"""
        SELECT date(ts) AS day,
               COUNT(*) AS requests,
               COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens),0) AS completion_tokens,
               COALESCE(SUM(total_tokens),0) AS total_tokens
        FROM requests{where}
        GROUP BY date(ts)
        ORDER BY day
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def distinct_models(conn) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT model FROM requests WHERE model IS NOT NULL"
    ).fetchall()
    return [r["model"] for r in rows]


def rows_for_energy(conn, since=None, until=None, model=None) -> list[tuple]:
    where, params = _where(since, until, model)
    prefix = " AND " if where else " WHERE "
    rows = conn.execute(
        f"SELECT model, ts, duration_ms FROM requests{where}{prefix}"
        "duration_ms IS NOT NULL AND duration_ms > 0",
        params,
    ).fetchall()
    return [(r["model"], r["ts"], r["duration_ms"]) for r in rows]
