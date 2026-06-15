from tokmeter import db


def make_conn(tmp_path):
    path = tmp_path / "usage.db"
    conn = db.connect(path)
    db.init_db(conn)
    return conn


def rec(**kw):
    base = dict(
        ts="2026-06-15T10:00:00+00:00",
        model="m1",
        endpoint="chat/completions",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        duration_ms=1200,
        tokens_per_sec=42.0,
        stream=0,
        status=200,
        upstream="http://127.0.0.1:8080",
    )
    base.update(kw)
    return db.UsageRecord(**base)


def test_insert_and_aggregate_by_model(tmp_path):
    conn = make_conn(tmp_path)
    db.insert_record(conn, rec(model="m1", prompt_tokens=100, completion_tokens=50))
    db.insert_record(conn, rec(model="m1", prompt_tokens=10, completion_tokens=5))
    db.insert_record(conn, rec(model="m2", prompt_tokens=7, completion_tokens=3))

    rows = {r["model"]: r for r in db.aggregate_by_model(conn)}
    assert rows["m1"]["requests"] == 2
    assert rows["m1"]["prompt_tokens"] == 110
    assert rows["m1"]["completion_tokens"] == 55
    assert rows["m2"]["requests"] == 1


def test_aggregate_by_day(tmp_path):
    conn = make_conn(tmp_path)
    db.insert_record(conn, rec(ts="2026-06-15T10:00:00+00:00", total_tokens=150))
    db.insert_record(conn, rec(ts="2026-06-15T23:00:00+00:00", total_tokens=10))
    db.insert_record(conn, rec(ts="2026-06-16T01:00:00+00:00", total_tokens=20))

    rows = {r["day"]: r for r in db.aggregate_by_day(conn)}
    assert rows["2026-06-15"]["total_tokens"] == 160
    assert rows["2026-06-16"]["total_tokens"] == 20


def test_distinct_models(tmp_path):
    conn = make_conn(tmp_path)
    db.insert_record(conn, rec(model="m1"))
    db.insert_record(conn, rec(model="m2"))
    db.insert_record(conn, rec(model="m1"))
    assert sorted(db.distinct_models(conn)) == ["m1", "m2"]


def test_filters_since_until_model(tmp_path):
    conn = make_conn(tmp_path)
    db.insert_record(conn, rec(ts="2026-06-14T10:00:00+00:00", model="m1"))
    db.insert_record(conn, rec(ts="2026-06-15T10:00:00+00:00", model="m1"))
    db.insert_record(conn, rec(ts="2026-06-16T10:00:00+00:00", model="m2"))

    rows = db.aggregate_by_model(conn, since="2026-06-15", until="2026-06-15", model="m1")
    assert len(rows) == 1
    assert rows[0]["model"] == "m1"
    assert rows[0]["requests"] == 1
