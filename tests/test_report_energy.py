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


def test_energy_lines_escapes_markup_in_currency():
    from rich.console import Console
    import io

    pricing = {k: dict(v) if isinstance(v, dict) else v for k, v in PRICING.items()}
    pricing["electricity"] = dict(PRICING["electricity"])
    pricing["electricity"]["currency"] = "[/]"
    summary, warnings = report.energy_summary(pricing, ROWS)
    assert warnings == []
    lines = report.energy_lines(summary, gross_saved_usd=10.0)
    text = "\n".join(lines)
    assert "[/]" in text

    console = Console(file=io.StringIO(), force_terminal=False)
    for line in lines:
        console.print(line)  # must not raise MarkupError
