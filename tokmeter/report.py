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


def render_comparison_table(rows: list[dict]) -> Table:
    table = Table(title="Would-have-cost on cloud (= savings vs each)")
    table.add_column("Reference", overflow="fold")
    table.add_column("In $/1M", justify="right")
    table.add_column("Out $/1M", justify="right")
    table.add_column("Would-have-cost", justify="right")
    for r in rows:
        table.add_row(
            str(r.get("reference", "")),
            f"{r.get('input_per_1m', 0.0):.2f}",
            f"{r.get('output_per_1m', 0.0):.2f}",
            f"${r.get('would_cost', 0.0):.2f}",
        )
    return table


def build_comparison_matrix(per_model_rows: list[dict], references: list) -> list[dict]:
    out = []
    for row in per_model_rows:
        costs = {
            name: pricing_mod.compute_savings(
                row.get("prompt_tokens", 0), row.get("completion_tokens", 0), rate
            )
            for name, rate in references
        }
        out.append(
            {
                "model": row.get("model"),
                "prompt_tokens": row.get("prompt_tokens", 0),
                "completion_tokens": row.get("completion_tokens", 0),
                "total_tokens": row.get("total_tokens", 0),
                "costs": costs,
            }
        )
    return out


def render_matrix_table(rows: list[dict], reference_names: list[str]) -> Table:
    table = Table(title="Would-have-cost by model")
    table.add_column("Model", overflow="fold")
    table.add_column("Total", justify="right")
    for name in reference_names:
        table.add_column(name, justify="right")
    for r in rows:
        cells = [str(r.get("model", "")), f"{r.get('total_tokens', 0):,}"]
        for name in reference_names:
            cells.append(f"${r['costs'].get(name, 0.0):.2f}")
        table.add_row(*cells)
    return table


def build_comparison(prompt_tokens: int, completion_tokens: int, references: list) -> list[dict]:
    rows = []
    for name, rate in references:
        # compute_savings is just tokens x rate; here that product is the
        # would-have-cost on this reference (== savings vs it, since local is free).
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


def energy_summary(pricing: dict, rows) -> tuple[dict | None, list[str]]:
    from . import energy as energy_mod

    elec, warnings = pricing_mod.electricity_config(pricing)
    if elec is None:
        return None, warnings
    hours, kwh = energy_mod.energy_kwh(
        rows, watts_for=lambda m: pricing_mod.resolve_watts(pricing, elec, m)
    )
    cost = kwh * elec.price_per_kwh
    return (
        {
            "active_hours": hours,
            "kwh": kwh,
            "cost": cost,
            "currency": elec.currency,
            "cost_usd": cost * elec.usd_per_unit if elec.usd_per_unit is not None else None,
        },
        warnings,
    )


def energy_lines(summary: dict, gross_saved_usd: float | None) -> list[str]:
    lines = [
        f"[bold]Energy:[/] {summary['active_hours']:.1f} h active · {summary['kwh']:.2f} kWh"
    ]
    cost_line = f"[bold]Electricity:[/] {summary['currency']} {summary['cost']:.2f}"
    if summary["cost_usd"] is not None:
        cost_line += f" (≈ ${summary['cost_usd']:.2f})"
        lines.append(cost_line)
        if gross_saved_usd is not None:
            net = gross_saved_usd - summary["cost_usd"]
            lines.append(
                f"[bold]Net saved:[/] ${gross_saved_usd:.2f} gross − "
                f"${summary['cost_usd']:.2f} electricity = ${net:.2f}"
            )
    else:
        cost_line += " (set electricity.usd_per_unit to see net savings)"
        lines.append(cost_line)
    return lines
