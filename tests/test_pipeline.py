import json
from pathlib import Path

import pytest

from app.models import ExtractionCandidate, ListingCandidate, PriceMention, RawMessage
from app.pipeline import process_message
from app.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "messages.jsonl"
SAMPLES = [RawMessage.model_validate(json.loads(l))
           for l in FIXTURES.read_text(encoding="utf-8").splitlines() if l.strip()]
BY_ID = {m.id: m for m in SAMPLES}


class FakeExtractor:
    """Stands in for the LLM: returns a canned candidate or raises."""

    name = "llm"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def extract(self, message):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def store():
    return Store(":memory:")


def run_all(store, extractor=None):
    return {m.id: process_message(m, store, extractor) for m in SAMPLES}


# --- samples end to end (rules) ---

def test_all_samples_end_to_end(store):
    r = run_all(store)
    assert {k: v.classification.value for k, v in r.items()} == {
        "m-001": "listing", "m-002": "listing", "m-003": "listing", "m-004": "listing",
        "m-005": "buyer_request", "m-006": "project", "m-007": "listing", "m-008": "listing",
        "m-009": "irrelevant", "m-010": "listing"}
    assert r["m-003"].listings[0].duplicate_of == "m-001-1"
    assert r["m-005"].listings == [] and r["m-009"].listings == []
    assert r["m-006"].listings == [] and r["m-006"].project.min_price == 780_000
    assert [l.id for l in r["m-010"].listings] == ["m-010-1", "m-010-2", "m-010-3"]
    assert r["m-007"].listings[0].price.amount is None
    assert all(l.source == "rules" for res in r.values() for l in res.listings)


def test_results_are_stored_and_listable(store):
    run_all(store)
    ids = [l["id"] for l in store.list_listings()]
    assert ids[:3] == ["m-001-1", "m-002-1", "m-003-1"] and len(ids) == 9
    assert {l["id"] for l in store.list_listings(needs_review=False)} == {"m-001-1", "m-002-1", "m-003-1"}


def test_processing_the_same_message_twice_returns_the_stored_result(store):
    first = process_message(BY_ID["m-001"], store)
    fake = FakeExtractor(error=RuntimeError("must not be called"))
    assert process_message(BY_ID["m-001"], store, fake) == first
    assert fake.calls == 0
    assert len(store.list_listings()) == 1


# --- LLM path with a fake extractor ---

def test_llm_candidate_is_used_and_grounded(store):
    fake = FakeExtractor(ExtractionCandidate(classification="listing", listings=[ListingCandidate(
        purpose="sale", property_type="villa", city="Riyadh", district="Al Malqa", bedrooms=5,
        built_up_area_sqm=380, prices=[PriceMention(quote="3.2M", amount=3_200_000)],
        contact_phone_text="0551234567")]))
    (l,) = process_message(BY_ID["m-001"], store, fake).listings
    assert l.source == "llm" and l.price.amount == 3_200_000 and l.review_reasons == []


def test_invented_llm_values_are_stripped_and_flagged(store):
    fake = FakeExtractor(ExtractionCandidate(classification="listing", listings=[ListingCandidate(
        purpose="rent", property_type="apartment", city="Riyadh", district="Al Olaya", bedrooms=2,
        prices=[PriceMention(quote="85,000", amount=85_000)], price_period="yearly",
        contact_phone_text="0559999999")]))
    (l,) = process_message(BY_ID["m-004"], store, fake).listings
    assert l.city is None  # "Riyadh" isn't in m-004
    assert l.contact_phone == "+966541112222"  # invented phone dropped → sender fallback
    assert {"city_unverified", "contact_phone_unverified", "contact_phone_from_sender"} <= set(l.review_reasons)


@pytest.mark.parametrize("error", [TimeoutError("timed out"), ValueError("not JSON"), RuntimeError("500")])
def test_llm_failure_falls_back_to_rules_with_flag(store, error):
    result = process_message(BY_ID["m-001"], store, FakeExtractor(error=error))
    (l,) = result.listings
    assert l.source == "rules"
    assert l.price.amount == 3_200_000
    assert l.review_reasons == ["llm_fallback_used"]


@pytest.mark.parametrize("bad", [
    ExtractionCandidate(classification="spam"),
    ExtractionCandidate(classification="listing", listings=[]),
])
def test_unusable_llm_output_falls_back_to_rules(store, bad):
    result = process_message(BY_ID["m-002"], store, FakeExtractor(bad))
    assert result.classification == "listing"
    assert result.listings[0].source == "rules"
    assert "llm_fallback_used" in result.listings[0].review_reasons


def test_fallback_on_a_project_flags_the_project(store):
    result = process_message(BY_ID["m-006"], store, FakeExtractor(error=TimeoutError()))
    assert "llm_fallback_used" in result.project.review_reasons


def test_llm_listings_for_a_buyer_request_are_discarded(store):
    fake = FakeExtractor(ExtractionCandidate(classification="buyer_request",
                                             listings=[ListingCandidate(property_type="villa")]))
    result = process_message(BY_ID["m-005"], store, fake)
    assert result.classification == "buyer_request" and result.listings == []
