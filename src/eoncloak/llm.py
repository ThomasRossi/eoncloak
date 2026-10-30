"""A thin client for any OpenAI-compatible chat completions endpoint.

OpenRouter, Ollama, llama.cpp (llama-server), vLLM, LM Studio, SGLang and NVIDIA NIM all
expose ``/v1/chat/completions``, so one client covers cloud and local deployments.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from .config import LLMSettings, StructuredOutput

T = TypeVar("T", bound=BaseModel)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(Exception):
    pass


@dataclass
class Usage:
    """Tokens and requests used by a client, as reported by the server."""

    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Replies cut off at max_output_tokens, and which host answered (OpenRouter reports it).
    truncated: int = 0
    providers: Counter[str] = field(default_factory=Counter)

    def add(self, response: object) -> None:
        self.requests += 1
        usage = getattr(response, "usage", None)
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        if getattr(response.choices[0], "finish_reason", None) == "length":
            self.truncated += 1
        provider = (getattr(response, "model_extra", None) or {}).get("provider")
        if provider:
            self.providers[provider] += 1


class JSONModel(Protocol):
    """What the detectors need from an LLM; tests substitute a fake implementation."""

    def complete_json(self, system: str, user: str, schema: type[T]) -> T: ...


def parse_json_reply(content: str, schema: type[T]) -> T:
    text = _THINK_RE.sub("", content).strip()
    text = _FENCE_RE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in reply")
    return schema.model_validate(json.loads(text[start : end + 1]))


class LLMClient:
    def __init__(self, settings: LLMSettings):
        self.settings = settings
        self.base_url = settings.resolved_base_url()
        headers = {}
        if settings.provider == "openrouter":
            if not settings.resolved_api_key():
                raise LLMError("OpenRouter needs an API key: put OPENROUTER_API_KEY in ./.env")
            headers = {"X-OpenRouter-Title": "eoncloak"}
        self._client = openai.OpenAI(
            base_url=self.base_url,
            # Local servers usually ignore the key, but the SDK requires a value.
            api_key=settings.resolved_api_key() or "not-needed",
            timeout=settings.timeout,
            max_retries=2,
            default_headers=headers,
        )
        self._mode: StructuredOutput = settings.structured_output
        self._temperature = settings.temperature
        self._truncated = False
        self._model = settings.model or settings.profile.default_model
        self.usage = Usage()

    @property
    def model(self) -> str:
        if self._model is None:
            self._model = self._discover_model()
        return self._model

    @property
    def structured_output_mode(self) -> StructuredOutput:
        """The response format in use ("auto" until the first request settles it)."""
        return self._mode

    def _discover_model(self) -> str:
        """Use the only model a local server is serving, if there is exactly one."""
        try:
            ids = [m.id for m in self._client.models.list()]
        except openai.OpenAIError as exc:
            raise LLMError(f"cannot list models at {self.base_url}: {exc}") from exc
        if len(ids) == 1:
            return ids[0]
        available = ", ".join(ids) or "none"
        raise LLMError(f"no model configured; pick one with --model (available: {available})")

    def list_models(self) -> list[str]:
        return [m.id for m in self._client.models.list()]

    def complete_json(self, system: str, user: str, schema: type[T]) -> T:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for _ in range(self.settings.max_retries + 1):
            content = self._chat(messages, schema)
            try:
                return parse_json_reply(content, schema)
            except (ValueError, ValidationError) as exc:
                last_error = exc
                if self._truncated:
                    # The reply hit max_output_tokens (usually a model repeating itself):
                    # ask again from scratch instead of sending the runaway text back.
                    continue
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": f"That reply was not valid JSON for the schema ({exc}). "
                        "Reply again with only the JSON object.",
                    },
                ]
        raise LLMError(f"model did not return valid JSON: {last_error}")

    def _chat(self, messages: list[dict], schema: type[BaseModel]) -> str:
        modes: list[StructuredOutput] = (
            ["json_schema", "json_object", "none"] if self._mode == "auto" else [self._mode]
        )
        temperatures = list(dict.fromkeys([self._temperature, None]))
        attempts = [(mode, temp) for mode in modes for temp in temperatures]
        for i, (mode, temperature) in enumerate(attempts):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **self._request_extras(mode, schema, temperature),
                )
            except (openai.BadRequestError, openai.NotFoundError) as exc:
                # A parameter was rejected (OpenRouter answers 404 when no provider accepts
                # them all): retry without temperature, which some models refuse, then with
                # a simpler response_format.
                if i == len(attempts) - 1:
                    raise LLMError(f"request to {self.base_url} was rejected: {exc}") from exc
                continue
            except openai.OpenAIError as exc:
                raise LLMError(f"request to {self.base_url} failed: {exc}") from exc
            self._mode, self._temperature = mode, temperature
            self.usage.add(response)
            choice = response.choices[0]
            self._truncated = getattr(choice, "finish_reason", None) == "length"
            return choice.message.content or ""
        raise AssertionError("unreachable")

    def _request_extras(
        self, mode: StructuredOutput, schema: type[BaseModel], temperature: float | None
    ) -> dict:
        extras: dict = {}
        extra_body: dict = {}
        if temperature is not None:
            extras["temperature"] = temperature
        extras["max_tokens"] = self.settings.resolved_max_output_tokens()
        if mode == "json_schema":
            extras["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            }
        elif mode == "json_object":
            extras["response_format"] = {"type": "json_object"}
        reasoning = self.settings.reasoning
        if self.settings.provider == "openrouter":
            provider: dict = {"data_collection": self.settings.openrouter_data_collection}
            if self.settings.openrouter_zdr:
                provider["zdr"] = True
            if self.settings.openrouter_quantizations:
                provider["quantizations"] = self.settings.openrouter_quantizations
            if mode == "json_schema":
                provider["require_parameters"] = True
            extra_body["provider"] = provider
            if reasoning == "off":
                extra_body["reasoning"] = {"enabled": False}
            elif reasoning != "default":
                extra_body["reasoning"] = {"effort": reasoning}
        elif reasoning == "off":
            extra_body["chat_template_kwargs"] = {"enable_thinking": False}
        elif reasoning != "default":
            extras["reasoning_effort"] = reasoning
        if extra_body:
            extras["extra_body"] = extra_body
        return extras


def _strict_schema(schema: type[BaseModel]) -> dict:
    """JSON schema with ``additionalProperties: false`` everywhere, as strict mode requires."""
    data = schema.model_json_schema()

    def visit(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(data)
    return data
