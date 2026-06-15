import csv

from tokmeter import report

PRICING = {
    "default": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "models": {"m1": {"input_per_1m": 1.0, "output_per_1m": 2.0}},
}

AGG = [
    {"model": "m1", "requests": 2, "prompt_tokens": 1_000_000, "completion_tokens": 500_000, "total_tokens": 1_500_000},
    {"model": "m2", "requests": 1, "prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000},
]


def test_build_rows_adds_savings_and_mapped_flag():
    rows = report.build_rows(AGG, PRICING, key="model")
    by = {r["model"]: r for r in rows}
    # m1: 1.0 * 1 + 2.0 * 0.5 = 2.0
    assert round(by["m1"]["saved_usd"], 4) == 2.0
    assert by["m1"]["mapped"] is True
    # m2 unmapped -> default 0.15 * 1 = 0.15
    assert round(by["m2"]["saved_usd"], 4) == 0.15
    assert by["m2"]["mapped"] is False


def test_write_csv(tmp_path):
    rows = report.build_rows(AGG, PRICING, key="model")
    out = tmp_path / "out.csv"
    report.write_csv(rows, out)
    with open(out) as f:
        data = list(csv.DictReader(f))
    assert len(data) == 2
    assert "saved_usd" in data[0]


def test_render_table_returns_table_with_title():
    rows = report.build_rows(AGG, PRICING, key="model")
    table = report.render_table(rows, key="model", title="By Model")
    assert table.title == "By Model"
    assert table.row_count == 2
