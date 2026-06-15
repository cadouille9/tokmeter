import csv
import io

from rich.console import Console

from tokmeter import report
from tokmeter import pricing as pricing_for_tests

REFS = pricing_for_tests.reference_rates(
    {
        "references": {
            "opus": {"input_per_1m": 10.0, "output_per_1m": 20.0},
            "haiku": {"input_per_1m": 1.0, "output_per_1m": 2.0},
        }
    }
)


def render_to_text(table, width=200):
    console = Console(file=io.StringIO(), width=width)
    console.print(table)
    return console.file.getvalue()

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


def test_render_table_model_view_marks_default_pricing():
    rows = report.build_rows(AGG, PRICING, key="model")  # m1 mapped, m2 default
    out = render_to_text(report.render_table(rows, key="model", title="t"))
    assert "default" in out  # m2 falls back to default pricing


def test_render_table_name_column_folds_instead_of_truncating():
    table = report.render_table(
        report.build_rows(AGG, PRICING, key="model"), key="model", title="t"
    )
    # overflow="fold" preserves the full name when space is tight, instead of
    # silently dropping characters with an ellipsis.
    assert table.columns[0].overflow == "fold"


def test_build_comparison_computes_cost_and_sorts_desc():
    rows = report.build_comparison(1_000_000, 500_000, REFS)
    # opus: 10*1 + 20*0.5 = 20.0 ; haiku: 1*1 + 2*0.5 = 2.0
    assert rows[0]["reference"] == "opus"
    assert round(rows[0]["would_cost"], 4) == 20.0
    assert rows[1]["reference"] == "haiku"
    assert round(rows[1]["would_cost"], 4) == 2.0
    assert rows[0]["input_per_1m"] == 10.0
    assert rows[0]["output_per_1m"] == 20.0


def test_build_comparison_empty_references():
    assert report.build_comparison(1000, 1000, []) == []
