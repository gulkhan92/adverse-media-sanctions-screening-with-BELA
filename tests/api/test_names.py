from __future__ import annotations

from atlas.names import fold, normalize_name, script_of, transliterate


def test_fold_removes_case_accents_and_punctuation() -> None:
    assert fold("  José  O'Brien-Smith, Jr. ") == "jose o brien smith jr"
    assert fold("ＡＢＣ Ｃｏ") == "abc co"  # NFKC full-width forms


def test_company_suffixes_stripped_only_for_organizations() -> None:
    org = normalize_name("Acme Trading Co., Ltd.", "organization")
    assert org.stripped == "acme trading"
    person = normalize_name("Ltd Co", "person")
    assert person.stripped == "ltd co"
    # A name made only of suffixes is kept rather than emptied.
    assert normalize_name("Co Ltd", "organization").stripped == "co ltd"


def test_token_sorted_key_matches_reordered_names() -> None:
    a = normalize_name("Hassan Mohammad Ali", "person")
    b = normalize_name("Ali, Mohammad Hassan", "person")
    assert a.key == b.key


def test_transliteration_links_scripts() -> None:
    cyr = normalize_name("Владимир Путин", "person")
    lat = normalize_name("Vladimir Putin", "person")
    assert cyr.key == lat.key
    assert transliterate("Москва") == "Moskva"


def test_script_detection() -> None:
    assert script_of("Vladimir") == "LATIN"
    assert script_of("Владимир") == "CYRILLIC"
    assert script_of("فلاديمير") == "ARABIC"
    assert script_of("1234") == "NONE"
