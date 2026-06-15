from tokmeter import cli, db


def seed(tmp_path):
    path = tmp_path / "usage.db"
    conn = db.connect(path)
    db.init_db(conn)
    conn.execute(
        "INSERT INTO requests (ts,model,endpoint,prompt_tokens,completion_tokens,total_tokens,"
        "duration_ms,tokens_per_sec,stream,status,upstream) VALUES "
        "('2026-06-15T10:00:00+00:00','m1','chat/completions',1000000,500000,1500000,10,1.0,0,200,'u')"
    )
    conn.commit()
    return path


def test_report_by_model(tmp_path, monkeypatch, capsys):
    path = seed(tmp_path)
    monkeypatch.setattr(cli.config, "db_path", lambda: path)
    monkeypatch.setattr(cli.config, "pricing_path", lambda: tmp_path / "missing.yaml")

    rc = cli.main(["report", "--by", "model"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m1" in out
    assert "Saved" in out


def test_report_csv_export(tmp_path, monkeypatch):
    path = seed(tmp_path)
    out_csv = tmp_path / "report.csv"
    monkeypatch.setattr(cli.config, "db_path", lambda: path)
    monkeypatch.setattr(cli.config, "pricing_path", lambda: tmp_path / "missing.yaml")

    rc = cli.main(["report", "--by", "model", "--csv", str(out_csv)])
    assert rc == 0
    assert out_csv.exists()
    assert "saved_usd" in out_csv.read_text()


def test_models_lists_mapped_state(tmp_path, monkeypatch, capsys):
    path = seed(tmp_path)
    monkeypatch.setattr(cli.config, "db_path", lambda: path)
    monkeypatch.setattr(cli.config, "pricing_path", lambda: tmp_path / "missing.yaml")

    rc = cli.main(["models"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m1" in out
    # The pricing-state label must survive Rich rendering (regression: [default]
    # was being interpreted as Rich markup and silently dropped).
    assert "[default]" in out


def seed_pricing_with_references(tmp_path):
    p = tmp_path / "pricing.yaml"
    p.write_text(
        "references:\n"
        "  opus:\n"
        "    input_per_1m: 10.0\n"
        "    output_per_1m: 20.0\n"
        "  haiku:\n"
        "    input_per_1m: 1.0\n"
        "    output_per_1m: 2.0\n"
    )
    return p


def test_compare_totals(tmp_path, monkeypatch, capsys):
    path = seed(tmp_path)
    pp = seed_pricing_with_references(tmp_path)
    monkeypatch.setattr(cli.config, "db_path", lambda: path)
    monkeypatch.setattr(cli.config, "pricing_path", lambda: pp)

    rc = cli.main(["compare"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Recorded usage" in out
    assert "1,000,000" in out  # prompt-token total from seed()
    assert "opus" in out
    assert "$" in out  # a would-have-cost figure was rendered


def test_compare_by_model(tmp_path, monkeypatch, capsys):
    path = seed(tmp_path)
    pp = seed_pricing_with_references(tmp_path)
    monkeypatch.setattr(cli.config, "db_path", lambda: path)
    monkeypatch.setattr(cli.config, "pricing_path", lambda: pp)

    rc = cli.main(["compare", "--by-model"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m1" in out
    assert "opus" in out
    assert "$" in out  # per-model cost cells were rendered


def test_compare_without_references_shows_guidance(tmp_path, monkeypatch, capsys):
    path = seed(tmp_path)
    monkeypatch.setattr(cli.config, "db_path", lambda: path)
    monkeypatch.setattr(cli.config, "pricing_path", lambda: tmp_path / "missing.yaml")

    rc = cli.main(["compare"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No reference models" in out
