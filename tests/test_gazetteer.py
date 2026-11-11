import pytest

from app.gazetteer import CITIES, DISTRICTS, find_places, fold, lookup_district


def _names(text, places):
    return [m.place.name for m in find_places(text, places)]


def test_fold_keeps_length_so_indices_map_back():
    text = "للإيجار – ٣ غرف، جدة"
    assert len(fold(text)) == len(text)
    assert fold("للإيجار") == "للايجار"
    assert fold("جدة") == "جده"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Villa in Al Malqa, Riyadh", ["Al Malqa"]),
        ("Malqa district", ["Al Malqa"]),
        ("al-malqa", ["Al Malqa"]),
        ("حي النرجس، الرياض", ["Al Narjis"]),
        ("Studio Al Yasmin 35K/yr", ["Al Yasmin"]),
        ("3BR Hittin 80K/yr", ["Hittin"]),
        ("Land in Obhur", ["Obhur"]),
    ],
)
def test_districts_match_english_and_arabic_spellings(text, expected):
    assert _names(text, DISTRICTS) == expected


@pytest.mark.parametrize(
    "text, expected",
    [("Riyadh", ["Riyadh"]), ("الرياض", ["Riyadh"]), ("بالرياض", ["Riyadh"]),
     ("Jeddah", ["Jeddah"]), ("جدة", ["Jeddah"])],
)
def test_cities_match_with_article_and_clitics(text, expected):
    assert _names(text, CITIES) == expected


def test_arabic_punctuation_is_a_word_boundary():
    assert _names("حي النرجس، الرياض", DISTRICTS) == ["Al Narjis"]
    assert _names("في الرياض، حي", CITIES) == ["Riyadh"]
    assert _names("جدة؟", CITIES) == ["Jeddah"]


def test_no_partial_word_matches():
    assert _names("Hamrabad", DISTRICTS) == []
    assert _names("Riyadhi style", CITIES) == []


def test_ambiguous_districts_are_marked():
    assert lookup_district("Al Olaya").is_ambiguous          # Riyadh and Khobar
    assert lookup_district("Al Rawdah").is_ambiguous         # Riyadh and Jeddah
    assert not lookup_district("Al Malqa").is_ambiguous
    assert lookup_district("Al Malqa").cities == ("Riyadh",)


def test_unknown_district_lookup_returns_none():
    assert lookup_district("Al Arid") is None
