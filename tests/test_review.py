"""Field policies (PLAN.md §5) and review reasons, tested through build_listing."""

import pytest

from app.models import ListingCandidate, PriceMention, ProjectCandidate
from app.review import REVIEW_REASONS, build_listing, build_project

def P(amount, quote="x"):
    # build_listing receives grounded mentions: amount already resolved.
    return PriceMention(quote=quote, amount=amount)


COMPLETE = dict(purpose="sale", property_type="villa", city="Riyadh", district="Al Malqa",
                bedrooms=5, built_up_area_sqm=380, prices=[P(3_200_000)], contact_phone_text="0551234567")


def build(sender="+966541112222", extra=None, **fields):
    return build_listing(ListingCandidate(**fields), "m-1-1", "rules", sender, extra)


def test_complete_listing_needs_no_review():
    l = build(**COMPLETE)
    assert l.review_reasons == [] and l.needs_review is False
    assert l.price.amount == 3_200_000 and l.price.period == "total"
    assert l.contact_phone == "+966551234567"


def test_every_reason_produced_is_in_the_fixed_vocabulary():
    l = build(sender=None)
    assert l.review_reasons and set(l.review_reasons) <= REVIEW_REASONS


def test_grounding_reasons_are_carried_through():
    l = build(extra=["price_unverified"], **COMPLETE)
    assert l.review_reasons == ["price_unverified"]


# --- price (D2) ---

def test_m007_contradiction_keeps_both_candidates_and_no_amount():
    l = build(purpose="sale", property_type="land", city="Jeddah", district="Obhur",
              built_up_area_sqm=900, prices=[P(2_100_000)], price_per_sqm=P(2_500),
              price_period="total", contact_phone_text="0555556666")
    assert l.price.amount is None
    assert l.price_per_sqm == 2_500
    assert [(c.amount, c.basis) for c in l.price_candidates] == [
        (2_100_000, "stated_total"), (2_250_000, "computed_from_per_sqm")]
    assert l.review_reasons == ["price_contradiction"]


def test_total_exactly_matching_per_sqm_is_accepted():
    l = build(**{**COMPLETE, "property_type": "land", "built_up_area_sqm": 900,
                 "prices": [P(2_250_000)], "price_per_sqm": P(2_500)})
    assert l.price.amount == 2_250_000 and "price_contradiction" not in l.review_reasons


def test_rounded_total_is_still_a_contradiction():
    # No tolerance (D2): 875 × 2,500 = 2,187,500 vs stated 2.2M.
    l = build(**{**COMPLETE, "property_type": "land", "built_up_area_sqm": 875,
                 "prices": [P(2_200_000)], "price_per_sqm": P(2_500)})
    assert l.price.amount is None and "price_contradiction" in l.review_reasons


def test_two_stated_prices_are_a_contradiction():
    l = build(**{**COMPLETE, "prices": [P(3_200_000), P(2_900_000)]})
    assert l.price.amount is None
    assert [c.amount for c in l.price_candidates] == [3_200_000, 2_900_000]
    assert "price_contradiction" in l.review_reasons


def test_per_sqm_only_is_computed_and_flagged():
    l = build(**{**COMPLETE, "property_type": "land", "built_up_area_sqm": 900,
                 "prices": [], "price_per_sqm": P(2_500)})
    assert l.price.amount == 2_250_000
    assert "price.amount" in l.inferred_fields and "price_computed" in l.review_reasons


def test_missing_price_is_null_and_flagged():
    l = build(**{**COMPLETE, "prices": []})
    assert l.price.amount is None and "price_missing" in l.review_reasons


def test_rent_without_period_is_flagged():
    l = build(**{**COMPLETE, "purpose": "rent", "prices": [P(85_000)]})
    assert l.price.period is None and "price_period_missing" in l.review_reasons


# --- purpose ---

def test_yearly_price_infers_rent_with_flag():
    l = build(**{**COMPLETE, "purpose": None, "prices": [P(35_000)], "price_period": "yearly"})
    assert l.purpose == "rent"
    assert "purpose" in l.inferred_fields and "purpose_inferred" in l.review_reasons


def test_no_purpose_and_no_period_stays_missing():
    l = build(**{**COMPLETE, "purpose": None})
    assert l.purpose is None and "purpose_missing" in l.review_reasons


# --- location ---

def test_unambiguous_district_infers_city_with_flag():
    l = build(**{**COMPLETE, "city": None, "district": "Hittin"})
    assert l.city == "Riyadh"
    assert "city" in l.inferred_fields and "city_inferred" in l.review_reasons


def test_ambiguous_district_never_infers_city():
    l = build(**{**COMPLETE, "city": None, "district": "Al Olaya"})
    assert l.city is None and "city_missing" in l.review_reasons and "city" not in l.inferred_fields


def test_names_are_canonical_english_for_known_places_and_raw_otherwise():
    assert build(**{**COMPLETE, "city": "الرياض", "district": "النرجس"}).district == "Al Narjis"
    assert build(**{**COMPLETE, "city": "الرياض", "district": "النرجس"}).city == "Riyadh"
    l = build(**{**COMPLETE, "district": "الربوة"})
    assert l.district == "الربوة" and l.city == "Riyadh"


def test_unknown_district_without_city_is_city_missing():
    l = build(**{**COMPLETE, "city": None, "district": "Al Arid"})
    assert l.city is None and "city_missing" in l.review_reasons


# --- rooms & area ---

def test_studio_without_bedroom_count_is_zero_without_flag():
    l = build(**{**COMPLETE, "property_type": "studio", "bedrooms": None})
    assert l.bedrooms == 0 and "bedrooms" in l.inferred_fields
    assert "bedrooms_missing" not in l.review_reasons


def test_studio_with_stated_bedroom_keeps_it():
    assert build(**{**COMPLETE, "property_type": "studio", "bedrooms": 1}).bedrooms == 1


def test_land_does_not_need_bedrooms_and_uses_plot_area():
    l = build(**{**COMPLETE, "property_type": "land", "bedrooms": None, "built_up_area_sqm": None,
                 "land_area_sqm": 600})
    assert l.area_sqm == 600 and l.land_area_sqm == 600
    assert "bedrooms_missing" not in l.review_reasons


def test_villa_area_is_built_up_and_land_area_kept_separately():
    l = build(**{**COMPLETE, "land_area_sqm": 450})
    assert (l.area_sqm, l.land_area_sqm) == (380, 450)


def test_missing_bedrooms_and_area_are_flagged():
    l = build(**{**COMPLETE, "bedrooms": None, "built_up_area_sqm": None})
    assert {"bedrooms_missing", "area_missing"} <= set(l.review_reasons)


# --- contact ---

def test_no_phone_in_text_falls_back_to_sender_with_flag():
    l = build(**{**COMPLETE, "contact_phone_text": None})
    assert l.contact_phone == "+966541112222"
    assert "contact_phone" in l.inferred_fields and "contact_phone_from_sender" in l.review_reasons


def test_no_phone_and_invalid_sender_is_missing():
    l = build(sender="+971501234567", **{**COMPLETE, "contact_phone_text": None})
    assert l.contact_phone is None and "contact_phone_missing" in l.review_reasons


def test_landline_in_text_is_invalid_not_replaced_by_sender():
    l = build(**{**COMPLETE, "contact_phone_text": "0114567890"})
    assert l.contact_phone is None and l.review_reasons == ["contact_phone_invalid"]


# --- project ---

def test_interpreted_price_is_marked_inferred():
    l = build(extra=["price_interpreted"], **COMPLETE)
    assert "price.amount" in l.inferred_fields and l.review_reasons == ["price_interpreted"]


def test_build_project_normalizes_price_and_names():
    p = build_project(ProjectCandidate(name="Al Waha Residences", city="جدة", min_price=P(780_000),
                                       min_bedrooms=1, max_bedrooms=4))
    assert (p.city, p.min_price, p.min_bedrooms, p.max_bedrooms) == ("Jeddah", 780_000, 1, 4)
    assert p.review_reasons == [] and p.needs_review is False


def test_project_missing_core_fields_is_flagged():
    p = build_project(ProjectCandidate(), ["delivery_date_unverified"])
    assert p.review_reasons == ["delivery_date_unverified", "project_name_missing",
                                "city_missing", "price_missing"]
    assert p.needs_review is True
