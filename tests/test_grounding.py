"""Grounding: invented or malformed values (typical LLM failures) are removed and flagged."""

import pytest

from app.grounding import clean_amount_text, ground, ground_listing, ground_project, resolve_price
from app.models import ExtractionCandidate, ListingCandidate, PriceMention, ProjectCandidate

M001 = ("For Sale – Villa in Al Malqa, Riyadh. 5 BR, 6 baths, 450 sqm land / 380 sqm BUA. "
        "Price 3.2M SAR negotiable. Call Ahmed 0551234567")
M002 = "للبيع شقة في حي النرجس، الرياض. ٣ غرف نوم، ٢ دورة مياه، المساحة ١٦٥ م². السعر ٩٥٠ ألف ريال. للتواصل: ٠٥٠٩٨٧٦٥٤٣"
SENDER = "+966551234567"


def g(text=M001, sender=SENDER, **fields):
    return ground_listing(ListingCandidate(**fields), text, sender)


def P(quote, amount=None):
    return PriceMention(quote=quote, amount=amount)


def test_values_present_in_the_text_pass_untouched():
    c = ListingCandidate(purpose="sale", property_type="villa", city="Riyadh", district="Al Malqa",
                         bedrooms=5, bathrooms=6, built_up_area_sqm=380, land_area_sqm=450,
                         prices=[P("3.2M")], contact_phone_text="0551234567")
    grounded, reasons = ground_listing(c, M001, SENDER)
    assert reasons == []
    assert grounded.prices == [P("3.2M", 3_200_000)]  # amount filled from the quote
    assert grounded.model_dump(exclude={"prices"}) == c.model_dump(exclude={"prices"})


def test_arabic_text_values_are_grounded_after_digit_folding():
    c, reasons = g(M002, "+966509876543", city="الرياض", district="Al Narjis", bedrooms=3,
                   bathrooms=2, built_up_area_sqm=165, prices=[P("950 ألف")],
                   contact_phone_text="+966509876543")
    assert reasons == []
    assert c.prices[0].amount == 950_000


# --- cited prices (D6) -----------------------------------------------------------

def test_cited_number_matching_the_quote_is_accepted():
    resolved, reasons = resolve_price(P("3.2M", 3_200_000), M001)
    assert resolved.amount == 3_200_000 and reasons == []


def test_quote_not_in_message_is_dropped():
    resolved, reasons = resolve_price(P("3.5M", 3_500_000), M001)
    assert resolved is None and reasons == ["price_unverified"]


def test_llm_number_disagreeing_with_its_quote_is_replaced_by_our_parse():
    resolved, reasons = resolve_price(P("3.2M", 2_300_000), M001)
    assert resolved.amount == 3_200_000
    assert reasons == ["price_llm_mismatch"]


def test_quote_we_cannot_parse_keeps_llm_amount_with_flag():
    text = "فيلا للبيع في الملقا بسعر ثلاثة ملايين ريال"
    resolved, reasons = resolve_price(P("ثلاثة ملايين", 3_000_000), text)
    assert resolved.amount == 3_000_000 and reasons == ["price_interpreted"]


def test_quote_we_cannot_parse_without_amount_is_dropped():
    resolved, reasons = resolve_price(P("negotiable"), M001)
    assert resolved is None and reasons == ["price_unverified"]


def test_only_the_invented_price_is_removed_when_mixed_with_a_real_one():
    c, reasons = g(prices=[P("3.2M"), P("2.9M")])
    assert [p.quote for p in c.prices] == ["3.2M"]
    assert reasons == ["price_unverified"]


def test_interpreted_price_flows_into_listing_reasons():
    c, reasons = g("فيلا للبيع بسعر ثلاثة ملايين", None, prices=[P("ثلاثة ملايين", 3_000_000)])
    assert c.prices[0].amount == 3_000_000 and reasons == ["price_interpreted"]


# --- other invented values --------------------------------------------------------

def test_invented_numbers_are_removed():
    c, reasons = g(bedrooms=7, bathrooms=9, built_up_area_sqm=500)
    assert (c.bedrooms, c.bathrooms, c.built_up_area_sqm) == (None, None, None)
    assert reasons == ["bedrooms_unverified", "bathrooms_unverified", "area_unverified"]


def test_city_from_world_knowledge_is_removed():
    # "Al Olaya" → Riyadh is the kind of inference the LLM must not make on its own.
    c, reasons = g("Apartment for rent in Al Olaya, 2BR furnished, 85,000/yr", None, city="Riyadh")
    assert c.city is None
    assert reasons == ["city_unverified"]


def test_invented_district_is_removed():
    c, reasons = g(district="Al Narjis")
    assert c.district is None and reasons == ["district_unverified"]


def test_tiny_district_name_cannot_match_inside_other_words():
    c, _ = g(district="a")
    assert c.district is None


def test_invented_phone_is_removed():
    c, reasons = g(contact_phone_text="0559999999")
    assert c.contact_phone_text is None and reasons == ["contact_phone_unverified"]


def test_sender_number_does_not_count_as_stated_in_the_text():
    # Regression (D9): the sender is an inference, only review.py may use it (with a flag).
    c, reasons = g("Villa for sale", SENDER, contact_phone_text="0551234567")
    assert c.contact_phone_text is None and reasons == ["contact_phone_unverified"]


@pytest.mark.parametrize("field, value", [("purpose", "lease"), ("property_type", "castle"),
                                          ("price_period", "weekly")])
def test_invalid_enum_values_are_removed(field, value):
    c, reasons = g(**{field: value})
    assert getattr(c, field) is None
    assert reasons == [f"{field}_unverified"]


def test_arabic_bedroom_words_count_as_stated():
    _, reasons = g("شقة للبيع غرفتين وصالة", None, bedrooms=2)
    assert reasons == []


@pytest.mark.parametrize("raw, expected", [("3.2M SAR", "3.2M"), ("85,000/yr", "85,000"),
                                           ("950 ألف ريال", "950 ألف"), ("SAR 780K", "780K")])
def test_clean_amount_text(raw, expected):
    assert clean_amount_text(raw) == expected


# --- projects -----------------------------------------------------------------------

def test_project_fields_not_in_text_are_dropped_and_flagged():
    text = "New launch: Al Waha Residences, Jeddah. Off-plan, 1–4 BR units from SAR 780K. Handover Q4 2028"
    p, reasons = ground_project(ProjectCandidate(
        name="Al Waha Residences", city="Jeddah", min_price=P("650K"), min_bedrooms=1,
        max_bedrooms=5, delivery_date="Q2 2027", payment_terms="20% down payment"), text)
    assert (p.name, p.city, p.min_bedrooms) == ("Al Waha Residences", "Jeddah", 1)
    assert (p.min_price, p.max_bedrooms, p.delivery_date, p.payment_terms) == (None, None, None, None)
    assert reasons == ["price_unverified", "bedrooms_unverified", "delivery_date_unverified",
                       "payment_terms_unverified"]


def test_ground_returns_reasons_per_listing_and_for_project():
    cand = ExtractionCandidate(classification="listing",
                               listings=[ListingCandidate(bedrooms=5), ListingCandidate(bedrooms=8)])
    grounded, reasons, project_reasons = ground(cand, M001, SENDER)
    assert reasons == [[], ["bedrooms_unverified"]]
    assert project_reasons == []
    assert grounded.listings[1].bedrooms is None


# --- enum values must be stated, not just valid (D13) --------------------------------

M010 = "Available now: 1) Studio Al Yasmin 35K/yr 2) 2BR Al Yasmin 55K/yr 3) 3BR Hittin 80K/yr. Call 0520202020"


def test_purpose_inferred_by_the_llm_is_rejected():
    # Regression (D13): m-010 never says "rent"; the LLM inferred it from "/yr".
    c, reasons = g(M010, None, purpose="rent")
    assert c.purpose is None and reasons == ["purpose_unverified"]


@pytest.mark.parametrize("text, purpose", [
    ("Apartment for rent in Al Olaya", "rent"),
    ("شقة للإيجار في الروضة", "rent"),
    ("للبيع شقة في حي النرجس", "sale"),
    ("Villa for sale in Malqa", "sale"),
])
def test_stated_purpose_passes(text, purpose):
    c, reasons = g(text, None, purpose=purpose)
    assert c.purpose == purpose and reasons == []


def test_property_type_must_be_stated():
    c, reasons = g("Apartment for rent in Al Olaya", None, property_type="villa")
    assert c.property_type is None and reasons == ["property_type_unverified"]
    c, reasons = g("شقة للإيجار", None, property_type="apartment")
    assert c.property_type == "apartment" and reasons == []


def test_type_guessed_from_bedrooms_is_rejected():
    # "2BR" alone doesn't say apartment.
    c, _ = g(M010, None, property_type="apartment")
    assert c.property_type is None


@pytest.mark.parametrize("text, period, ok", [
    ("Apartment 85,000/yr", "yearly", True),
    ("Apartment 85,000, 2 payments", "yearly", True),
    ("شقة ٤٠ ألف سنويا", "yearly", True),
    ("Apartment 4,500 monthly", "monthly", True),
    ("Land 900 sqm (total 2.1M)", "total", True),
    ("Apartment for rent 85,000", "yearly", False),
    ("Villa for sale 3.2M", "total", False),
])
def test_price_period_must_be_stated(text, period, ok):
    c, reasons = g(text, None, price_period=period)
    assert (c.price_period == period) is ok
    assert (reasons == []) is ok
