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


def test_normalizable_date_bounds_are_accepted(tmp_path):
    # SQLite's date() returns NULL for both "20260615" and "2026-6-15", which
    # silently filtered out every row instead of erroring. Normalize to the
    # ISO form these queries actually need.
    conn = make_conn(tmp_path)
    db.insert_record(conn, rec(ts="2026-06-14T10:00:00+00:00"))
    db.insert_record(conn, rec(ts="2026-06-15T10:00:00+00:00"))

    for bound in ("20260615", "2026-6-15", "2026-06-15", " 2026-06-15 "):
        rows = db.aggregate_by_model(conn, since=bound)
        assert len(rows) == 1, bound
        assert rows[0]["requests"] == 1, bound


def test_parse_date_normalizes_to_iso():
    assert db.parse_date("20260615", "since") == "2026-06-15"
    assert db.parse_date("2026-6-5", "since") == "2026-06-05"


def test_unparseable_date_bound_raises(tmp_path):
    conn = make_conn(tmp_path)
    db.insert_record(conn, rec(ts="2026-06-15T10:00:00+00:00"))

    for bound in ("last tuesday", "06/15/2026", "2026-13-01", "20260615x", ""):
        for kwargs in ({"since": bound}, {"until": bound}):
            if not bound:  # falsy bounds mean "no filter", not a bad value
                assert db.aggregate_by_model(conn, **kwargs)
                continue
            try:
                db.aggregate_by_model(conn, **kwargs)
            except db.DateFormatError:
                pass
            else:
                raise AssertionError(f"{kwargs} should have been rejected")


def test_rows_for_energy_validates_bounds(tmp_path):
    conn = make_conn(tmp_path)
    db.insert_record(conn, rec(ts="2026-06-15T10:00:00+00:00", duration_ms=1200))
    assert len(db.rows_for_energy(conn, since="20260615")) == 1
    try:
        db.rows_for_energy(conn, since="nope")
    except db.DateFormatError:
        pass
    else:
        raise AssertionError("rows_for_energy should reject a bad --since")
