import json

from tokmeter import usage


def test_classify_endpoint():
    assert usage.classify_endpoint("/v1/chat/completions") == "chat/completions"
    assert usage.classify_endpoint("/v1/completions") == "completions"
    assert usage.classify_endpoint("/v1/embeddings") == "embeddings"
    assert usage.classify_endpoint("/health") == "other"


def test_parse_json_usage():
    body = json.dumps(
        {
            "model": "Qwen3.6-27B-UD-Q6_K_XL",
            "usage": {"prompt_tokens": 312, "completion_tokens": 128, "total_tokens": 440},
            "timings": {"predicted_per_second": 55.5},
        }
    ).encode()
    parsed = usage.parse_json_body(body)
    assert parsed.model == "Qwen3.6-27B-UD-Q6_K_XL"
    assert parsed.prompt_tokens == 312
    assert parsed.completion_tokens == 128
    assert parsed.total_tokens == 440
    assert parsed.tokens_per_sec == 55.5


def test_parse_json_body_without_usage_returns_none_counts():
    parsed = usage.parse_json_body(b'{"model":"m","choices":[]}')
    assert parsed.model == "m"
    assert parsed.prompt_tokens is None


def test_parse_json_body_invalid_is_safe():
    parsed = usage.parse_json_body(b"not json")
    assert parsed.model is None
    assert parsed.prompt_tokens is None


def test_request_wants_stream():
    assert usage.request_wants_stream(b'{"stream": true}') is True
    assert usage.request_wants_stream(b'{"stream": false}') is False
    assert usage.request_wants_stream(b"{}") is False
    assert usage.request_wants_stream(b"garbage") is False


def test_inject_include_usage():
    out = usage.inject_include_usage(b'{"model":"m","stream":true}')
    obj = json.loads(out)
    assert obj["stream_options"]["include_usage"] is True
    # preserves existing fields
    assert obj["model"] == "m"


def test_inject_include_usage_preserves_existing_stream_options():
    out = usage.inject_include_usage(b'{"stream":true,"stream_options":{"foo":1}}')
    obj = json.loads(out)
    assert obj["stream_options"]["foo"] == 1
    assert obj["stream_options"]["include_usage"] is True


def test_inject_include_usage_invalid_body_unchanged():
    assert usage.inject_include_usage(b"garbage") == b"garbage"
