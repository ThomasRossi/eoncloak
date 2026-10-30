from eoncloak.replace import Match, Replacer, rewrite_pieces


def test_whole_words_only():
    r = Replacer({"Rossi": "[PERSON_1]"})
    assert r.sub("Rossi met Rossini.") == "[PERSON_1] met Rossini."


def test_case_insensitive_for_longer_aliases():
    r = Replacer({"Rossi": "[PERSON_1]"})
    assert r.sub("ROSSI and rossi") == "[PERSON_1] and [PERSON_1]"


def test_short_aliases_are_case_sensitive():
    r = Replacer({"Al": "[PERSON_1]"})
    assert r.sub("Al went al mare") == "[PERSON_1] went al mare"


def test_flexible_whitespace():
    r = Replacer({"Mario Rossi": "[PERSON_1]"})
    assert r.sub("Mario\n  Rossi signed") == "[PERSON_1] signed"


def test_longest_form_wins():
    r = Replacer({"Rossi": "[PERSON_1]", "Via Rossi 3": "[ADDRESS_1]"})
    assert r.sub("Lives at Via Rossi 3; Rossi") == "Lives at [ADDRESS_1]; [PERSON_1]"


def test_email_is_not_split_by_contained_name():
    r = Replacer({"Rossi": "[PERSON_1]", "mario.rossi@acme.it": "[EMAIL_1]"})
    assert r.sub("mario.rossi@acme.it") == "[EMAIL_1]"


def test_unicode_and_unspaced_scripts():
    r = Replacer({"Žofie Dvořáková": "[PERSON_1]", "山田太郎": "[PERSON_2]"})
    assert r.sub("Paní Žofie Dvořáková.") == "Paní [PERSON_1]."
    assert r.sub("山田太郎さんは東京にいます") == "[PERSON_2]さんは東京にいます"


def test_possessive_keeps_suffix():
    r = Replacer({"Rossi": "[PERSON_1]"})
    assert r.sub("Rossi's car") == "[PERSON_1]'s car"


def test_empty_replacer():
    r = Replacer({})
    assert not r
    assert r.sub("anything") == "anything"


def test_rewrite_pieces_across_runs():
    pieces = ["Signed by Ma", "rio Ro", "ssi, today"]
    full = "".join(pieces)
    start = full.index("Mario")
    matches = [Match(start, start + len("Mario Rossi"), "[PERSON_1]")]
    assert rewrite_pieces(pieces, matches) == ["Signed by [PERSON_1]", "", ", today"]


def test_rewrite_pieces_multiple_matches_in_one_piece():
    r = Replacer({"Rossi": "[P1]", "ACME": "[O1]"})
    pieces = ["Rossi of ACME", " and Rossi"]
    out = rewrite_pieces(pieces, r.find("".join(pieces)))
    assert out == ["[P1] of [O1]", " and [P1]"]
