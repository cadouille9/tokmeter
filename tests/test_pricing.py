from tokmeter import pricing

PRICING = {
    "default": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "models": {
        "Qwen3.6-27B-UD-Q6_K_XL": {"input_per_1m": 0.30, "output_per_1m": 1.20},
    },
}


def test_resolve_mapped_model():
    rate = pricing.resolve_rate(PRICING, "Qwen3.6-27B-UD-Q6_K_XL")
    assert rate.input_per_1m == 0.30
    assert rate.output_per_1m == 1.20
    assert rate.mapped is True


def test_resolve_matches_despite_gguf_suffix():
    # llama.cpp reports e.g. "Qwen3.6-27B-UD-Q6_K_XL.gguf"; the config key omits .gguf.
    rate = pricing.resolve_rate(PRICING, "Qwen3.6-27B-UD-Q6_K_XL.gguf")
    assert rate.input_per_1m == 0.30
    assert rate.output_per_1m == 1.20
    assert rate.mapped is True


def test_resolve_matches_case_insensitively():
    rate = pricing.resolve_rate(PRICING, "qwen3.6-27b-ud-q6_k_xl")
    assert rate.input_per_1m == 0.30
    assert rate.mapped is True


def test_resolve_unmapped_falls_back_to_default():
    rate = pricing.resolve_rate(PRICING, "some-other-model")
    assert rate.input_per_1m == 0.15
    assert rate.mapped is False


def test_compute_savings():
    rate = pricing.resolve_rate(PRICING, "Qwen3.6-27B-UD-Q6_K_XL")
    # 1,000,000 prompt + 500,000 completion tokens
    saved = pricing.compute_savings(1_000_000, 500_000, rate)
    assert round(saved, 4) == round(0.30 + 0.60, 4)  # 0.90


def test_load_pricing_missing_file_returns_builtin_default(tmp_path):
    p = tmp_path / "nope.yaml"
    data = pricing.load_pricing(p)
    assert "default" in data
    assert data["default"]["input_per_1m"] >= 0


def test_reference_rates_parses_entries_in_order():
    p = {
        "references": {
            "claude-opus-4-8": {"input_per_1m": 5.0, "output_per_1m": 25.0},
            "claude-sonnet-4-6": {"input_per_1m": 3.0, "output_per_1m": 15.0},
        }
    }
    refs = pricing.reference_rates(p)
    assert [name for name, _ in refs] == ["claude-opus-4-8", "claude-sonnet-4-6"]
    rate = dict(refs)["claude-opus-4-8"]
    assert rate.input_per_1m == 5.0
    assert rate.output_per_1m == 25.0


def test_reference_rates_empty_when_section_absent():
    assert pricing.reference_rates({"default": {"input_per_1m": 0.1, "output_per_1m": 0.2}}) == []


def test_reference_rates_skips_non_numeric_price_with_warning():
    # A typo'd price must NOT silently become $0.00 — the reference is dropped
    # and a warning is surfaced (cost estimates are user-edited financial inputs).
    p = {"references": {"x": {"input_per_1m": "oops", "output_per_1m": 5.0}}}
    assert pricing.reference_rates(p) == []
    warns = pricing.reference_warnings(p)
    assert len(warns) == 1
    assert "x" in warns[0]
    assert "input_per_1m" in warns[0]


def test_reference_rates_skips_missing_output_price_with_warning():
    p = {"references": {"x": {"input_per_1m": 5.0}}}
    assert pricing.reference_rates(p) == []
    assert "output_per_1m" in pricing.reference_warnings(p)[0]


def test_reference_rates_skips_none_spec_with_warning():
    p = {"references": {"x": None}}
    assert pricing.reference_rates(p) == []
    assert pricing.reference_warnings(p)  # at least one warning emitted


def test_reference_rates_skips_negative_price():
    p = {"references": {"x": {"input_per_1m": -1.0, "output_per_1m": 5.0}}}
    assert pricing.reference_rates(p) == []
    assert pricing.reference_warnings(p)


def test_reference_rates_skips_non_finite_price():
    p = {"references": {"x": {"input_per_1m": float("inf"), "output_per_1m": 5.0}}}
    assert pricing.reference_rates(p) == []
    assert pricing.reference_warnings(p)


def test_reference_rates_allows_zero_price():
    # Zero is a legitimate price (a free model); only missing/invalid/negative drop.
    p = {"references": {"x": {"input_per_1m": 0.0, "output_per_1m": 0.0}}}
    refs = pricing.reference_rates(p)
    assert dict(refs)["x"].input_per_1m == 0.0
    assert pricing.reference_warnings(p) == []


def test_reference_rates_keeps_valid_skips_invalid_in_mix():
    p = {
        "references": {
            "good": {"input_per_1m": 5.0, "output_per_1m": 25.0},
            "bad": {"input_per_1m": 5.0, "output_per_1m": "x"},
        }
    }
    assert [name for name, _ in pricing.reference_rates(p)] == ["good"]
    assert len(pricing.reference_warnings(p)) == 1
    assert "bad" in pricing.reference_warnings(p)[0]
