import json

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
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
