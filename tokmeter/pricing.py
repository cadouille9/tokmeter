from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

BUILTIN_DEFAULT = {"default": {"input_per_1m": 0.15, "output_per_1m": 0.60}, "models": {}}


@dataclass(frozen=True)
class Rate:
    input_per_1m: float
    output_per_1m: float
    mapped: bool


def load_pricing(path: Path) -> dict:
    if not Path(path).exists():
        return dict(BUILTIN_DEFAULT)
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("default", BUILTIN_DEFAULT["default"])
    data.setdefault("models", {})
    return data


def resolve_rate(pricing: dict, model: str | None) -> Rate:
    models = pricing.get("models", {})
    default = pricing.get("default", BUILTIN_DEFAULT["default"])
    if model and model in models:
        m = models[model]
        return Rate(
            input_per_1m=float(m.get("input_per_1m", default["input_per_1m"])),
            output_per_1m=float(m.get("output_per_1m", default["output_per_1m"])),
            mapped=True,
        )
    return Rate(
        input_per_1m=float(default["input_per_1m"]),
        output_per_1m=float(default["output_per_1m"]),
        mapped=False,
    )


def compute_savings(prompt_tokens: int, completion_tokens: int, rate: Rate) -> float:
    p = (prompt_tokens or 0) / 1_000_000 * rate.input_per_1m
    c = (completion_tokens or 0) / 1_000_000 * rate.output_per_1m
    return p + c
