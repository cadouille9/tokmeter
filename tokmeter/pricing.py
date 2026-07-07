from __future__ import annotations

import math
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


def _parse_price(value: object) -> float | None:
    # A valid price is a finite, non-negative number. Anything else (missing,
    # non-numeric, negative, inf/nan) is rejected -> None, so callers never
    # silently substitute 0.0 for a typo and understate cost.
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0:
        return None
    return f


def _parse_references(pricing: dict) -> tuple[list[tuple[str, Rate]], list[str]]:
    refs = pricing.get("references") or {}
    valid: list[tuple[str, Rate]] = []
    warnings: list[str] = []
    for name, spec in refs.items():
        spec = spec or {}
        in_rate = _parse_price(spec.get("input_per_1m"))
        out_rate = _parse_price(spec.get("output_per_1m"))
        bad = [
            field
            for field, parsed in (("input_per_1m", in_rate), ("output_per_1m", out_rate))
            if parsed is None
        ]
        if bad:
            warnings.append(
                f"reference {name!r} skipped: missing or invalid "
                + " and ".join(bad)
                + " (each price must be a number >= 0)"
            )
            continue
        valid.append((name, Rate(input_per_1m=in_rate, output_per_1m=out_rate, mapped=True)))
    return valid, warnings


def reference_rates(pricing: dict) -> list[tuple[str, Rate]]:
    return _parse_references(pricing)[0]


def reference_warnings(pricing: dict) -> list[str]:
    return _parse_references(pricing)[1]


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
