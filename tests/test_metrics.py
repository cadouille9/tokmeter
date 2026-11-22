import httpx
import pytest
from starlette.testclient import TestClient

from tokmeter import config
from tokmeter.db import UsageRecord
from tokmeter.metrics import (
    CountingWriter,
    UsageCounters,
    create_metrics_app,
    detect_backend,
    model_label,
    inject_labels,
    merge_expositions,
)


def rec(**kw):
    base: dict = dict(
        ts="2026-11-22T00:00:00+00:00",
        model="m1",
        endpoint="chat/completions",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        duration_ms=1500,
        tokens_per_sec=13.3,
        stream=1,
        status=200,
        upstream="http://127.0.0.1:8080",
    )
    base.update(kw)
    return UsageRecord(**base)


# ---------------------------------------------------------------- label injection / merging


def test_inject_labels_without_existing_labels():
    assert inject_labels("foo_total 3", {"backend": "x"}) == 'foo_total{backend="x"} 3'


def test_inject_labels_keeps_existing_and_timestamp():
    out = inject_labels('foo{a="1",b="}"} 2.5 1700000000', {"backend": "x"})
    assert out == 'foo{backend="x",a="1",b="}"} 2.5 1700000000'


def test_inject_labels_does_not_duplicate_a_present_label():
    out = inject_labels('foo{backend="orig"} 1', {"backend": "x", "upstream": "u"})
    assert out == 'foo{upstream="u",backend="orig"} 1'


def test_merge_groups_shared_family_once():
    a = (
        "# HELP vllm:num_requests_running Running.\n"
        "# TYPE vllm:num_requests_running gauge\n"
        'vllm:num_requests_running{model_name="q"} 1\n'
        "# HELP strata:live_tok_s Live.\n"
        "# TYPE strata:live_tok_s gauge\n"
        "strata:live_tok_s 39.5\n"
    )
    b = (
        "# HELP vllm:num_requests_running Running (vLLM).\n"
        "# TYPE vllm:num_requests_running gauge\n"
        'vllm:num_requests_running{model_name="v"} 0\n'
    )
    lines = merge_expositions([({"backend": "strata"}, a), ({"backend": "vllm"}, b)])
    assert lines.count("# TYPE vllm:num_requests_running gauge") == 1
    assert "# HELP vllm:num_requests_running Running." in lines
    i = lines.index("# TYPE vllm:num_requests_running gauge")
    # both engines' samples sit directly under the single TYPE line
    assert lines[i + 1] == 'vllm:num_requests_running{backend="strata",model_name="q"} 1'
    assert lines[i + 2] == 'vllm:num_requests_running{backend="vllm",model_name="v"} 0'
    assert 'strata:live_tok_s{backend="strata"} 39.5' in lines


def test_merge_keeps_histogram_suffixes_in_family():
    text = (
        "# TYPE vllm:ttft_seconds histogram\n"
        'vllm:ttft_seconds_bucket{le="1.0"} 2\n'
        "vllm:ttft_seconds_sum 1.2\n"
        "vllm:ttft_seconds_count 2\n"
        "# EOF\n"
    )
    lines = merge_expositions([({"backend": "s"}, text)])
    assert lines == [
        "# TYPE vllm:ttft_seconds histogram",
        'vllm:ttft_seconds_bucket{backend="s",le="1.0"} 2',
        'vllm:ttft_seconds_sum{backend="s"} 1.2',
        'vllm:ttft_seconds_count{backend="s"} 2',
    ]


@pytest.mark.parametrize(
    "text,owned_by,expected",
    [
        ("strata:live_tok_s 1\nvllm:x 1", None, "strata"),
        ("vllm:x 1", None, "vllm"),
        ("llamacpp:prompt_tokens_total 1", None, "llama.cpp"),
        (None, "llamacpp", "llama.cpp"),
        (None, "h2o-shim", "h2o-shim"),
        (None, None, "unknown"),
    ],
)
def test_detect_backend(text, owned_by, expected):
    assert detect_backend(text, owned_by) == expected


# ---------------------------------------------------------------- counters


class ListWriter:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


def test_counting_writer_counts_and_delegates():
    inner, counters = ListWriter(), UsageCounters()
    w = CountingWriter(inner, counters)
    w.write(rec())
    w.write(rec(prompt_tokens=None, completion_tokens=5, duration_ms=500))
    assert len(inner.records) == 2
    text = "\n".join(counters.render())
    labels = 'upstream="127.0.0.1:8080",model="m1"'
    assert f'tokmeter_requests_total{{{labels},endpoint="chat/completions",status="200"}} 2' in text
    assert f'tokmeter_tokens_total{{{labels},kind="prompt"}} 100' in text
    assert f'tokmeter_tokens_total{{{labels},kind="completion"}} 25' in text
    assert f"tokmeter_request_seconds_total{{{labels}}} 2.000" in text


# ---------------------------------------------------------------- /metrics app


STRATA_METRICS = (
    "# HELP vllm:num_requests_running Running.\n"
    "# TYPE vllm:num_requests_running gauge\n"
    'vllm:num_requests_running{model_name="qwen-strata"} 1\n'
    "# TYPE strata:live_tok_s gauge\n"
    "strata:live_tok_s 39.5\n"
)


def fake_upstreams(request: httpx.Request) -> httpx.Response:
    port = request.url.port
    if port == 8080:  # Strata: models + prometheus text
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen-strata", "owned_by": "strata"}]})
        if request.url.path == "/metrics":
            return httpx.Response(200, text=STRATA_METRICS, headers={"content-type": "text/plain"})
    if port == 8092:  # llama.cpp without --metrics
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "winnow", "owned_by": "llamacpp"}]})
        return httpx.Response(501, json={"error": "metrics disabled"})
    raise httpx.ConnectError("refused", request=request)  # 8010: vLLM stopped


def test_metrics_app_reports_up_down_and_passthrough():
    counters = UsageCounters()
    counters.observe(rec())
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake_upstreams))
    app = create_metrics_app(
        upstreams=[
            "http://127.0.0.1:8080",
            "http://127.0.0.1:8010",
            "http://127.0.0.1:8092",
            "http://127.0.0.1:8080",  # duplicate upstream (LAN + loopback listener)
        ],
        counters=counters,
        client=client,
    )
    resp = TestClient(app).get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text

    assert 'tokmeter_backend_up{upstream="127.0.0.1:8080"} 1' in body
    assert 'tokmeter_backend_up{upstream="127.0.0.1:8010"} 0' in body
    assert 'tokmeter_backend_up{upstream="127.0.0.1:8092"} 1' in body
    assert body.count('tokmeter_backend_up{upstream="127.0.0.1:8080"}') == 1
    assert 'tokmeter_backend_info{upstream="127.0.0.1:8080",backend="strata",model="qwen-strata"} 1' in body
    assert 'tokmeter_backend_info{upstream="127.0.0.1:8092",backend="llama.cpp",model="winnow"} 1' in body
    assert 'tokmeter_backend_metrics_ok{upstream="127.0.0.1:8092"} 0' in body
    assert (
        'vllm:num_requests_running{backend="strata",upstream="127.0.0.1:8080",model_name="qwen-strata"} 1'
        in body
    )
    assert 'strata:live_tok_s{backend="strata",upstream="127.0.0.1:8080"} 39.5' in body
    assert "tokmeter_requests_total{" in body


# ---------------------------------------------------------------- config


def test_metrics_listeners_parse(monkeypatch):
    monkeypatch.setenv("TOKMETER_METRICS", "9188, 192.168.1.252:9188")
    monkeypatch.delenv("TOKMETER_LISTENERS", raising=False)
    s = config.load_settings()
    assert s.metrics_listeners == ((None, 9188), ("192.168.1.252", 9188))


def test_metrics_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TOKMETER_METRICS", raising=False)
    assert config.load_settings().metrics_listeners == ()


def test_metrics_listener_rejects_bad_port(monkeypatch):
    monkeypatch.setenv("TOKMETER_METRICS", "host:abc")
    with pytest.raises(ValueError):
        config.load_settings()


def test_model_label_trims_gguf_paths():
    assert model_label("/mnt/ai/models/x/Qwen3.8-UD-Q6_K_XL.gguf") == "Qwen3.8-UD-Q6_K_XL"
    assert model_label("qwen3.8-flash-next-q8-strata") == "qwen3.8-flash-next-q8-strata"
    assert model_label(None) is None
