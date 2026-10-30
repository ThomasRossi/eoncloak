"""Configuration: provider profiles, config file, environment variables.

Precedence (lowest to highest): built-in defaults < config file < ``./.env`` < environment
< explicit overrides (CLI flags or keyword arguments).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from .entities import ALL_TYPES, DEFAULT_LABEL_FORMAT, EntityType


@dataclass(frozen=True)
class ProviderProfile:
    base_url: str
    default_model: str | None
    api_key_env: str | None = None


# Every profile speaks the OpenAI-compatible /v1/chat/completions API.
PROFILES: dict[str, ProviderProfile] = {
    "openrouter": ProviderProfile(
        "https://openrouter.ai/api/v1",
        "anthropic/claude-haiku-5.5",
        api_key_env="OPENROUTER_API_KEY",
    ),
    "ollama": ProviderProfile("http://localhost:11434/v1", None),
    "llamacpp": ProviderProfile("http://localhost:8080/v1", None),
    "vllm": ProviderProfile("http://localhost:8000/v1", None),
    "lmstudio": ProviderProfile("http://localhost:1234/v1", None),
    "custom": ProviderProfile("", None),
}

StructuredOutput = Literal["auto", "json_schema", "json_object", "none"]
Reasoning = Literal["default", "off", "low", "medium", "high"]


class LLMSettings(BaseModel):
    provider: str = "openrouter"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    # None sends no temperature; models that refuse one are retried without it anyway.
    temperature: float | None = 0.0
    timeout: float = 180.0
    max_retries: int = 2
    # Upper bound for one reply, so a model stuck repeating itself can't run for minutes.
    # None: 4096 with reasoning "off" (an entity list is far shorter), otherwise 16384.
    max_output_tokens: int | None = None
    structured_output: StructuredOutput = "auto"
    # Thinking for reasoning models: "default" leaves it to the model, "off" disables it.
    reasoning: Reasoning = "default"
    # OpenRouter only: refuse providers that store prompts; optionally require zero retention.
    openrouter_data_collection: Literal["allow", "deny"] = "deny"
    openrouter_zdr: bool = False
    # OpenRouter only: accept just these quantizations, e.g. ["fp8", "bf16"], to skip
    # heavily compressed (fp4/int4) hosts. Closed models report "unknown".
    openrouter_quantizations: list[str] | None = None

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        if value not in PROFILES:
            raise ValueError(f"unknown provider {value!r}; choose one of {', '.join(PROFILES)}")
        return value

    @property
    def profile(self) -> ProviderProfile:
        return PROFILES[self.provider]

    def resolved_base_url(self) -> str:
        url = self.base_url or self.profile.base_url
        if not url:
            raise ConfigError("provider 'custom' needs a base_url")
        return url.rstrip("/")

    def resolved_max_output_tokens(self) -> int:
        if self.max_output_tokens is not None:
            return self.max_output_tokens
        return 4096 if self.reasoning == "off" else 16384

    def resolved_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if os.environ.get("EONCLOAK_API_KEY"):
            return os.environ["EONCLOAK_API_KEY"]
        if self.profile.api_key_env:
            return os.environ.get(self.profile.api_key_env)
        return None


class AnonymizeSettings(BaseModel):
    entities: list[EntityType] = Field(default_factory=lambda: list(ALL_TYPES))
    label_format: str = DEFAULT_LABEL_FORMAT
    # "auto" asks the LLM for the main language; otherwise an ISO 639-1 code such as "it".
    language: str = "auto"
    # ISO 3166-1 alpha-2 region used to recognise local phone numbers; "auto" infers it.
    region: str = "auto"
    chunk_chars: int = 6000
    # Send each distinct line to the model once (repeated signatures, quoted headers, ...).
    skip_repeated_lines: bool = True
    local_only: bool = False
    use_llm: bool = True
    scrub_metadata: bool = True

    @field_validator("label_format")
    @classmethod
    def _label_has_placeholders(cls, value: str) -> str:
        if "{type}" not in value or "{n}" not in value:
            raise ValueError("label_format must contain both {type} and {n}")
        return value


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    anonymize: AnonymizeSettings = Field(default_factory=AnonymizeSettings)


class ConfigError(Exception):
    pass


ENV_VARS: dict[str, tuple[str, str]] = {
    "EONCLOAK_PROVIDER": ("llm", "provider"),
    "EONCLOAK_MODEL": ("llm", "model"),
    "EONCLOAK_BASE_URL": ("llm", "base_url"),
    "EONCLOAK_REASONING": ("llm", "reasoning"),
    "EONCLOAK_LOCAL_ONLY": ("anonymize", "local_only"),
    "EONCLOAK_LANGUAGE": ("anonymize", "language"),
}


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file: ``KEY=value`` lines, optional ``export``, quotes and comments."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def environment(env_file: Path | None = None) -> dict[str, str]:
    """``os.environ`` on top of ``./.env``; the file never changes the real environment."""
    path = env_file or Path.cwd() / ".env"
    values = read_env_file(path) if path.is_file() else {}
    return values | dict(os.environ)


def default_config_paths() -> list[Path]:
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return [Path.cwd() / "eoncloak.toml", xdg / "eoncloak" / "config.toml"]


def load_settings(
    config_path: Path | None = None, *, env_file: Path | None = None, **overrides: Any
) -> Settings:
    """Build settings from the config file, ``.env``, the environment and ``overrides``.

    ``overrides`` use flat names (``provider``, ``model``, ``base_url``, ``local_only``,
    ``language``, ...) and ``None`` values are ignored. ``env_file`` defaults to ``./.env``.
    """
    data: dict[str, dict[str, Any]] = {"llm": {}, "anonymize": {}}

    path = config_path
    if path is None:
        path = next((p for p in default_config_paths() if p.is_file()), None)
    elif not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    if path is not None:
        with path.open("rb") as fh:
            file_data = tomllib.load(fh)
        for section in data:
            data[section].update(file_data.get(section, {}))

    env = environment(env_file)
    for var, (section, key) in ENV_VARS.items():
        if var in env:
            data[section][key] = env[var]

    llm_fields = set(LLMSettings.model_fields)
    for key, value in overrides.items():
        if value is None:
            continue
        section = "llm" if key in llm_fields else "anonymize"
        if key not in llm_fields and key not in AnonymizeSettings.model_fields:
            raise TypeError(f"unknown setting: {key}")
        data[section][key] = value

    if not data["llm"].get("api_key"):
        profile = PROFILES.get(data["llm"].get("provider", "openrouter"))
        key_vars = ["EONCLOAK_API_KEY", profile.api_key_env if profile else None]
        data["llm"]["api_key"] = next((env[v] for v in key_vars if v and env.get(v)), None)

    return Settings.model_validate(data)


# -- privacy guard -------------------------------------------------------------------

# Carrier-grade NAT space, used by Tailscale and similar overlays to reach home/lab boxes.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_local_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip in _CGNAT


def is_local_url(url: str) -> bool:
    """True when ``url`` resolves only to loopback / private-network addresses."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        return _is_local_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addresses = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
    return bool(addresses) and all(_is_local_ip(a) for a in addresses)


def check_local_only(settings: Settings) -> None:
    if not (settings.anonymize.local_only and settings.anonymize.use_llm):
        return
    url = settings.llm.resolved_base_url()
    if not is_local_url(url):
        raise ConfigError(
            f"local_only is enabled but {url} is not on this machine or a private network; "
            "refusing to send document text to it"
        )


EXAMPLE_CONFIG = """\
# eoncloak configuration.
# Looked up in ./eoncloak.toml, then ~/.config/eoncloak/config.toml.

[llm]
# openrouter (default, cloud) | ollama | llamacpp | vllm | lmstudio | custom
provider = "openrouter"
# model = "anthropic/claude-haiku-5.5"
# base_url = "http://dgx.local:8000/v1"   # override the profile's default address
# Keep keys out of this file: put OPENROUTER_API_KEY (or EONCLOAK_API_KEY) in ./.env.
temperature = 0.0
timeout = 180
# Cap on one reply (includes thinking tokens); stops a model that loops.
# Default: 4096 with reasoning = "off", otherwise 16384.
# max_output_tokens = 16384
# auto | json_schema | json_object | none
structured_output = "auto"
# Thinking: default | off | low | medium | high. "off" is much faster for Qwen3-style models;
# on local servers it sends chat_template_kwargs.enable_thinking=false (vLLM, SGLang, llama.cpp).
reasoning = "default"
# OpenRouter: only route to providers that do not store prompts.
openrouter_data_collection = "deny"
openrouter_zdr = false
# Only providers serving these quantizations (skips fp4/int4 hosts of open models):
# openrouter_quantizations = ["fp8", "bf16"]

[anonymize]
# Refuse to run unless the LLM is on localhost or a private network.
local_only = false
entities = ["PERSON", "ORG", "ADDRESS", "EMAIL", "PHONE", "IBAN", "ID", "URL", "BIRTH_DATE"]
label_format = "[{type}_{n}]"
language = "auto"   # or an ISO 639-1 code, e.g. "it"
region = "auto"     # or an ISO 3166-1 code for phone numbers, e.g. "IT"
chunk_chars = 6000
# The model reads each distinct line once; replacement still covers every copy.
skip_repeated_lines = true
scrub_metadata = true
"""
