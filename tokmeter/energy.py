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
