from __future__ import annotations

import re

import pytest

from eoncloak.config import load_settings
from eoncloak.detect import EntitiesReply, LanguageReply
from eoncloak.entities import EntityType


class FakeLLM:
    """Stands in for a model: reports the configured entities that occur in each chunk."""

    def __init__(self, entities: dict[str, tuple[EntityType, str]], language="en", country=None):
        self.entities = entities
        self.language = language
        self.country = country
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        if schema is LanguageReply:
            return LanguageReply(language=self.language, country=self.country)
        assert schema is EntitiesReply
        chunk = user.split("DOCUMENT:", 1)[1]
        items = [
            {"text": text, "type": etype, "canonical": canonical}
            for text, (etype, canonical) in self.entities.items()
            if re.search(re.escape(text), chunk)
        ]
        return EntitiesReply.model_validate({"entities": items})


@pytest.fixture
def fake_llm():
    return FakeLLM(
        {
            "Mario Rossi": (EntityType.PERSON, "Mario Rossi"),
            "Rossi": (EntityType.PERSON, "Mario Rossi"),
            "M. Rossi": (EntityType.PERSON, "Mario Rossi"),
            "Giulia Bianchi": (EntityType.PERSON, "Giulia Bianchi"),
            "ACME S.p.A.": (EntityType.ORG, "ACME S.p.A."),
            "Via Roma 12, 20121 Milano": (EntityType.ADDRESS, "Via Roma 12, 20121 Milano"),
        },
        language="it",
        country="IT",
    )


@pytest.fixture
def settings(monkeypatch, tmp_path):
    # Isolate from any config file or environment of the developer running the tests.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for var in ("EONCLOAK_PROVIDER", "EONCLOAK_MODEL", "EONCLOAK_BASE_URL", "EONCLOAK_LOCAL_ONLY"):
        monkeypatch.delenv(var, raising=False)
    return load_settings(provider="ollama", model="fake")
