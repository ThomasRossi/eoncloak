"""eoncloak: anonymise documents with a cloud or local LLM, keeping pseudonyms consistent."""

from .anonymizer import (
    Anonymizer,
    Result,
    TextResult,
    load_mapping,
    restore_document,
    save_mapping,
)
from .config import ConfigError, Settings, load_settings
from .documents import load_document, save_document
from .entities import Entity, EntityType, Mention, Registry

__version__ = "0.1.0"

__all__ = [
    "Anonymizer",
    "ConfigError",
    "Entity",
    "EntityType",
    "Mention",
    "Registry",
    "Result",
    "Settings",
    "TextResult",
    "load_document",
    "load_mapping",
    "load_settings",
    "restore_document",
    "save_document",
    "save_mapping",
]
