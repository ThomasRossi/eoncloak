import pytest

from eoncloak.entities import EntityType, Mention, Registry, alias_variants


def test_aliases_link_through_canonical():
    reg = Registry()
    a = reg.add(Mention("Mario Rossi", EntityType.PERSON, "Mario Rossi"))
    b = reg.add(Mention("Rossi", EntityType.PERSON, "Mario Rossi"))
    c = reg.add(Mention("Giulia", EntityType.PERSON, "Giulia Bianchi"))
    assert a is b
    assert a.label == "[PERSON_1]"
    assert c.label == "[PERSON_2]"
    assert reg.replacements()["Rossi"] == "[PERSON_1]"


def test_same_surface_is_case_insensitive():
    reg = Registry()
    assert reg.add(Mention("ACME", EntityType.ORG)) is reg.add(Mention("Acme", EntityType.ORG))


def test_counters_are_per_type():
    reg = Registry()
    reg.add(Mention("Mario Rossi", EntityType.PERSON))
    org = reg.add(Mention("ACME", EntityType.ORG))
    assert org.label == "[ORG_1]"


def test_phone_numbers_normalised():
    reg = Registry(phone_region="IT")
    a = reg.add(Mention("+39 02 1234 5678", EntityType.PHONE))
    b = reg.add(Mention("02 12345678", EntityType.PHONE))
    assert a is b


def test_too_short_mentions_ignored():
    reg = Registry()
    assert reg.add(Mention("J.", EntityType.PERSON)) is None
    assert reg.entities == []


def test_custom_label_format():
    reg = Registry(label_format="<{type}-{n}>")
    assert reg.add(Mention("Mario Rossi", EntityType.PERSON)).label == "<PERSON-1>"


def test_mapping_round_trip_continues_numbering():
    reg = Registry()
    reg.add(Mention("Mario Rossi", EntityType.PERSON))
    reg.add(Mention("Rossi", EntityType.PERSON, "Mario Rossi"))
    reg.add(Mention("Giulia Bianchi", EntityType.PERSON))

    loaded = Registry.from_mapping(reg.to_mapping())
    assert loaded.add(Mention("rossi", EntityType.PERSON)).label == "[PERSON_1]"
    assert loaded.add(Mention("Luca Verdi", EntityType.PERSON)).label == "[PERSON_3]"


def test_mapping_version_checked():
    with pytest.raises(ValueError):
        Registry.from_mapping({"version": 99, "entities": []})


@pytest.mark.parametrize(
    ("surface", "variants"),
    [
        ("NORTHWIND TRADING LTD.", ["NORTHWIND TRADING LTD", "NORTHWIND TRADING"]),
        ("BLUE HERON LTD", ["BLUE HERON"]),
        ("ACME S.p.A.", ["ACME S.p.A", "ACME"]),
        ("Northwind Pty Ltd", ["Northwind"]),
        ("Ltd", []),
        ("Mario Rossi", []),
    ],
)
def test_org_variants(surface, variants):
    assert alias_variants(EntityType.ORG, surface) == variants


def test_variants_registered_but_never_steal():
    reg = Registry()
    person = reg.add(Mention("Heron", EntityType.PERSON))
    org = reg.add(Mention("BLUE HERON LTD", EntityType.ORG))
    reg.add(Mention("Blue Ltd", EntityType.ORG, "BLUE HERON LTD"))
    replacements = reg.replacements()
    assert replacements["BLUE HERON"] == org.label
    assert replacements["Heron"] == person.label
    # "Blue" alone has only four characters but is still a distinct company form.
    assert replacements["Blue"] == org.label
    assert alias_variants(EntityType.EMAIL, "a@b.co.") == []


def test_same_link_with_or_without_scheme():
    reg = Registry()
    a = reg.add(Mention("http://www.EXAMPLE-law.com", EntityType.URL))
    b = reg.add(Mention("https://www.example-law.com/", EntityType.URL))
    c = reg.add(Mention("https://www.example-law.com/contact", EntityType.URL))
    assert a is b
    assert c is not a
