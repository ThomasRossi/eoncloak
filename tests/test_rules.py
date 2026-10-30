from eoncloak.entities import ALL_TYPES, EntityType
from eoncloak.rules import detect_rules, is_valid_iban


def found(text, region=None, etype=None):
    return [m.text for m in detect_rules(text, ALL_TYPES, region) if etype in (None, m.type)]


def test_emails():
    text = "Write to mario.rossi+work@acme.co.uk or info@münchen.de."
    assert found(text, etype=EntityType.EMAIL) == ["mario.rossi+work@acme.co.uk", "info@münchen.de"]


def test_international_phone_without_region():
    assert found("Call +44 20 7946 0958 now", etype=EntityType.PHONE) == ["+44 20 7946 0958"]


def test_local_phone_needs_region():
    text = "Tel. 02 1234 5678"
    assert found(text, etype=EntityType.PHONE) == []
    assert found(text, region="IT", etype=EntityType.PHONE) == ["02 1234 5678"]


def test_iban_validated():
    assert is_valid_iban("GB82 WEST 1234 5698 7654 32")
    assert not is_valid_iban("GB82 WEST 1234 5698 7654 33")
    assert found("IBAN: GB82 WEST 1234 5698 7654 32.", etype=EntityType.IBAN) == [
        "GB82 WEST 1234 5698 7654 32"
    ]


def test_only_requested_types():
    assert detect_rules("a@b.com +44 20 7946 0958", [EntityType.EMAIL]) != []
    assert detect_rules("+44 20 7946 0958", [EntityType.EMAIL]) == []
