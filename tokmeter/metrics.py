"""Prometheus /metrics for tokmeter: its own usage counters plus every upstream's metrics.

On each scrape, every distinct upstream is probed concurrently:

* ``GET /v1/models`` says whether it is up and which model it serves;
* ``GET /metrics`` (Prometheus text) is passed through with ``backend`` and
  ``upstream`` labels added, so one scrape target covers Strata, vLLM and
  llama.cpp, and Prometheus never has to reach the loopback-only servers.

Several upstreams may expose the same metric family (Strata deliberately reuses
vLLM's names), and the text format requires each family's HELP/TYPE once with
its samples grouped together, so the expositions are merged by family rather
than concatenated.
"""
from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .db import UsageRecord

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
PROBE_TIMEOUT = httpx.Timeout(3.0, connect=1.0)

_SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})?\s+(.+)$")
_LABEL_NAME = re.compile(r'(?:^|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"')


def upstream_label(url: str | None) -> str:
    """http://127.0.0.1:8080 -> 127.0.0.1:8080 (the label used everywhere)."""
    if not url:
        return ""
    parts = urlsplit(url)
    return parts.netloc or url


def _escape(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**labels) -> str:
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
    return "{" + inner + "}"


# --------------------------------------------------------------------------- usage counters


class UsageCounters:
    """In-process counters fed by every usage record (reset on restart; use rate())."""

    def __init__(self):
        self._lock = threading.Lock()
        self.requests: dict[tuple, int] = {}
        self.tokens: dict[tuple, int] = {}
        self.seconds: dict[tuple, float] = {}

    def observe(self, record: UsageRecord) -> None:
        up = upstream_label(record.upstream)
        model = record.model or "unknown"
        with self._lock:
            rkey = (up, model, record.endpoint or "", str(record.status))
            self.requests[rkey] = self.requests.get(rkey, 0) + 1
            for kind, n in (("prompt", record.prompt_tokens), ("completion", record.completion_tokens)):
                if n:
                    tkey = (up, model, kind)
                    self.tokens[tkey] = self.tokens.get(tkey, 0) + int(n)
            if record.duration_ms is not None:
                skey = (up, model)
                self.seconds[skey] = self.seconds.get(skey, 0.0) + record.duration_ms / 1000.0

    def render(self) -> list[str]:
        with self._lock:
            requests = dict(self.requests)
            tokens = dict(self.tokens)
            seconds = dict(self.seconds)
        out = [
            "# HELP tokmeter_requests_total LLM requests proxied by tokmeter.",
            "# TYPE tokmeter_requests_total counter",
        ]
        for (up, model, endpoint, status), n in sorted(requests.items()):
            out.append(
                f"tokmeter_requests_total{_labels(upstream=up, model=model, endpoint=endpoint, status=status)} {n}"
            )
        out += [
            "# HELP tokmeter_tokens_total Tokens reported by upstream responses, by kind.",
            "# TYPE tokmeter_tokens_total counter",
        ]
        for (up, model, kind), n in sorted(tokens.items()):
            out.append(f"tokmeter_tokens_total{_labels(upstream=up, model=model, kind=kind)} {n}")
        out += [
            "# HELP tokmeter_request_seconds_total Wall time spent in proxied requests.",
            "# TYPE tokmeter_request_seconds_total counter",
        ]
        for (up, model), s in sorted(seconds.items()):
            out.append(f"tokmeter_request_seconds_total{_labels(upstream=up, model=model)} {s:.3f}")
        return out


class CountingWriter:
    """Wraps the usage writer: count first, then delegate (the proxy only calls .write)."""

    def __init__(self, inner, counters: UsageCounters):
        self.inner = inner
        self.counters = counters

    def write(self, record: UsageRecord) -> None:
        try:
            self.counters.observe(record)
        finally:
            self.inner.write(record)


# --------------------------------------------------------------------------- exposition merging


def inject_labels(line: str, extra: dict[str, str]) -> str | None:
    """Add labels to one sample line; existing labels of the same name win. None if unparseable."""
    m = _SAMPLE.match(line)
    if not m:
        return None
    name, block, rest = m.group(1), m.group(2), m.group(3)
    inner = block[1:-1].strip().rstrip(",") if block else ""
    present = set(_LABEL_NAME.findall(inner))
    add = ",".join(f'{k}="{_escape(v)}"' for k, v in extra.items() if k not in present)
    merged = ",".join(p for p in (add, inner) if p)
    return f"{name}{{{merged}}} {rest}"


@dataclass
class _Family:
    help: str | None = None
    type: str | None = None
    samples: list[str] = field(default_factory=list)


def merge_expositions(sources: list[tuple[dict[str, str], str]]) -> list[str]:
    """Merge Prometheus text expositions, adding each source's labels to its samples."""
    families: dict[str, _Family] = {}

    for extra, text in sources:
        current: str | None = None
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line == "# EOF":
                continue
            if line.startswith("#"):
                parts = line.split(None, 3)
                if len(parts) >= 3 and parts[1] in ("HELP", "TYPE"):
                    current = parts[2]
                    fam = families.setdefault(current, _Family())
                    value = parts[3] if len(parts) > 3 else ""
                    if parts[1] == "HELP" and fam.help is None:
                        fam.help = value
                    elif parts[1] == "TYPE" and fam.type is None:
                        fam.type = value
                continue
            m = _SAMPLE.match(line)
            if not m:
                continue
            name = m.group(1)
            fam_name = current if current and name.startswith(current) else name
            sample = inject_labels(line, extra)
            if sample is not None:
                families.setdefault(fam_name, _Family()).samples.append(sample)

    out: list[str] = []
    for name, fam in families.items():
        if not fam.samples:
            continue
        if fam.help is not None:
            out.append(f"# HELP {name} {fam.help}")
        if fam.type is not None:
            out.append(f"# TYPE {name} {fam.type}")
        out.extend(fam.samples)
    return out


# --------------------------------------------------------------------------- upstream probing


@dataclass
class Probe:
    upstream: str  # host:port label
    up: bool
    backend: str = "unknown"
    model: str | None = None
    metrics_text: str | None = None


def model_label(model_id: str | None) -> str | None:
    """llama.cpp reports the GGUF path when started without --alias: keep the file name."""
    if not model_id or "/" not in model_id:
        return model_id
    name = model_id.rstrip("/").rsplit("/", 1)[-1]
    return name[:-5] if name.lower().endswith(".gguf") else name


def detect_backend(metrics_text: str | None, owned_by: str | None) -> str:
    text = metrics_text or ""
    if "strata:" in text:  # Strata also exports vllm:* names, so check it first
        return "strata"
    if "vllm:" in text:
        return "vllm"
    if "llamacpp:" in text:
        return "llama.cpp"
    owner = (owned_by or "").lower()
    if owner in ("llamacpp", "llama.cpp", "llama-cpp"):
        return "llama.cpp"
    if owner:
        return owner
    return "unknown"


async def probe(client: httpx.AsyncClient, upstream: str) -> Probe:
    base = upstream.rstrip("/")
    label = upstream_label(upstream)
    try:
        resp = await client.get(f"{base}/v1/models", timeout=PROBE_TIMEOUT)
    except httpx.HTTPError:
        return Probe(upstream=label, up=False)
    if resp.status_code >= 500:
        return Probe(upstream=label, up=False)

    model = owned_by = None
    try:
        data = resp.json().get("data") or []
        if data:
            model = model_label(data[0].get("id"))
            owned_by = data[0].get("owned_by")
    except (ValueError, AttributeError):
        pass

    metrics_text = None
    try:
        mresp = await client.get(
            f"{base}/metrics",
            headers={"Accept": "text/plain;version=0.0.4"},
            timeout=PROBE_TIMEOUT,
        )
        ctype = mresp.headers.get("content-type", "")
        if mresp.status_code == 200 and "json" not in ctype and "html" not in ctype:
            metrics_text = mresp.text
    except httpx.HTTPError:
        pass

    return Probe(
        upstream=label,
        up=True,
        backend=detect_backend(metrics_text, owned_by),
        model=model,
        metrics_text=metrics_text,
    )


def render(counters: UsageCounters, probes: list[Probe]) -> str:
    out = [
        "# HELP tokmeter_backend_up Whether the upstream answers /v1/models.",
        "# TYPE tokmeter_backend_up gauge",
    ]
    out += [f"tokmeter_backend_up{_labels(upstream=p.upstream)} {int(p.up)}" for p in probes]
    out += [
        "# HELP tokmeter_backend_info The backend and model an up upstream is serving.",
        "# TYPE tokmeter_backend_info gauge",
    ]
    out += [
        f"tokmeter_backend_info{_labels(upstream=p.upstream, backend=p.backend, model=p.model or 'unknown')} 1"
        for p in probes
        if p.up
    ]
    out += [
        "# HELP tokmeter_backend_metrics_ok Whether the upstream's own /metrics was scraped.",
        "# TYPE tokmeter_backend_metrics_ok gauge",
    ]
    out += [
        f"tokmeter_backend_metrics_ok{_labels(upstream=p.upstream)} {int(p.metrics_text is not None)}"
        for p in probes
    ]
    out += counters.render()
    out += merge_expositions(
        [
            ({"backend": p.backend, "upstream": p.upstream}, p.metrics_text)
            for p in probes
            if p.metrics_text
        ]
    )
    return "\n".join(out) + "\n"


def create_metrics_app(
    *, upstreams: list[str], counters: UsageCounters, client: httpx.AsyncClient | None = None
) -> Starlette:
    """A tiny app serving GET /metrics for the given upstream base URLs."""
    unique = list(dict.fromkeys(upstreams))
    state = {"client": client}

    async def metrics(request: Request) -> Response:
        if state["client"] is None:
            state["client"] = httpx.AsyncClient()
        probes = await asyncio.gather(*(probe(state["client"], u) for u in unique))
        return Response(render(counters, list(probes)), media_type=CONTENT_TYPE)

    async def health(request: Request) -> Response:
        return Response("ok\n", media_type="text/plain")

    return Starlette(
        routes=[Route("/metrics", metrics, methods=["GET"]), Route("/health", health, methods=["GET"])]
    )
