"""A thin client for any OpenAI-compatible chat completions endpoint.

OpenRouter, Ollama, llama.cpp (llama-server), vLLM, LM Studio, SGLang and NVIDIA NIM all
expose ``/v1/chat/completions``, so one client covers cloud and local deployments.
"""

from __future__ import annotations

import json
import re
from typing import Protocol, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from .config import LLMSettings, StructuredOutput

T = TypeVar("T", bound=BaseModel)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(Exception):
    pass


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
                raise LLMError("OpenRouter needs an API key: set OPENROUTER_API_KEY")
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
        self._model = settings.model or settings.profile.default_model

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
        for i, mode in enumerate(modes):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.settings.temperature,
                    **self._request_extras(mode, schema),
                )
            except openai.BadRequestError as exc:
                # The server rejected this response_format; fall back to a simpler mode.
                if i == len(modes) - 1:
                    raise LLMError(f"request to {self.base_url} was rejected: {exc}") from exc
                continue
            except openai.OpenAIError as exc:
                raise LLMError(f"request to {self.base_url} failed: {exc}") from exc
            self._mode = mode
            return response.choices[0].message.content or ""
        raise AssertionError("unreachable")

    def _request_extras(self, mode: StructuredOutput, schema: type[BaseModel]) -> dict:
        extras: dict = {}
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
        if self.settings.provider == "openrouter":
            provider: dict = {"data_collection": self.settings.openrouter_data_collection}
            if self.settings.openrouter_zdr:
                provider["zdr"] = True
            if mode == "json_schema":
                provider["require_parameters"] = True
            extras["extra_body"] = {"provider": provider}
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
