"""Configuration: provider profiles, config file, environment variables.

Precedence (lowest to highest): built-in defaults < config file < environment < explicit
overrides (CLI flags or keyword arguments).
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


class LLMSettings(BaseModel):
    provider: str = "openrouter"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    timeout: float = 180.0
    max_retries: int = 2
    structured_output: StructuredOutput = "auto"
    # OpenRouter only: refuse providers that store prompts; optionally require zero retention.
    openrouter_data_collection: Literal["allow", "deny"] = "deny"
    openrouter_zdr: bool = False

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
    "EONCLOAK_LOCAL_ONLY": ("anonymize", "local_only"),
    "EONCLOAK_LANGUAGE": ("anonymize", "language"),
}


def default_config_paths() -> list[Path]:
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return [Path.cwd() / "eoncloak.toml", xdg / "eoncloak" / "config.toml"]


def load_settings(config_path: Path | None = None, **overrides: Any) -> Settings:
    """Build settings from the config file, the environment and ``overrides``.

    ``overrides`` use flat names (``provider``, ``model``, ``base_url``, ``local_only``,
    ``language``, ...) and ``None`` values are ignored.
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

    for var, (section, key) in ENV_VARS.items():
        if var in os.environ:
            data[section][key] = os.environ[var]

    llm_fields = set(LLMSettings.model_fields)
    for key, value in overrides.items():
        if value is None:
            continue
        section = "llm" if key in llm_fields else "anonymize"
        if key not in llm_fields and key not in AnonymizeSettings.model_fields:
            raise TypeError(f"unknown setting: {key}")
        data[section][key] = value

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
# Prefer environment variables for keys: OPENROUTER_API_KEY or EONCLOAK_API_KEY.
temperature = 0.0
timeout = 180
# auto | json_schema | json_object | none
structured_output = "auto"
# OpenRouter: only route to providers that do not store prompts.
openrouter_data_collection = "deny"
openrouter_zdr = false

[anonymize]
# Refuse to run unless the LLM is on localhost or a private network.
local_only = false
entities = ["PERSON", "ORG", "ADDRESS", "EMAIL", "PHONE", "IBAN", "ID"]
label_format = "[{type}_{n}]"
language = "auto"   # or an ISO 639-1 code, e.g. "it"
region = "auto"     # or an ISO 3166-1 code for phone numbers, e.g. "IT"
chunk_chars = 6000
scrub_metadata = true
"""
