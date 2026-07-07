# Electricity Cost Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Charge logged usage for electricity (per-model watts × merged active time × CHF/kWh) and report net savings.

**Architecture:** A new pure module `tokmeter/energy.py` reconstructs request intervals from the existing `requests` table (`ts` is the request **end**, ISO-8601 with UTC tz; interval = `[ts − duration_ms, ts]`), merges overlaps **per model**, and converts hours × watts to kWh. `pricing.py` parses a new optional `electricity:` block plus per-model `watts`. `report.py`/`cli.py` append energy/cost/net lines to `report` and a footer to `compare`. Nothing in the serve path changes.

**Tech Stack:** Python 3.11+, stdlib (`datetime`, `dataclasses`), existing deps (PyYAML, rich, pytest). Spec: `docs/superpowers/specs/2026-07-07-electricity-cost-accounting-design.md`.

## Global Constraints

- Feature fully off (byte-identical report output) when `electricity:` absent from pricing.yaml.
- No DB schema changes; no changes to `proxy.py`/`writer.py` (live service must not need a restart).
- Config validation mirrors `_parse_price`: invalid values warn and disable, never crash.
- Model-name matching for `watts` reuses `_normalize` (case-insensitive, trailing `.gguf` ignored).
- All commits in `~/dev/tokmeter`; run tests with `.venv/bin/python -m pytest`.

---

### Task 1: `energy.py` — interval merge and kWh math

**Files:**
- Create: `tokmeter/energy.py`
- Test: `tests/test_energy.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `merged_seconds(intervals: list[tuple[float, float]]) -> float` — sum of the union of `(start_epoch, end_epoch)` intervals.
  - `intervals_from_rows(rows: Iterable[tuple[str | None, str, int | None]]) -> dict[str | None, list[tuple[float, float]]]` — rows are `(model, ts_iso_end, duration_ms)`; zero/None durations dropped.
  - `energy_kwh(rows, watts_for: Callable[[str | None], float]) -> tuple[float, float]` — returns `(active_hours_total, kwh_total)`, merging per model, summing across models.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_energy.py
from tokmeter import energy


def test_merged_seconds_disjoint():
    assert energy.merged_seconds([(0, 10), (20, 30)]) == 20


def test_merged_seconds_overlapping_and_nested():
    # (0,10) and (5,15) overlap -> 15; (16,20) nested in (16,20) stays 4
    assert energy.merged_seconds([(0, 10), (5, 15), (16, 20), (17, 19)]) == 19


def test_merged_seconds_adjacent_touching():
    # touching endpoints merge without double counting
    assert energy.merged_seconds([(0, 10), (10, 20)]) == 20


def test_merged_seconds_empty():
    assert energy.merged_seconds([]) == 0


def test_intervals_from_rows_ts_is_end():
    # ts is the request END; a 2000ms request ending at :10 spans :08 -> :10
    rows = [("m", "2026-07-07T12:00:10+00:00", 2000)]
    ivals = energy.intervals_from_rows(rows)
    (start, end), = ivals["m"]
    assert end - start == 2.0


def test_intervals_from_rows_drops_zero_and_none_duration():
    rows = [
        ("m", "2026-07-07T12:00:10+00:00", 0),
        ("m", "2026-07-07T12:00:11+00:00", None),
    ]
    assert energy.intervals_from_rows(rows) == {}


def test_energy_kwh_merges_within_model_adds_across_models():
    # model a: two fully-overlapping 1h requests at 100W -> 1h, 0.1 kWh
    # model b: one 1h request at 300W, concurrent with a -> adds 0.3 kWh
    rows = [
        ("a", "2026-07-07T13:00:00+00:00", 3_600_000),
        ("a", "2026-07-07T13:00:00+00:00", 3_600_000),
        ("b", "2026-07-07T13:00:00+00:00", 3_600_000),
    ]
    hours, kwh = energy.energy_kwh(rows, watts_for=lambda m: {"a": 100, "b": 300}[m])
    assert hours == 2.0  # per-model active hours summed
    assert abs(kwh - 0.4) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest tests/test_energy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tokmeter.energy'` (or ImportError).

- [ ] **Step 3: Write the implementation**

```python
# tokmeter/energy.py
"""Estimate energy from logged requests: watts x merged active time.

`ts` in the requests table is the request END (see proxy.py: the record is
built after the response completes), so a request spans
[ts - duration_ms, ts]. Overlapping requests to the SAME model (parallel
slots on one server) are merged so concurrent decoding isn't double-billed;
DIFFERENT models are assumed to run on different hardware, so their energy adds.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable


def merged_seconds(intervals: list[tuple[float, float]]) -> float:
    total = 0.0
    end_prev = float("-inf")
    for start, end in sorted(intervals):
        if start > end_prev:
            total += end - start
            end_prev = end
        elif end > end_prev:
            total += end - end_prev
            end_prev = end
    return total


def intervals_from_rows(
    rows: Iterable[tuple[str | None, str, int | None]],
) -> dict[str | None, list[tuple[float, float]]]:
    out: dict[str | None, list[tuple[float, float]]] = {}
    for model, ts_iso, duration_ms in rows:
        if not duration_ms or duration_ms <= 0:
            continue
        try:
            end = datetime.fromisoformat(ts_iso).timestamp()
        except (TypeError, ValueError):
            continue
        out.setdefault(model, []).append((end - duration_ms / 1000.0, end))
    return out


def energy_kwh(
    rows: Iterable[tuple[str | None, str, int | None]],
    watts_for: Callable[[str | None], float],
) -> tuple[float, float]:
    hours_total = 0.0
    kwh_total = 0.0
    for model, ivals in intervals_from_rows(rows).items():
        hours = merged_seconds(ivals) / 3600.0
        hours_total += hours
        kwh_total += hours * watts_for(model) / 1000.0
    return hours_total, kwh_total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest tests/test_energy.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/tokmeter && git add tokmeter/energy.py tests/test_energy.py && git commit -m "feat: energy module — per-model interval merge and kWh math"
```

---

### Task 2: `pricing.py` — electricity config parsing and watts resolution

**Files:**
- Modify: `tokmeter/pricing.py` (append after `reference_warnings`, ~line 113)
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `_parse_price(value) -> float | None`, `_normalize(name) -> str`.
- Produces:
  - `@dataclass(frozen=True) ElectricityConfig(price_per_kwh: float, currency: str, usd_per_unit: float | None, default_watts: float)`
  - `electricity_config(pricing: dict) -> tuple[ElectricityConfig | None, list[str]]` — `(None, warnings)` when absent or invalid.
  - `resolve_watts(pricing: dict, elec: ElectricityConfig, model: str | None) -> float` — per-model `watts` via the same forgiving matching as `resolve_rate`, else `elec.default_watts`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_config.py
from tokmeter import pricing as pricing_mod


def _elec_yaml(**over):
    base = {
        "price_per_kwh": 0.277,
        "currency": "CHF",
        "usd_per_unit": 1.25,
        "default_watts": 250,
    }
    base.update(over)
    return {"electricity": base, "models": {"Gemma-4-31B": {"watts": 210}}}


def test_electricity_absent_returns_none_no_warnings():
    cfg, warnings = pricing_mod.electricity_config({})
    assert cfg is None
    assert warnings == []


def test_electricity_parses_full_block():
    cfg, warnings = pricing_mod.electricity_config(_elec_yaml())
    assert warnings == []
    assert cfg.price_per_kwh == 0.277
    assert cfg.currency == "CHF"
    assert cfg.usd_per_unit == 1.25
    assert cfg.default_watts == 250


def test_electricity_missing_usd_rate_is_allowed():
    data = _elec_yaml()
    del data["electricity"]["usd_per_unit"]
    cfg, warnings = pricing_mod.electricity_config(data)
    assert cfg is not None and cfg.usd_per_unit is None and warnings == []


def test_electricity_invalid_price_disables_with_warning():
    cfg, warnings = pricing_mod.electricity_config(_elec_yaml(price_per_kwh="oops"))
    assert cfg is None
    assert len(warnings) == 1


def test_resolve_watts_per_model_forgiving_match_and_default():
    data = _elec_yaml()
    cfg, _ = pricing_mod.electricity_config(data)
    assert pricing_mod.resolve_watts(data, cfg, "gemma-4-31b") == 210
    assert pricing_mod.resolve_watts(data, cfg, "unknown-model") == 250
    assert pricing_mod.resolve_watts(data, cfg, None) == 250
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest tests/test_config.py -v -k electricity or resolve_watts`
Note: the exact invocation is `.venv/bin/python -m pytest tests/test_config.py -v -k "electricity or resolve_watts"`.
Expected: FAIL — `AttributeError: module 'tokmeter.pricing' has no attribute 'electricity_config'`.

- [ ] **Step 3: Write the implementation** (append to `tokmeter/pricing.py`)

```python
@dataclass(frozen=True)
class ElectricityConfig:
    price_per_kwh: float
    currency: str
    usd_per_unit: float | None
    default_watts: float


def electricity_config(pricing: dict) -> tuple[ElectricityConfig | None, list[str]]:
    block = pricing.get("electricity")
    if not block:
        return None, []
    price = _parse_price(block.get("price_per_kwh"))
    watts = _parse_price(block.get("default_watts", 250))
    usd = block.get("usd_per_unit")
    usd_parsed = _parse_price(usd) if usd is not None else None
    problems = []
    if price is None:
        problems.append("price_per_kwh")
    if watts is None:
        problems.append("default_watts")
    if usd is not None and usd_parsed is None:
        problems.append("usd_per_unit")
    if problems:
        return None, [
            "electricity block ignored: invalid " + " and ".join(problems)
            + " (each must be a number >= 0)"
        ]
    return (
        ElectricityConfig(
            price_per_kwh=price,
            currency=str(block.get("currency", "CHF")),
            usd_per_unit=usd_parsed,
            default_watts=watts,
        ),
        [],
    )


def resolve_watts(pricing: dict, elec: ElectricityConfig, model: str | None) -> float:
    models = pricing.get("models", {})
    entry = None
    if model:
        if model in models:
            entry = models[model]
        else:
            target = _normalize(model)
            for key, val in models.items():
                if _normalize(key) == target:
                    entry = val
                    break
    if entry:
        w = _parse_price(entry.get("watts"))
        if w is not None:
            return w
    return elec.default_watts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest tests/test_config.py -v`
Expected: all pass (existing + 5 new).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/tokmeter && git add tokmeter/pricing.py tests/test_config.py && git commit -m "feat: parse electricity config block and per-model watts"
```

---

### Task 3: `db.py` — rows for energy computation

**Files:**
- Modify: `tokmeter/db.py` (append after `distinct_models`, line 117)
- Test: `tests/test_energy.py` (append)

**Interfaces:**
- Consumes: existing `_where(since, until, model)`, `connect`, `init_db`, `insert_record`, `UsageRecord`.
- Produces: `rows_for_energy(conn, since=None, until=None, model=None) -> list[tuple[str | None, str, int | None]]` — `(model, ts, duration_ms)` tuples, `duration_ms > 0` only, same date filters as the aggregates.

- [ ] **Step 1: Write the failing test** (append to `tests/test_energy.py`)

```python
from tokmeter import db


def _record(ts, model="m", duration_ms=1000):
    return db.UsageRecord(
        ts=ts, model=model, endpoint="chat/completions",
        prompt_tokens=1, completion_tokens=1, total_tokens=2,
        duration_ms=duration_ms, tokens_per_sec=1.0, stream=0, status=200,
        upstream="http://x",
    )


def test_rows_for_energy_filters_and_shape(tmp_path):
    conn = db.connect(tmp_path / "usage.db")
    db.init_db(conn)
    db.insert_record(conn, _record("2026-07-06T10:00:01+00:00"))
    db.insert_record(conn, _record("2026-07-07T10:00:02+00:00"))
    db.insert_record(conn, _record("2026-07-07T10:00:03+00:00", duration_ms=0))
    db.insert_record(conn, _record("2026-07-07T10:00:04+00:00", model="other"))

    rows = db.rows_for_energy(conn, since="2026-07-07")
    assert ("m", "2026-07-07T10:00:02+00:00", 1000) in rows
    assert ("other", "2026-07-07T10:00:04+00:00", 1000) in rows
    assert len(rows) == 2  # zero-duration and pre-since rows excluded

    rows_m = db.rows_for_energy(conn, model="m")
    assert {r[0] for r in rows_m} == {"m"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest tests/test_energy.py::test_rows_for_energy_filters_and_shape -v`
Expected: FAIL — `AttributeError: module 'tokmeter.db' has no attribute 'rows_for_energy'`.

- [ ] **Step 3: Write the implementation** (append to `tokmeter/db.py`)

```python
def rows_for_energy(conn, since=None, until=None, model=None) -> list[tuple]:
    where, params = _where(since, until, model)
    prefix = " AND " if where else " WHERE "
    rows = conn.execute(
        f"SELECT model, ts, duration_ms FROM requests{where}{prefix}"
        "duration_ms IS NOT NULL AND duration_ms > 0",
        params,
    ).fetchall()
    return [(r["model"], r["ts"], r["duration_ms"]) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest tests/test_energy.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd ~/dev/tokmeter && git add tokmeter/db.py tests/test_energy.py && git commit -m "feat: db query for energy interval rows"
```

---

### Task 4: report lines + CLI wiring (`report` and `compare`)

**Files:**
- Modify: `tokmeter/report.py` (append after `build_comparison`)
- Modify: `tokmeter/cli.py` (`_cmd_report` line 53-59, `_cmd_compare` line 99-107)
- Test: `tests/test_report_energy.py` (create)

**Interfaces:**
- Consumes: `energy.energy_kwh`, `pricing_mod.electricity_config`, `pricing_mod.resolve_watts`, `db.rows_for_energy`.
- Produces:
  - `report.energy_summary(pricing: dict, rows) -> tuple[dict | None, list[str]]` — dict has keys `active_hours: float, kwh: float, cost: float, currency: str, cost_usd: float | None`; `(None, warnings)` when unconfigured/invalid.
  - `report.energy_lines(summary: dict, gross_saved_usd: float | None) -> list[str]` — pre-formatted plain-text lines (no Rich markup in values; the leading label uses `[bold]`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report_energy.py
from tokmeter import report


PRICING = {
    "electricity": {
        "price_per_kwh": 0.5,
        "currency": "CHF",
        "usd_per_unit": 2.0,
        "default_watts": 1000,
    },
    "models": {},
}
# one 1-hour request at 1000 W -> 1 kWh -> CHF 0.50 -> $1.00
ROWS = [("m", "2026-07-07T13:00:00+00:00", 3_600_000)]


def test_energy_summary_computes_cost_and_usd():
    summary, warnings = report.energy_summary(PRICING, ROWS)
    assert warnings == []
    assert abs(summary["kwh"] - 1.0) < 1e-9
    assert abs(summary["cost"] - 0.5) < 1e-9
    assert summary["currency"] == "CHF"
    assert abs(summary["cost_usd"] - 1.0) < 1e-9


def test_energy_summary_unconfigured_is_none():
    summary, warnings = report.energy_summary({"models": {}}, ROWS)
    assert summary is None and warnings == []


def test_energy_summary_no_usd_rate():
    pricing = {k: dict(v) if isinstance(v, dict) else v for k, v in PRICING.items()}
    pricing["electricity"] = dict(PRICING["electricity"])
    del pricing["electricity"]["usd_per_unit"]
    summary, _ = report.energy_summary(pricing, ROWS)
    assert summary["cost_usd"] is None


def test_energy_lines_with_net():
    summary, _ = report.energy_summary(PRICING, ROWS)
    lines = report.energy_lines(summary, gross_saved_usd=10.0)
    text = "\n".join(lines)
    assert "1.0 h" in text and "1.00 kWh" in text
    assert "CHF 0.50" in text and "$1.00" in text
    assert "$10.00" in text and "$9.00" in text  # gross - electricity = net


def test_energy_lines_without_usd_rate_omits_net():
    summary, _ = report.energy_summary(PRICING, ROWS)
    summary = {**summary, "cost_usd": None}
    lines = report.energy_lines(summary, gross_saved_usd=10.0)
    text = "\n".join(lines)
    assert "usd_per_unit" in text  # hint how to enable netting
    assert "$9.00" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest tests/test_report_energy.py -v`
Expected: FAIL — `AttributeError: module 'tokmeter.report' has no attribute 'energy_summary'`.

- [ ] **Step 3: Write the implementation**

Append to `tokmeter/report.py`:

```python
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
```

In `tokmeter/cli.py`, replace the end of `_cmd_report` (after the `console.print(report.render_table(...))` / totals block, lines 53-59):

```python
    console.print(report.render_table(rows, key=key, title=title))
    t = report.totals(rows)
    console.print(
        f"[bold]Totals:[/] {t['requests']} requests, "
        f"{t['total_tokens']:,} tokens, ~${t['saved_usd']:.2f} saved"
    )
    energy_rows = db.rows_for_energy(conn, since=args.since, until=args.until, model=args.model)
    summary, warnings = report.energy_summary(pricing_data, energy_rows)
    for warning in warnings:
        console.print(f"[yellow]warning:[/] {warning}")
    if summary is not None:
        for line in report.energy_lines(summary, gross_saved_usd=t["saved_usd"]):
            console.print(line)
    return 0
```

In `_cmd_compare`, after `console.print(report.render_comparison_table(rows))` (line 106):

```python
    energy_rows = db.rows_for_energy(conn, since=args.since, until=args.until, model=args.model)
    summary, warnings = report.energy_summary(pricing_data, energy_rows)
    for warning in warnings:
        console.print(f"[yellow]warning:[/] {warning}")
    if summary is not None:
        for line in report.energy_lines(summary, gross_saved_usd=None):
            console.print(line)
        if summary["cost_usd"] is not None:
            console.print(
                "[dim]Subtract the electricity ≈$ figure from any reference above for net savings.[/]"
            )
    return 0
```

- [ ] **Step 4: Run the full suite**

Run: `cd ~/dev/tokmeter && .venv/bin/python -m pytest -v`
Expected: all pass (existing suite + new tests).

- [ ] **Step 5: Smoke-test against the live DB (read-only)**

Run: `cd ~/dev/tokmeter && .venv/bin/tokmeter report --since 2026-07-06 | tail -5`
Expected: table renders; no energy lines yet (live pricing.yaml has no electricity block — confirms off-by-default).

- [ ] **Step 6: Commit**

```bash
cd ~/dev/tokmeter && git add tokmeter/report.py tokmeter/cli.py tests/test_report_energy.py && git commit -m "feat: energy + net-savings lines in report and compare"
```

---

### Task 5: docs + live config rollout

**Files:**
- Modify: `README.md` (add "Electricity cost" section after the pricing-config section)
- Modify: `config/pricing.yaml` (example config in repo)
- Modify: `~/.config/tokmeter/pricing.yaml` (live config — outside the repo)

**Interfaces:** none (documentation + deployment).

- [ ] **Step 1: Add README section** (locate the pricing section with `grep -n "pricing" README.md` and insert after it)

```markdown
## Electricity cost (net savings)

Add an optional `electricity:` block to `pricing.yaml` and reports subtract power
cost from the cloud-equivalent savings:

```yaml
electricity:
  price_per_kwh: 0.277   # your tariff (ElCom 2026 Swiss household average shown)
  currency: CHF
  usd_per_unit: 1.25     # FX for the net-savings line; omit to skip netting
  default_watts: 250

models:
  gemma-4-31b: { watts: 210 }   # per-model override: box draw for that model's GPU config
```

How it's estimated: requests are turned into time intervals (`ts` is the request end,
spanning `duration_ms` backwards), overlapping intervals of the same model are merged
(parallel slots aren't double-billed), and each model's active hours × watts gives kWh.
Different models add up — if two models run concurrently they're assumed to be on
different GPUs, both drawing power. Idle draw (model loaded, nothing generating) is not
counted, and `watts` values are your estimates — calibrate with a wall-plug meter for
precision. Reports re-price history retroactively whenever you edit the config.
```

- [ ] **Step 2: Update `config/pricing.yaml`** (example file) — append the same `electricity:` block (commented out) and a commented `watts:` line inside the models example.

- [ ] **Step 3: Update the live config** `~/.config/tokmeter/pricing.yaml` — add the electricity block (price 0.277 CHF, usd_per_unit 1.25, default_watts 250) and per-model watts entries:

```yaml
electricity:
  price_per_kwh: 0.277
  currency: CHF
  usd_per_unit: 1.25
  default_watts: 250

# merged into existing models: section
#   gemma-4-31b:      { watts: 210 }
#   gemma-4-26b-a4b:  { watts: 240 }
#   gemma-4-12b:      { watts: 260 }
#   qwen3.6-35b-a3b:  { watts: 210 }
#   qwen3.6-27b:      { watts: 210 }   # update to ~400 if run dual-GPU
```

- [ ] **Step 4: Verify against live data**

Run: `.venv/bin/tokmeter report --since 2026-07-06` and `.venv/bin/tokmeter compare --since 2026-07-06`
Expected: Energy/Electricity/Net-saved lines appear with plausible values (order: tens of active hours, single-digit kWh, cost of a few CHF).

- [ ] **Step 5: Commit**

```bash
cd ~/dev/tokmeter && git add README.md config/pricing.yaml && git commit -m "docs: electricity cost accounting usage + example config"
```
