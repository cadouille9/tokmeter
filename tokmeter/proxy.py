from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from . import usage as usage_mod
from .db import UsageRecord

log = logging.getLogger("tokmeter.proxy")

# Response headers we must not copy verbatim (length/encoding get recomputed; hop-by-hop dropped).
_DROP_RESP_HEADERS = {
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
}
_DROP_REQ_HEADERS = {"host", "content-length"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_write(writer, record: UsageRecord) -> None:
    try:
        writer.write(record)
    except Exception:  # capture is best-effort; never break the response
        log.exception("failed to enqueue usage record")


def create_app(*, upstream: str, writer, client: httpx.AsyncClient | None = None) -> Starlette:
    state = {"client": client}

    @asynccontextmanager
    async def lifespan(app):
        if state["client"] is None:
            state["client"] = httpx.AsyncClient(base_url=upstream, timeout=None)
        try:
            yield
        finally:
            if state["client"] is not None:
                await state["client"].aclose()

    async def handler(request: Request) -> Response:
        client = state["client"]
        body = await request.body()
        path = request.url.path
        endpoint = usage_mod.classify_endpoint(path)

        wants_stream = usage_mod.request_wants_stream(body)
        fwd_body = usage_mod.inject_include_usage(body) if wants_stream else body

        req_headers = [(k, v) for k, v in request.headers.items() if k.lower() not in _DROP_REQ_HEADERS]

        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query

        started = time.monotonic()
        upstream_req = client.build_request(
            request.method, target, headers=req_headers, content=fwd_body
        )
        resp = await client.send(upstream_req, stream=True)

        resp_headers = [(k, v) for k, v in resp.headers.items() if k.lower() not in _DROP_RESP_HEADERS]
        content_type = resp.headers.get("content-type", "")
        is_sse = "text/event-stream" in content_type

        if is_sse:
            return _stream_response(
                resp, resp_headers, endpoint, upstream, writer, started
            )

        raw = await resp.aread()
        await resp.aclose()
        duration_ms = int((time.monotonic() - started) * 1000)
        parsed = usage_mod.parse_json_body(raw)
        _safe_write(
            writer,
            UsageRecord(
                ts=_now_iso(),
                model=parsed.model,
                endpoint=endpoint,
                prompt_tokens=parsed.prompt_tokens,
                completion_tokens=parsed.completion_tokens,
                total_tokens=parsed.total_tokens,
                duration_ms=duration_ms,
                tokens_per_sec=parsed.tokens_per_sec,
                stream=0,
                status=resp.status_code,
                upstream=upstream,
            ),
        )
        return Response(content=raw, status_code=resp.status_code, headers=dict(resp_headers))

    def _stream_response(resp, resp_headers, endpoint, upstream, writer, started) -> StreamingResponse:
        chunks: list[bytes] = []

        async def body_iterator():
            try:
                async for chunk in resp.aiter_raw():
                    chunks.append(chunk)
                    yield chunk
            finally:
                await resp.aclose()
                duration_ms = int((time.monotonic() - started) * 1000)
                raw_text = b"".join(chunks).decode("utf-8", errors="replace")
                parsed = usage_mod.parse_sse_text(raw_text)
                _safe_write(
                    writer,
                    UsageRecord(
                        ts=_now_iso(),
                        model=parsed.model,
                        endpoint=endpoint,
                        prompt_tokens=parsed.prompt_tokens,
                        completion_tokens=parsed.completion_tokens,
                        total_tokens=parsed.total_tokens,
                        duration_ms=duration_ms,
                        tokens_per_sec=parsed.tokens_per_sec,
                        stream=1,
                        status=resp.status_code,
                        upstream=upstream,
                    ),
                )

        return StreamingResponse(
            body_iterator(),
            status_code=resp.status_code,
            headers=dict(resp_headers),
            media_type=resp.headers.get("content-type"),
        )

    routes = [
        Route("/{path:path}", handler, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    ]
    return Starlette(routes=routes, lifespan=lifespan)
