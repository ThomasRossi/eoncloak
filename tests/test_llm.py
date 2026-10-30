from types import SimpleNamespace

import openai
import pytest

from eoncloak.detect import EntitiesReply
from eoncloak.llm import LLMClient, LLMError, _strict_schema, parse_json_reply


def test_parse_reply_with_thinking_and_fences():
    content = '<think>hmm</think>\n```json\n{"entities": []}\n```'
    assert parse_json_reply(content, EntitiesReply).entities == []


def test_parse_reply_without_json():
    with pytest.raises(ValueError):
        parse_json_reply("sorry, no", EntitiesReply)


def test_strict_schema_closes_objects():
    schema = _strict_schema(EntitiesReply)
    item = schema["$defs"]["EntityItem"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"text", "type", "canonical"}


def _reply(content, finish_reason="stop"):
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


def _bad_request():
    response = SimpleNamespace(request=None, status_code=400, headers={})
    return openai.BadRequestError("bad", response=response, body=None)


def make_client(settings, responses):
    client = LLMClient(settings.llm)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, tuple):
            return _reply(*result)
        return _reply(result)

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, calls


def test_falls_back_when_json_schema_unsupported(settings):
    client, calls = make_client(settings, [_bad_request(), _bad_request(), '{"entities": []}'])
    client.complete_json("sys", "user", EntitiesReply)
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_schema"
    assert calls[2]["response_format"]["type"] == "json_object"
    assert calls[2]["temperature"] == 0.0
    assert client.structured_output_mode == "json_object"


def test_retries_invalid_json(settings):
    client, calls = make_client(settings, ["not json", '{"entities": []}'])
    assert client.complete_json("sys", "user", EntitiesReply).entities == []
    assert len(calls) == 2
    assert "not valid JSON" in calls[1]["messages"][-1]["content"]


def test_gives_up_after_retries(settings):
    client, _ = make_client(settings, ["nope"] * 3)
    with pytest.raises(LLMError):
        client.complete_json("sys", "user", EntitiesReply)


def test_openrouter_requires_key_and_denies_data_collection(settings, monkeypatch):
    from eoncloak.config import load_settings

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EONCLOAK_API_KEY", raising=False)
    with pytest.raises(LLMError):
        LLMClient(load_settings().llm)

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    client, calls = make_client(load_settings(), ['{"entities": []}'])
    client.complete_json("sys", "user", EntitiesReply)
    provider = calls[0]["extra_body"]["provider"]
    assert provider["data_collection"] == "deny"
    assert provider["require_parameters"] is True


def _no_endpoints():
    response = SimpleNamespace(request=None, status_code=404, headers={})
    return openai.NotFoundError("No endpoints found", response=response, body=None)


def test_drops_temperature_when_refused(settings):
    # OpenRouter answers 404 when no provider accepts every parameter (e.g. Claude Haiku 5.5
    # takes no temperature); keep json_schema and retry without temperature.
    client, calls = make_client(settings, [_no_endpoints(), '{"entities": []}', '{"entities": []}'])
    client.complete_json("sys", "user", EntitiesReply)
    client.complete_json("sys", "user", EntitiesReply)
    assert calls[0]["temperature"] == 0.0
    assert "temperature" not in calls[1] and "temperature" not in calls[2]
    assert calls[1]["response_format"]["type"] == "json_schema"
    assert client.structured_output_mode == "json_schema"


@pytest.mark.parametrize(
    ("provider", "reasoning", "body", "top_level"),
    [
        ("openrouter", "off", {"reasoning": {"enabled": False}}, {}),
        ("openrouter", "low", {"reasoning": {"effort": "low"}}, {}),
        ("vllm", "off", {"chat_template_kwargs": {"enable_thinking": False}}, {}),
        ("vllm", "high", {}, {"reasoning_effort": "high"}),
        ("vllm", "default", {}, {}),
    ],
)
def test_reasoning(settings, monkeypatch, provider, reasoning, body, top_level):
    from eoncloak.config import load_settings

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    loaded = load_settings(provider=provider, model="m", reasoning=reasoning)
    client, calls = make_client(loaded, ['{"entities": []}'])
    client.complete_json("sys", "user", EntitiesReply)
    extra_body = dict(calls[0].get("extra_body", {}))
    extra_body.pop("provider", None)
    assert extra_body == body
    assert {k: calls[0][k] for k in ("reasoning_effort",) if k in calls[0]} == top_level


def test_runaway_reply_is_retried_fresh(settings):
    runaway = ('{"entities": [' + '{"text": "x", "type": "ORG", "canonical": "x"}, ' * 50, "length")
    client, calls = make_client(settings, [runaway, '{"entities": []}'])
    assert client.complete_json("sys", "user", EntitiesReply).entities == []
    assert calls[0]["max_tokens"] == 16384
    assert client.usage.truncated == 1
    # The cut-off reply is not sent back to the model.
    assert calls[1]["messages"] == calls[0]["messages"]


def test_output_cap_and_quantizations(settings, monkeypatch):
    from eoncloak.config import load_settings

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    loaded = load_settings(model="m", reasoning="off", openrouter_quantizations=["fp8", "bf16"])
    client, calls = make_client(loaded, ['{"entities": []}'])
    client.complete_json("sys", "user", EntitiesReply)
    assert calls[0]["max_tokens"] == 4096
    assert calls[0]["extra_body"]["provider"]["quantizations"] == ["fp8", "bf16"]
