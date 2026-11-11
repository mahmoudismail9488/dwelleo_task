import pytest

from app.textnorm import find_saudi_mobiles, normalize_saudi_mobile, parse_amount, to_ascii_digits


def test_arabic_indic_and_persian_digits_become_ascii():
    assert to_ascii_digits("٣ غرف، ١٦٥ م²") == "3 غرف، 165 م²"
    assert to_ascii_digits("۰۵۵۱۲۳") == "055123"
    assert to_ascii_digits("٣٫٥") == "3.5"
    assert to_ascii_digits("٣٬٢٠٠٬٠٠٠") == "3,200,000"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("3.2M", 3_200_000),
        ("3.2m", 3_200_000),
        ("3.2 million", 3_200_000),
        ("950 ألف", 950_000),
        ("٩٥٠ ألف", 950_000),
        ("950 الف", 950_000),
        ("1.5 مليون", 1_500_000),
        ("3,200,000", 3_200_000),
        ("85,000", 85_000),
        ("35K", 35_000),
        ("780k", 780_000),
        ("2,500", 2_500),
        ("2.1M", 2_100_000),
        ("950000", 950_000),
    ],
)
def test_parse_amount_valid(raw, expected):
    result = parse_amount(raw)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2.5M", 2_500_000),
        ("2.500M", 2_500_000),
        ("1.25 مليون", 1_250_000),
        ("3.5K", 3_500),
        ("1,250.5K", 1_250_500),
    ],
)
def test_dot_is_decimal_when_suffix_present(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2.500", 2_500),
        ("85.000", 85_000),
        ("1.200.000", 1_200_000),
        ("٢٫٥٠٠", 2_500),
        ("3,200,000.50", 3_200_000.5),
    ],
)
def test_dot_is_thousands_separator_without_suffix(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["2.5", "2.75", "0.5", "2.50.000", "1.2.3", "2.5000"])
def test_ambiguous_bare_dot_returns_none(raw):
    # Probably a missing "M" (2.5 → 2.5M?) — never guessed.
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "SAR 3.2M", "3.2M SAR", "85,000/yr", "3,20,000", "1,2", "1.200.000M", "عند التواصل", None],
)
def test_parse_amount_rejects_anything_not_a_bare_amount(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "0551234567",
        "551234567",
        "966551234567",
        "+966551234567",
        "00966551234567",
        "+966 55 123 4567",
        "055-123-4567",
        "(055) 123 4567",
        "٠٥٥١٢٣٤٥٦٧",
    ],
)
def test_saudi_mobile_variants_normalize_to_e164(raw):
    assert normalize_saudi_mobile(raw) == "+966551234567"


@pytest.mark.parametrize(
    "raw",
    [
        "0114567890",      # Riyadh landline
        "+971501234567",   # UAE mobile
        "05512345",        # too short
        "055123456789",    # too long
        "",
        None,
    ],
)
def test_non_saudi_mobile_numbers_are_rejected(raw):
    assert normalize_saudi_mobile(raw) is None


def test_find_saudi_mobiles_in_free_text():
    assert find_saudi_mobiles("Call Ahmed 0551234567") == ["+966551234567"]
    assert find_saudi_mobiles("WhatsApp +966 55 123 4567") == ["+966551234567"]
    assert find_saudi_mobiles("للتواصل: ٠٥٠٩٨٧٦٥٤٣") == ["+966509876543"]


def test_find_saudi_mobiles_ignores_prices_and_areas_and_dedupes():
    text = "Price 3,200,000 SAR, 450 sqm. Call 0551234567 or +966551234567"
    assert find_saudi_mobiles(text) == ["+966551234567"]


@pytest.mark.parametrize(
    "text",
    [
        "Call 0551234567 5 BR villa",
        "0551234567 2) 3BR Hittin",
        "call 055 123 4567 now",
        "(055) 123 4567",
        "966551234567 for details",
    ],
)
def test_find_saudi_mobiles_does_not_swallow_neighbouring_numbers(text):
    # Regression: the first version merged "0551234567 5" into one 11-digit candidate and lost the phone.
    assert find_saudi_mobiles(text) == ["+966551234567"]


def test_find_saudi_mobiles_does_not_match_inside_longer_numbers():
    assert find_saudi_mobiles("ref 120551234567890") == []


def test_find_saudi_mobiles_returns_empty_when_none():
    assert find_saudi_mobiles("Apartment for rent in Al Olaya, 2BR furnished, 85,000/yr") == []
