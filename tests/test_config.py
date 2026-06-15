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
