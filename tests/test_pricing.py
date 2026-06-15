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


def test_reference_rates_tolerates_non_numeric_price():
    refs = pricing.reference_rates({"references": {"x": {"input_per_1m": "oops"}}})
    assert dict(refs)["x"].input_per_1m == 0.0
    assert dict(refs)["x"].output_per_1m == 0.0
