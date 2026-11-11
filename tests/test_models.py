import pytest
from pydantic import ValidationError

from app.models import (
    ExtractionCandidate,
    Listing,
    MessageResult,
    Price,
    PriceCandidate,
    Project,
    RawMessage,
)


def _listing(**kw):
    return Listing(id="m-001-1", source="rules", **kw)


# --- Input ---

def test_raw_message_requires_id_and_text():
    with pytest.raises(ValidationError):
        RawMessage.model_validate({"text": "hi"})
    with pytest.raises(ValidationError):
        RawMessage.model_validate({"id": "m-1"})
    with pytest.raises(ValidationError):
        RawMessage.model_validate({"id": "", "text": "hi"})


def test_raw_message_ignores_unknown_fields_and_sender_is_optional():
    msg = RawMessage.model_validate({"id": "m-1", "text": "hi", "group": "x"})
    assert msg.sender is None


# --- Contract ---

def test_needs_review_is_derived_from_review_reasons():
    assert _listing().needs_review is False
    assert _listing(review_reasons=["price_missing"]).needs_review is True


def test_needs_review_cannot_be_set_by_hand():
    # Passing needs_review=False alongside a reason must not hide the reason.
    listing = Listing.model_validate(
        {"id": "x", "source": "rules", "review_reasons": ["price_missing"], "needs_review": False}
    )
    assert listing.needs_review is True


def test_listing_serializes_with_contract_fields():
    out = _listing(
        purpose="sale",
        property_type="villa",
        price=Price(amount=3_200_000, period="total"),
        contact_phone="+966551234567",
    ).model_dump(mode="json")
    for key in [
        "id", "purpose", "property_type", "city", "district", "bedrooms", "bathrooms",
        "area_sqm", "price", "contact_phone", "needs_review", "review_reasons", "duplicate_of",
    ]:
        assert key in out
    assert out["price"] == {"amount": 3_200_000, "currency": "SAR", "period": "total"}
    assert out["needs_review"] is False


def test_currency_is_always_sar():
    with pytest.raises(ValidationError):
        Price(amount=1, currency="USD")


@pytest.mark.parametrize("bad", ["0551234567", "+966114567890", "+971501234567", "+96655123456"])
def test_contact_phone_must_be_normalized_saudi_mobile(bad):
    with pytest.raises(ValidationError):
        _listing(contact_phone=bad)


def test_invalid_enum_values_are_rejected_in_contract():
    with pytest.raises(ValidationError):
        _listing(property_type="castle")
    with pytest.raises(ValidationError):
        _listing(purpose="lease")


def test_price_candidates_keep_every_conflicting_value():
    listing = _listing(
        price=Price(amount=None, period="total"),
        price_per_sqm=2_500,
        price_candidates=[
            PriceCandidate(amount=2_100_000, basis="stated_total"),
            PriceCandidate(amount=2_250_000, basis="computed_from_per_sqm"),
        ],
        review_reasons=["price_contradiction"],
    )
    out = listing.model_dump(mode="json")
    assert out["price"]["amount"] is None
    assert [c["amount"] for c in out["price_candidates"]] == [2_100_000, 2_250_000]


# --- Result shape vs classification ---

@pytest.mark.parametrize("classification", ["buyer_request", "irrelevant", "project"])
def test_non_listing_messages_cannot_carry_listings(classification):
    with pytest.raises(ValidationError):
        MessageResult(message_id="m-1", classification=classification, listings=[_listing()])


def test_project_message_carries_project_and_no_listings():
    result = MessageResult(
        message_id="m-006",
        classification="project",
        project=Project(name="Al Waha Residences", city="Jeddah", min_price=780_000,
                        min_bedrooms=1, max_bedrooms=4, delivery_date="Q4 2028",
                        payment_terms="10% down payment"),
    )
    out = result.model_dump(mode="json")
    assert out["listings"] == []
    assert out["project"]["min_price"] == 780_000


def test_only_project_messages_may_carry_a_project():
    with pytest.raises(ValidationError):
        MessageResult(message_id="m-1", classification="listing", project=Project(name="x"))


def test_unknown_classification_is_rejected():
    with pytest.raises(ValidationError):
        MessageResult(message_id="m-1", classification="spam")


# --- Candidates (untrusted extractor output) ---

def test_candidate_keeps_unknown_enum_strings_for_per_field_flagging():
    cand = ExtractionCandidate.model_validate(
        {"classification": "listing", "listings": [{"property_type": "castle", "price_text": "3.2M"}]}
    )
    assert cand.listings[0].property_type == "castle"
    assert cand.listings[0].price_text == "3.2M"


def test_candidate_with_wrong_types_is_rejected_as_malformed():
    with pytest.raises(ValidationError):
        ExtractionCandidate.model_validate(
            {"classification": "listing", "listings": [{"bedrooms": "five"}]}
        )
    with pytest.raises(ValidationError):
        ExtractionCandidate.model_validate({"listings": []})  # classification missing
