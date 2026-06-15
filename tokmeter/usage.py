from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class ParsedUsage:
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tokens_per_sec: float | None = None


def classify_endpoint(path: str) -> str:
    if path.endswith("/chat/completions"):
        return "chat/completions"
    if path.endswith("/completions"):
        return "completions"
    if path.endswith("/embeddings"):
        return "embeddings"
    return "other"


def _usage_from_obj(obj: dict, into: ParsedUsage) -> None:
    if obj.get("model"):
        into.model = obj["model"]
    u = obj.get("usage")
    if isinstance(u, dict):
        into.prompt_tokens = u.get("prompt_tokens")
        into.completion_tokens = u.get("completion_tokens")
        into.total_tokens = u.get("total_tokens")
    t = obj.get("timings")
    if isinstance(t, dict) and t.get("predicted_per_second") is not None:
        into.tokens_per_sec = float(t["predicted_per_second"])


def parse_json_body(body: bytes) -> ParsedUsage:
    parsed = ParsedUsage()
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return parsed
    if isinstance(obj, dict):
        _usage_from_obj(obj, parsed)
    return parsed


def request_wants_stream(body: bytes) -> bool:
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return False
    return bool(isinstance(obj, dict) and obj.get("stream") is True)


def inject_include_usage(body: bytes) -> bytes:
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return body
    if not isinstance(obj, dict):
        return body
    opts = obj.get("stream_options")
    if not isinstance(opts, dict):
        opts = {}
    opts["include_usage"] = True
    obj["stream_options"] = opts
    return json.dumps(obj).encode()
