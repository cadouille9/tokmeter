from pathlib import Path

from tokmeter import config


def test_defaults(monkeypatch):
    monkeypatch.delenv("TOKMETER_PORT", raising=False)
    monkeypatch.delenv("TOKMETER_UPSTREAM", raising=False)
    s = config.load_settings()
    assert s.listen_host == "127.0.0.1"
    assert s.listen_port == 8079
    assert s.upstream == "http://127.0.0.1:8080"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("TOKMETER_PORT", "9000")
    monkeypatch.setenv("TOKMETER_UPSTREAM", "http://127.0.0.1:8081")
    s = config.load_settings()
    assert s.listen_port == 9000
    assert s.upstream == "http://127.0.0.1:8081"


def test_paths_under_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert config.db_path() == tmp_path / "data" / "tokmeter" / "usage.db"
    assert config.pricing_path() == tmp_path / "cfg" / "tokmeter" / "pricing.yaml"


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
