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


def _reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


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
        return _reply(result)

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, calls


def test_falls_back_when_json_schema_unsupported(settings):
    client, calls = make_client(settings, [_bad_request(), '{"entities": []}'])
    client.complete_json("sys", "user", EntitiesReply)
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[1]["response_format"]["type"] == "json_object"
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
