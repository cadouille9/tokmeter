import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    listen_host: str
    listen_port: int
    upstream: str


def load_settings() -> Settings:
    return Settings(
        listen_host=os.environ.get("TOKMETER_HOST", "127.0.0.1"),
        listen_port=int(os.environ.get("TOKMETER_PORT", "8079")),
        upstream=os.environ.get("TOKMETER_UPSTREAM", "http://127.0.0.1:8080"),
    )


def _xdg(env_var: str, default_subpath: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base)
    return Path.home() / default_subpath


def db_path() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "tokmeter" / "usage.db"


def pricing_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "tokmeter" / "pricing.yaml"
