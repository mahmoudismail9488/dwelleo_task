"""Behaviour of the rule extractor: what it reports, and — just as important — what it refuses to guess."""

import json
from pathlib import Path

import pytest

from app.extractors.rules import RuleExtractor
from app.models import RawMessage

FIXTURES = Path(__file__).parent / "fixtures" / "messages.jsonl"
SAMPLES = {
    m["id"]: RawMessage.model_validate(m)
    for m in (json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines() if line.strip())
}


def extract(text_or_id: str):
    msg = SAMPLES.get(text_or_id) or RawMessage(id="t", text=text_or_id)
    return RuleExtractor().extract(msg)


# --- Golden: the 10 sample messages ------------------------------------------------

@pytest.mark.parametrize(
    "mid, classification, n_listings",
    [("m-001", "listing", 1), ("m-002", "listing", 1), ("m-003", "listing", 1),
     ("m-004", "listing", 1), ("m-005", "buyer_request", 0), ("m-006", "project", 0),
     ("m-007", "listing", 1), ("m-008", "listing", 1), ("m-009", "irrelevant", 0),
     ("m-010", "listing", 3)],
)
def test_sample_classification_and_listing_count(mid, classification, n_listings):
    c = extract(mid)
    assert c.classification == classification
    assert len(c.listings) == n_listings


def test_m001_villa_with_land_and_built_up_area():
    (l,) = extract("m-001").listings
    assert (l.purpose, l.property_type, l.city, l.district) == ("sale", "villa", "Riyadh", "Al Malqa")
    assert (l.bedrooms, l.bathrooms) == (5, 6)
    assert (l.built_up_area_sqm, l.land_area_sqm) == (380, 450)
    assert l.price_text == "3.2M"
    assert l.contact_phone_text == "+966551234567"


def test_m002_arabic_listing_with_arabic_digits():
    (l,) = extract("m-002").listings
    assert (l.purpose, l.property_type, l.city, l.district) == ("sale", "apartment", "Riyadh", "Al Narjis")
    assert (l.bedrooms, l.bathrooms, l.built_up_area_sqm) == (3, 2, 165)
    assert l.price_text == "٩٥٠ ألف"
    assert l.contact_phone_text == "+966509876543"


def test_m003_repost_reads_same_property_facts_as_m001():
    (l,) = extract("m-003").listings
    assert (l.property_type, l.district, l.bedrooms, l.built_up_area_sqm) == ("villa", "Al Malqa", 5, 380)
    assert l.contact_phone_text == "+966551234567"


def test_m004_rent_yearly_but_no_city_and_no_phone_stated():
    (l,) = extract("m-004").listings
    assert (l.purpose, l.price_text, l.price_period) == ("rent", "85,000", "yearly")
    assert l.district == "Al Olaya"
    assert l.city is None               # not stated; ambiguous district → no inference here
    assert l.contact_phone_text is None  # sender fallback is the pipeline's job


def test_m006_project_keeps_ranges_and_starting_price_out_of_listings():
    p = extract("m-006").project
    assert p.name == "Al Waha Residences"
    assert p.city == "Jeddah"
    assert (p.min_bedrooms, p.max_bedrooms) == (1, 4)
    assert p.min_price_text == "780K"
    assert p.delivery_date == "Q4 2028"
    assert p.payment_terms == "10% down payment"


def test_m007_land_keeps_both_conflicting_price_figures():
    (l,) = extract("m-007").listings
    assert l.property_type == "land"
    assert l.price_text == "2.1M"
    assert l.price_per_sqm_text == "2,500"
    assert l.built_up_area_sqm == 900  # unlabeled area; pipeline maps it to land area for land


def test_m008_price_on_request_has_no_price():
    (l,) = extract("m-008").listings
    assert (l.purpose, l.property_type, l.city, l.bedrooms) == ("rent", "apartment", "Jeddah", 3)
    assert l.price_text is None


def test_m010_split_into_three_with_shared_phone_and_own_fields():
    ls = extract("m-010").listings
    assert [l.district for l in ls] == ["Al Yasmin", "Al Yasmin", "Hittin"]
    assert [l.price_text for l in ls] == ["35K", "55K", "80K"]
    assert {l.price_period for l in ls} == {"yearly"}
    assert {l.contact_phone_text for l in ls} == {"+966520202020"}
    assert ls[0].property_type == "studio"
    assert [l.bedrooms for l in ls] == [None, 2, 3]
    assert ls[1].property_type is None  # "2BR" alone doesn't state the type
    assert {l.purpose for l in ls} == {None}  # never stated; pipeline may infer from /yr


def test_arabic_comma_does_not_break_suffix_or_city():
    # Regression (D5): "ألف،" lost its suffix → price read as 950 SAR instead of 950,000.
    (l,) = extract("شقة للبيع في الرياض، السعر ٩٥٠ ألف، للتواصل ٠٥٥١٢٣٤٥٦٧").listings
    assert l.price_text == "٩٥٠ ألف"
    assert l.city == "Riyadh"


# --- Refuses to guess ------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    ["Villa for sale 3M or best offer 2.8M", "Villa for sale price 2.5 call 0551234567"],
)
def test_ambiguous_price_is_left_empty(text):
    assert extract(text).listings[0].price_text is None


def test_years_and_percentages_are_not_prices():
    (l,) = extract("Villa for sale built 2019, 5% discount, 2M SAR").listings
    assert l.price_text == "2M"


def test_two_cities_means_no_city():
    assert extract("Apartment for sale in Jeddah or Riyadh, 900K").listings[0].city is None


def test_sale_and_rent_together_means_no_purpose():
    assert extract("Villa for sale or rent in Al Malqa 3M").listings[0].purpose is None


# --- Classification precedence -----------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Looking for a villa for sale, budget 3M", "buyer_request"),
        ("مطلوب شقة للإيجار في النرجس", "buyer_request"),
        ("Villa for sale, off-plan, handover 2027", "project"),
        ("Thanks everyone, see you at 5", "irrelevant"),
        ("Apartment 120 sqm 3 BR 1,200,000 SAR", "listing"),
    ],
)
def test_classification_precedence(text, expected):
    assert extract(text).classification == expected


# --- Districts not in the gazetteer ------------------------------------------------

def test_unknown_district_captured_from_text():
    assert extract("For sale villa in Al Arid, 4 bedrooms, price 2,800,000").listings[0].district == "Al Arid"
    assert extract("شقة للإيجار في حي الربوة ٤٠ ألف سنويا").listings[0].district == "الربوة"


def test_city_is_not_captured_as_district():
    assert extract("Apartment for rent in Riyadh 60K/yr").listings[0].district is None
