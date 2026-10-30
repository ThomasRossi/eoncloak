import os

import pytest

from eoncloak.config import (
    ConfigError,
    check_local_only,
    is_local_url,
    load_settings,
    read_env_file,
)


def test_defaults(settings):
    default = load_settings()
    assert default.llm.provider == "openrouter"
    assert default.llm.resolved_base_url() == "https://openrouter.ai/api/v1"
    assert settings.llm.resolved_base_url() == "http://localhost:11434/v1"


def test_precedence_file_env_override(settings, tmp_path, monkeypatch):
    (tmp_path / "eoncloak.toml").write_text(
        '[llm]\nprovider = "vllm"\nmodel = "from-file"\n[anonymize]\nchunk_chars = 1234\n'
    )
    assert load_settings().llm.model == "from-file"
    assert load_settings().anonymize.chunk_chars == 1234

    monkeypatch.setenv("EONCLOAK_MODEL", "from-env")
    assert load_settings().llm.model == "from-env"
    assert load_settings(model="from-flag").llm.model == "from-flag"


def test_env_file(settings, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EONCLOAK_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "# keys\nexport OPENROUTER_API_KEY='sk-or-from-file'\nEONCLOAK_MODEL=from-dotenv # c\n"
    )
    loaded = load_settings()
    assert loaded.llm.resolved_api_key() == "sk-or-from-file"
    assert loaded.llm.model == "from-dotenv"
    assert "sk-or" not in repr(loaded)
    # The file does not leak into the process environment, and real variables win.
    assert "OPENROUTER_API_KEY" not in os.environ
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-env")
    assert load_settings().llm.resolved_api_key() == "sk-or-from-env"
    # Local providers don't pick up the OpenRouter key.
    assert load_settings(provider="ollama").llm.api_key is None


def test_read_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text('A=1\nB="two words"\nC = x # comment\nnot a pair\nD=a#b\nE=\n')
    assert read_env_file(path) == {"A": "1", "B": "two words", "C": "x", "D": "a#b", "E": ""}


def test_unknown_provider(settings):
    with pytest.raises(ValueError):
        load_settings(provider="nope")


def test_custom_needs_base_url(settings):
    with pytest.raises(ConfigError):
        load_settings(provider="custom").llm.resolved_base_url()


def test_missing_config_file(settings, tmp_path):
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "missing.toml")


def test_label_format_validated(settings):
    with pytest.raises(ValueError):
        load_settings(label_format="[{type}]")


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:11434/v1", True),
        ("http://127.0.0.1:8000/v1", True),
        ("http://192.168.1.20:8000/v1", True),
        ("http://10.0.0.5/v1", True),
        ("http://100.101.102.103:8000/v1", True),  # Tailscale
        ("http://[::1]:8080/v1", True),
        ("http://8.8.8.8/v1", False),
        ("not a url", False),
    ],
)
def test_is_local_url(url, local):
    assert is_local_url(url) is local


def test_local_only_refuses_remote(settings):
    with pytest.raises(ConfigError):
        check_local_only(
            load_settings(provider="vllm", base_url="http://8.8.8.8/v1", local_only=True)
        )
    check_local_only(load_settings(provider="ollama", local_only=True))
    # Rules-only mode never sends anything, so it is always allowed.
    check_local_only(
        load_settings(provider="vllm", base_url="http://8.8.8.8/v1", local_only=True, use_llm=False)
    )
