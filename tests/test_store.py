import pytest

from app.models import Listing, MessageResult, Price, Project, RawMessage
from app.store import Store


def msg(id):
    return RawMessage(id=id, text=f"text of {id}", sender="+966551234567")


def listing(id, **kw):
    base = dict(property_type="villa", district="Al Malqa", price=Price(amount=1), source="rules")
    base.update(kw)
    return Listing(id=id, **base)


@pytest.fixture
def store():
    return Store(":memory:")


def test_saved_result_round_trips(store):
    result = MessageResult(message_id="m-1", classification="listing",
                           listings=[listing("m-1-1", review_reasons=["price_missing"])])
    store.save(msg("m-1"), result)
    assert store.get_result("m-1") == result
    assert store.get_result("missing") is None


def test_project_result_round_trips(store):
    result = MessageResult(message_id="m-6", classification="project", project=Project(name="Al Waha"))
    store.save(msg("m-6"), result)
    assert store.get_result("m-6").project.name == "Al Waha"
    assert store.list_listings() == []


def test_list_listings_filters_by_needs_review(store):
    store.save(msg("m-1"), MessageResult(message_id="m-1", classification="listing", listings=[
        listing("m-1-1"), listing("m-1-2", review_reasons=["city_missing"])]))
    assert [l["id"] for l in store.list_listings()] == ["m-1-1", "m-1-2"]
    assert [l["id"] for l in store.list_listings(needs_review=True)] == ["m-1-2"]
    assert [l["id"] for l in store.list_listings(needs_review=False)] == ["m-1-1"]
    assert store.list_listings()[0]["message_id"] == "m-1"


def test_approve_clears_needs_review_and_keeps_reasons(store):
    store.save(msg("m-1"), MessageResult(message_id="m-1", classification="listing",
                                         listings=[listing("m-1-1", review_reasons=["city_missing"])]))
    approved = store.approve("m-1-1")
    assert approved["needs_review"] is False
    assert approved["approved_at"] is not None
    assert approved["review_reasons"] == ["city_missing"]  # kept as history
    assert store.list_listings(needs_review=True) == []


def test_approve_unknown_listing_returns_none(store):
    assert store.approve("nope") is None


def test_dedup_candidates_match_type_and_district_in_insertion_order(store):
    store.save(msg("m-1"), MessageResult(message_id="m-1", classification="listing", listings=[
        listing("m-1-1"), listing("m-1-2", district="Hittin")]))
    store.save(msg("m-2"), MessageResult(message_id="m-2", classification="listing",
                                         listings=[listing("m-2-1")]))
    found = store.dedup_candidates(listing("x"), exclude_message_id="m-9")
    assert [l.id for l in found] == ["m-1-1", "m-2-1"]


def test_dedup_candidates_exclude_the_same_message(store):
    store.save(msg("m-1"), MessageResult(message_id="m-1", classification="listing",
                                         listings=[listing("m-1-1")]))
    assert store.dedup_candidates(listing("x"), exclude_message_id="m-1") == []


def test_dedup_candidates_need_type_and_district(store):
    assert store.dedup_candidates(listing("x", district=None), exclude_message_id="m-1") == []


def test_duplicate_message_id_is_rejected(store):
    result = MessageResult(message_id="m-1", classification="irrelevant")
    store.save(msg("m-1"), result)
    with pytest.raises(Exception):
        store.save(msg("m-1"), result)


def test_file_database_persists_across_instances(tmp_path):
    path = str(tmp_path / "db.sqlite")
    Store(path).save(msg("m-1"), MessageResult(message_id="m-1", classification="irrelevant"))
    assert Store(path).get_result("m-1").classification == "irrelevant"


def test_missing_parent_folder_is_created(tmp_path):
    # Regression: `normalizer process … --db data/listings.db` crashed on a fresh clone.
    path = tmp_path / "data" / "nested" / "listings.db"
    Store(str(path)).save(msg("m-1"), MessageResult(message_id="m-1", classification="irrelevant"))
    assert path.exists()
