import pytest

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


def test_links():
    text = (
        "See https://www.example.com/usr/acme_shop. Also (www.acme.it) and acme-shop.co.uk/about, "
        "but not S.p.A., report.pdf, v3.11 or mario@acme.it."
    )
    assert found(text, etype=EntityType.URL) == [
        "https://www.example.com/usr/acme_shop",
        "www.acme.it",
        "acme-shop.co.uk/about",
    ]


def test_link_wrapped_at_hyphen():
    # PDF text extraction breaks long links at hyphens; the rest of the link must go too.
    text = "the pages\nhttps://find.example.gov.uk/officers/Ab12cdE-\n_Fg34hiJ/appointments\nend"
    assert found(text, etype=EntityType.URL) == [
        "https://find.example.gov.uk/officers/Ab12cdE-\n_Fg34hiJ/appointments"
    ]
    assert found("a well-\nknown fact", etype=EntityType.URL) == []


@pytest.mark.parametrize(
    ("text", "ids"),
    [
        ("Company no.: 01234567", ["01234567"]),
        ("(Company number 07654321)", ["07654321"]),
        ("Vat no.: GB123456789", ["GB123456789"]),
        ("P.IVA 01234567890, C.F. RSSMRA80A01F205X", ["01234567890", "RSSMRA80A01F205X"]),
        ("USt-IdNr.: DE123456789, HRB 123456", ["DE123456789", "123456"]),
        ("SIRET 123 456 789 00012", ["123 456 789 00012"]),
        ("in no time 12345 people came; ein 12345 Euro; page no. 7", []),
    ],
)
def test_company_and_tax_ids(text, ids):
    assert sorted(set(found(text, etype=EntityType.ID))) == sorted(ids)
