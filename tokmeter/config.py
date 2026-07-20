import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Listener:
    port: int
    upstream: str
    host: str | None = None  # None -> bind Settings.listen_host


@dataclass(frozen=True)
class Settings:
    listen_host: str
    listeners: tuple[Listener, ...]


def _parse_listeners(raw: str) -> tuple[Listener, ...]:
    listeners: list[Listener] = []
    seen: set[tuple[str | None, int]] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        left, sep, upstream = item.partition("=")
        if not sep or not upstream.strip():
            raise ValueError(
                f"TOKMETER_LISTENERS entry {item!r} must look like [HOST:]PORT=UPSTREAM_URL"
            )
        left = left.strip()
        host: str | None = None
        port_str = left
        if ":" in left:
            host, _, port_str = left.rpartition(":")
            host = host.strip()
            if not host:
                raise ValueError(f"TOKMETER_LISTENERS entry {item!r} has an empty host")
        try:
            port = int(port_str.strip())
        except ValueError:
            raise ValueError(
                f"TOKMETER_LISTENERS entry {item!r} has a non-numeric port"
            ) from None
        if (host, port) in seen:
            raise ValueError(f"TOKMETER_LISTENERS lists {left} more than once")
        seen.add((host, port))
        listeners.append(Listener(host=host, port=port, upstream=upstream.strip()))
    if not listeners:
        raise ValueError(
            "TOKMETER_LISTENERS is set but contains no [HOST:]PORT=UPSTREAM_URL entries"
        )
    return tuple(listeners)


def load_settings() -> Settings:
    raw = os.environ.get("TOKMETER_LISTENERS")
    if raw is not None:
        listeners = _parse_listeners(raw)
    else:
        listeners = (
            Listener(
                port=int(os.environ.get("TOKMETER_PORT", "8079")),
                upstream=os.environ.get("TOKMETER_UPSTREAM", "http://127.0.0.1:8080"),
            ),
        )
    return Settings(
        listen_host=os.environ.get("TOKMETER_HOST", "127.0.0.1"),
        listeners=listeners,
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
