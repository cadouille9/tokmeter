# Electricity Cost Accounting — Design

**Date:** 2026-07-07
**Status:** Approved by Chris (chat, 2026-07-07)

## Purpose

tokmeter reports how much money local inference *saves* versus cloud APIs, but running
the GPUs isn't free. This feature charges logged usage for electricity (average Swiss
household price) and reports **net** savings: gross cloud-equivalent savings minus
power cost.

## Requirements

- Energy is estimated **report-side only** from data already in `usage.db`
  (`ts`, `duration_ms`, `model`). No schema change, no proxy/serve-path change, no
  service restart. Re-prices all history retroactively.
- **Per-model wattage** with a global default. Each model runs on a known GPU
  configuration (A5000-solo capped/uncapped, 4080-solo, dual-GPU), so the model name
  identifies the power regime.
- Feature is **off** (reports unchanged) when the `electricity:` config block is absent.
- Electricity price is expressed in the user's currency (CHF); net savings convert via
  a **configured static FX rate**. No network calls.

## Configuration (`pricing.yaml`)

```yaml
electricity:
  price_per_kwh: 0.277   # 2026 ElCom Swiss household average (27.7 Rp./kWh)
  currency: CHF
  usd_per_unit: 1.25     # 1 CHF ≈ 1.25 USD; used only for the net-savings line
  default_watts: 250     # fallback for models without a watts entry

models:
  # watts = estimated box draw attributable to inference for THAT model's usual
  # deployment (GPU draw + ~30–50 W CPU/PSU share). Estimates — calibrate with a
  # wall-plug meter if precision matters.
  gemma-4-31b:       { watts: 210 }   # A5000 solo @ 200 W cap
  gemma-4-26b-a4b:   { watts: 240 }   # A5000 solo, uncapped era (230 W limit)
  gemma-4-12b:       { watts: 260 }   # RTX 4080 solo
  qwen3.6-35b-a3b:   { watts: 210 }   # A5000 solo @ 200 W cap
  qwen3.6-27b:       { watts: 210 }   # A5000 solo @ 200 W cap; if run dual-GPU,
                                      # update to ~400 (200 A5000 + ~180 4080 + share)
```

`watts` merges into the existing per-model entries (which today hold `input_per_1m`,
`output_per_1m`, `comparable`) — same forgiving name matching (case-insensitive,
trailing `.gguf` ignored).

## Energy model

1. For each model, reconstruct request intervals from the log. `ts` is checked against
   `writer.py` at implementation time: if `ts` is request end, interval =
   `[ts − duration_ms, ts]`; if start, `[ts, ts + duration_ms]`.
2. **Merge overlapping intervals per model** (sort by start; sweep). Concurrent
   requests to the same server (Deepthink runs up to 6-wide) must not double-bill.
3. Energy = Σ over models of `merged_hours(model) × watts(model) / 1000` (kWh).
   Intervals of *different* models are **not** merged across models — when two models
   run concurrently on different GPUs, both really draw power, so their energy adds.
4. Cost = `kWh × price_per_kwh` (in `currency`); USD equivalent via `usd_per_unit`.

Known approximations (documented in README, accepted):

- Idle/loaded-but-not-generating draw is excluded (active request time only).
- A model's `watts` is a single value even if the power regime changed over its
  history (e.g. a power-cap change mid-experiment). The user sets a representative value.
- Zero/NULL-duration rows (e.g. the known empty double-log rows) contribute nothing.

## Surfaces

**`tokmeter report`** — three lines appended after the existing table (only when
configured):

```
Energy:      41.3 h active · 10.3 kWh
Electricity: CHF 2.86 (≈ $3.57 @ 1.25)
Net saved:   $208.11 gross − $3.57 electricity = $204.54
```

**`tokmeter compare`** — the same electricity total is subtracted from each reference
model's would-have-cost delta ("net of electricity" column or footer line).

Totals only in v1 — no per-day or per-model energy breakdown (YAGNI; the per-model
plumbing exists internally if a breakdown is wanted later).

## Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `tokmeter/energy.py` (new, ~50 lines) | Pure functions: interval reconstruction, per-model overlap merge, kWh + cost math | stdlib only |
| `tokmeter/pricing.py` | Parse/validate the `electricity:` block + per-model `watts`; expose to report/compare | existing config loading |
| `tokmeter/db.py` | One query returning `(model, ts, duration_ms)` rows for a time range | existing connection helpers |
| `tokmeter/report.py` | Render the three report lines; compare-mode netting | energy, pricing, db |

## Error handling

- Missing `electricity:` block → feature disabled, zero new output.
- Partial block (e.g. no `usd_per_unit`) → energy + CHF lines print; net-savings line
  omitted with a one-line hint.
- Non-numeric/negative config values → same treatment as existing pricing validation
  (skip with warning, never crash a report).

## Testing

- `tests/test_energy.py`: interval merge (disjoint, overlapping, nested, adjacent,
  cross-model non-merge), end-vs-start ts convention, kWh/cost arithmetic, zero-duration rows.
- `tests/test_config.py` (extend): electricity block parsing, defaults, absence.
- `tests/test_report.py` (extend or add): report renders the three lines when
  configured, renders nothing new when not.

## Out of scope (v1)

- Live power measurement (nvidia-smi sampling in the proxy).
- Idle-draw / duty-cycle modeling.
- Per-day or per-model energy breakdown in report output.
- Automatic FX or electricity-price updates.
