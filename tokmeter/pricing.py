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


def _normalize(name: str) -> str:
    # Forgiving match: case-insensitive, ignoring a trailing .gguf, so a config
    # key like "Qwen3.6-27B-UD-Q6_K_XL" matches the server-reported
    # "Qwen3.6-27B-UD-Q6_K_XL.gguf" (and quant-case variations).
    n = name.strip().lower()
    if n.endswith(".gguf"):
        n = n[: -len(".gguf")]
    return n


def resolve_rate(pricing: dict, model: str | None) -> Rate:
    models = pricing.get("models", {})
    default = pricing.get("default", BUILTIN_DEFAULT["default"])
    m = None
    if model:
        if model in models:  # exact match wins
            m = models[model]
        else:
            target = _normalize(model)
            for key, val in models.items():
                if _normalize(key) == target:
                    m = val
                    break
    if m is not None:
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


def _to_float(value: object, default: float = 0.0) -> float:
    # References come from user-supplied YAML; coerce leniently so a typo'd or
    # missing price falls back to 0.0 instead of crashing `tokmeter compare`.
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def reference_rates(pricing: dict) -> list[tuple[str, Rate]]:
    refs = pricing.get("references") or {}
    out: list[tuple[str, Rate]] = []
    for name, spec in refs.items():
        spec = spec or {}
        out.append(
            (
                name,
                Rate(
                    input_per_1m=_to_float(spec.get("input_per_1m")),
                    output_per_1m=_to_float(spec.get("output_per_1m")),
                    mapped=True,
                ),
            )
        )
    return out
