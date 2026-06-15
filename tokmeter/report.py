from __future__ import annotations

import csv as _csv
from pathlib import Path

from rich.table import Table

from . import pricing as pricing_mod


def build_rows(agg_rows: list[dict], pricing: dict, key: str) -> list[dict]:
    out = []
    for row in agg_rows:
        model = row.get("model")  # present for by-model; None for by-day
        rate = pricing_mod.resolve_rate(pricing, model)
        saved = pricing_mod.compute_savings(
            row.get("prompt_tokens", 0), row.get("completion_tokens", 0), rate
        )
        out.append({**row, "saved_usd": saved, "mapped": rate.mapped})
    return out


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        Path(path).write_text("")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def render_table(rows: list[dict], key: str, title: str) -> Table:
    table = Table(title=title)
    # fold (not the default ellipsis) so long model names are never silently truncated.
    table.add_column(key.capitalize(), overflow="fold")
    table.add_column("Requests", justify="right")
    table.add_column("Prompt", justify="right")
    table.add_column("Completion", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Saved $", justify="right")
    show_pricing = key == "model"
    if show_pricing:
        table.add_column("Pricing")
    for r in rows:
        cells = [
            str(r.get(key, "")),
            str(r.get("requests", "")),
            f"{r.get('prompt_tokens', 0):,}",
            f"{r.get('completion_tokens', 0):,}",
            f"{r.get('total_tokens', 0):,}",
            f"{r.get('saved_usd', 0.0):.2f}",
        ]
        if show_pricing:
            cells.append("" if r.get("mapped", True) else "default")
        table.add_row(*cells)
    return table


def totals(rows: list[dict]) -> dict:
    return {
        "requests": sum(r.get("requests", 0) for r in rows),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
        "total_tokens": sum(r.get("total_tokens", 0) for r in rows),
        "saved_usd": sum(r.get("saved_usd", 0.0) for r in rows),
    }


def build_comparison(prompt_tokens: int, completion_tokens: int, references: list) -> list[dict]:
    rows = []
    for name, rate in references:
        cost = pricing_mod.compute_savings(prompt_tokens, completion_tokens, rate)
        rows.append(
            {
                "reference": name,
                "input_per_1m": rate.input_per_1m,
                "output_per_1m": rate.output_per_1m,
                "would_cost": cost,
            }
        )
    rows.sort(key=lambda r: r["would_cost"], reverse=True)
    return rows
