import json

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tokmeter import db
from tokmeter.proxy import create_app


class RecordingWriter:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


def fake_upstream():
    async def chat(request):
        body = await request.body()
        assert json.loads(body)["model"] == "m1"
        return JSONResponse(
            {
                "model": "m1",
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
            }
        )

    return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"])])


def make_client_for(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://upstream")


def test_non_streaming_forwards_and_records():
    writer = RecordingWriter()
    app = create_app(upstream="http://upstream", writer=writer, client=make_client_for(fake_upstream()))
    client = TestClient(app)

    resp = client.post("/v1/chat/completions", json={"model": "m1", "messages": []})

    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "hi"

    assert len(writer.records) == 1
    rec = writer.records[0]
    assert isinstance(rec, db.UsageRecord)
    assert rec.model == "m1"
    assert rec.endpoint == "chat/completions"
    assert rec.prompt_tokens == 11
    assert rec.completion_tokens == 3
    assert rec.total_tokens == 14
    assert rec.stream == 0
    assert rec.status == 200
    assert rec.duration_ms is not None


def test_non_streaming_capture_failure_does_not_break_response(monkeypatch):
    writer = RecordingWriter()

    def boom(record):
        raise RuntimeError("db on fire")

    writer.write = boom
    app = create_app(upstream="http://upstream", writer=writer, client=make_client_for(fake_upstream()))
    client = TestClient(app)

    resp = client.post("/v1/chat/completions", json={"model": "m1", "messages": []})
    assert resp.status_code == 200
    assert resp.json()["model"] == "m1"


def fake_sse_upstream(captured):
    async def chat(request):
        body = await request.body()
        captured["request_body"] = body

        async def gen():
            yield b'data: {"model":"m1","choices":[{"delta":{"content":"Hel"}}]}\n\n'
            yield b'data: {"model":"m1","choices":[{"delta":{"content":"lo"}}]}\n\n'
            yield (
                b'data: {"model":"m1","choices":[],'
                b'"usage":{"prompt_tokens":9,"completion_tokens":4,"total_tokens":13},'
                b'"timings":{"predicted_per_second":40.0}}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"])])


def test_streaming_forwards_and_records():
    writer = RecordingWriter()
    captured = {}
    app = create_app(
        upstream="http://upstream",
        writer=writer,
        client=make_client_for(fake_sse_upstream(captured)),
    )
    client = TestClient(app)

    with client.stream(
        "POST", "/v1/chat/completions", json={"model": "m1", "messages": [], "stream": True}
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    # Client received the full SSE stream, including the usage chunk.
    assert "Hel" in text and "lo" in text
    assert "[DONE]" in text

    # The proxy injected include_usage on the way to the upstream.
    assert json.loads(captured["request_body"])["stream_options"]["include_usage"] is True

    assert len(writer.records) == 1
    rec = writer.records[0]
    assert rec.stream == 1
    assert rec.model == "m1"
    assert rec.prompt_tokens == 9
    assert rec.completion_tokens == 4
    assert rec.total_tokens == 13
    assert rec.tokens_per_sec == 40.0
