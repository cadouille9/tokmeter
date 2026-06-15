from tokmeter import db
from tokmeter.writer import UsageWriter


def rec(model="m1"):
    return db.UsageRecord(
        ts="2026-06-15T10:00:00+00:00",
        model=model,
        endpoint="chat/completions",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        duration_ms=100,
        tokens_per_sec=1.0,
        stream=0,
        status=200,
        upstream="http://127.0.0.1:8080",
    )


def test_writer_persists_records(tmp_path):
    path = tmp_path / "usage.db"
    writer = UsageWriter(path)
    writer.start()
    writer.write(rec("m1"))
    writer.write(rec("m2"))
    writer.stop()  # flushes + joins

    conn = db.connect(path)
    assert sorted(db.distinct_models(conn)) == ["m1", "m2"]


def test_write_after_failure_does_not_raise(tmp_path):
    # A bad record (None ts violates NOT NULL) must be swallowed, not crash the thread.
    path = tmp_path / "usage.db"
    writer = UsageWriter(path)
    writer.start()
    bad = rec("m1")
    bad.ts = None
    writer.write(bad)
    writer.write(rec("good"))
    writer.stop()

    conn = db.connect(path)
    assert db.distinct_models(conn) == ["good"]
