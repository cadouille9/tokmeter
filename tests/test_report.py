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


def test_render_comparison_table_shows_references_and_costs():
    rows = report.build_comparison(1_000_000, 500_000, REFS)
    out = render_to_text(report.render_comparison_table(rows))
    assert "opus" in out
    assert "haiku" in out
    assert "Would-have-cost" in out
    assert "$20.00" in out


def test_build_comparison_matrix_costs_per_model():
    per_model = [
        {"model": "qwen", "prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000},
        {"model": "tiny", "prompt_tokens": 0, "completion_tokens": 1_000_000, "total_tokens": 1_000_000},
    ]
    rows = report.build_comparison_matrix(per_model, REFS)
    by = {r["model"]: r for r in rows}
    # qwen: prompt only -> opus 10*1=10, haiku 1*1=1
    assert round(by["qwen"]["costs"]["opus"], 4) == 10.0
    assert round(by["qwen"]["costs"]["haiku"], 4) == 1.0
    # tiny: completion only -> opus 20*1=20, haiku 2*1=2
    assert round(by["tiny"]["costs"]["opus"], 4) == 20.0
    assert by["qwen"]["total_tokens"] == 1_000_000


def test_render_matrix_table_has_model_and_reference_columns():
    per_model = [
        {"model": "qwen", "prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000},
    ]
    rows = report.build_comparison_matrix(per_model, REFS)
    table = report.render_matrix_table(rows, ["opus", "haiku"])
    out = render_to_text(table)
    assert table.columns[0].overflow == "fold"
    assert "qwen" in out
    assert "opus" in out
    assert "haiku" in out
    assert "$10.00" in out
