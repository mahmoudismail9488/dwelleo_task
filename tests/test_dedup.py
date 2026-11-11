import json
from pathlib import Path

from app.dedup import apply_dedup, find_duplicate
from app.extractors.rules import RuleExtractor
from app.grounding import ground
from app.models import Listing, Price, RawMessage
from app.review import build_listing

FIXTURES = Path(__file__).parent / "fixtures" / "messages.jsonl"


def L(id, **kw):
    base = dict(purpose="sale", property_type="villa", district="Al Malqa", bedrooms=5,
                area_sqm=380, price=Price(amount=3_200_000, period="total"),
                contact_phone="+966551234567", source="rules")
    base.update(kw)
    return Listing(id=id, **base)


# --- strong match: same broker phone ---

def test_same_phone_type_district_and_price_is_a_duplicate():
    assert find_duplicate(L("new"), [L("old")]) == ("old", False)


def test_same_phone_and_exact_area_but_different_price_is_a_duplicate():
    # A repost with a price change but the same property facts.
    assert find_duplicate(L("new", price=Price(amount=3_000_000)), [L("old")]) == ("old", False)


def test_rounded_price_and_different_area_is_not_a_duplicate():
    # Exact only (D8): 3,150,000 ≠ 3,200,000 and area unknown on one side.
    new = L("new", price=Price(amount=3_150_000), area_sqm=None)
    assert find_duplicate(new, [L("old")]) is None


def test_missing_fields_never_match():
    assert find_duplicate(L("new", district=None), [L("old", district=None)]) is None
    assert find_duplicate(L("new", contact_phone=None, purpose=None), [L("old")]) is None


# --- weak match: different broker ---

def test_different_broker_same_facts_is_a_possible_duplicate():
    new = L("new", contact_phone="+966533334444")
    assert find_duplicate(new, [L("old")]) == ("old", True)
    flagged = apply_dedup(new, [L("old")])
    assert flagged.duplicate_of == "old"
    assert flagged.review_reasons == ["possible_duplicate"] and flagged.needs_review


def test_weak_match_needs_exact_price():
    new = L("new", contact_phone="+966533334444", price=Price(amount=3_100_000))
    assert find_duplicate(new, [L("old")]) is None


# --- conflicts block ---

def test_same_broker_two_villas_with_different_bedrooms_are_not_duplicates():
    assert find_duplicate(L("new", bedrooms=6), [L("old")]) is None


def test_conflicting_purpose_or_area_blocks():
    assert find_duplicate(L("new", purpose="rent"), [L("old")]) is None
    assert find_duplicate(L("new", area_sqm=400), [L("old")]) is None


def test_field_missing_on_one_side_does_not_block():
    assert find_duplicate(L("new", bedrooms=None, bathrooms=6), [L("old")]) == ("old", False)


# --- canonical id ---

def test_duplicate_of_points_to_the_earliest_original():
    first = L("first")
    second = L("second", duplicate_of="first")
    assert find_duplicate(L("third"), [first, second]) == ("first", False)
    assert find_duplicate(L("third"), [second]) == ("first", False)


def test_no_match_leaves_listing_untouched():
    new = L("new", district="Hittin")
    assert apply_dedup(new, [L("old")]) == new


# --- samples through the real chain ---

def _build(mid):
    msgs = {json.loads(l)["id"]: json.loads(l) for l in FIXTURES.read_text(encoding="utf-8").splitlines()}
    m = RawMessage.model_validate(msgs[mid])
    c, reasons, _ = ground(RuleExtractor().extract(m), m.text, m.sender)
    return [build_listing(lc, f"{mid}-{i}", "rules", m.sender, r)
            for i, (lc, r) in enumerate(zip(c.listings, reasons), 1)]


def test_m003_is_a_duplicate_of_m001():
    (m001,) = _build("m-001")
    (m003,) = _build("m-003")
    result = apply_dedup(m003, [m001])
    assert result.duplicate_of == "m-001-1"
    assert "possible_duplicate" not in result.review_reasons  # same broker phone → strong match


def test_m010_listings_are_not_duplicates_of_m001():
    (m001,) = _build("m-001")
    for listing in _build("m-010"):
        assert apply_dedup(listing, [m001]).duplicate_of is None
