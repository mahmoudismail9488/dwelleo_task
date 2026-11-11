import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "messages.jsonl"
SAMPLES = [json.loads(l) for l in FIXTURES.read_text(encoding="utf-8").splitlines() if l.strip()]
BY_ID = {m["id"]: m for m in SAMPLES}


@pytest.fixture
def client():
    return TestClient(create_app(Store(":memory:")))


def test_post_message_returns_contract_result(client):
    r = client.post("/messages", json=BY_ID["m-001"])
    assert r.status_code == 200
    body = r.json()
    assert body["message_id"] == "m-001" and body["classification"] == "listing"
    (l,) = body["listings"]
    assert l["price"] == {"amount": 3_200_000, "currency": "SAR", "period": "total"}
    assert l["needs_review"] is False and l["contact_phone"] == "+966551234567"


def test_buyer_request_returns_no_listings(client):
    body = client.post("/messages", json=BY_ID["m-005"]).json()
    assert body["classification"] == "buyer_request" and body["listings"] == []


def test_invalid_message_is_rejected_with_422(client):
    assert client.post("/messages", json={"text": "no id"}).status_code == 422
    assert client.post("/messages", json={"id": "x"}).status_code == 422


def test_reposting_same_id_is_idempotent(client):
    first = client.post("/messages", json=BY_ID["m-001"]).json()
    second = client.post("/messages", json={**BY_ID["m-001"], "text": "changed"}).json()
    assert first == second
    assert len(client.get("/listings").json()) == 1


def test_dedup_across_requests(client):
    client.post("/messages", json=BY_ID["m-001"])
    body = client.post("/messages", json=BY_ID["m-003"]).json()
    assert body["listings"][0]["duplicate_of"] == "m-001-1"


def test_list_listings_with_review_filter(client):
    for m in SAMPLES:
        client.post("/messages", json=m)
    all_ = client.get("/listings").json()
    flagged = client.get("/listings", params={"needs_review": "true"}).json()
    clean = client.get("/listings", params={"needs_review": "false"}).json()
    assert len(all_) == 9
    assert {l["id"] for l in clean} == {"m-001-1", "m-002-1", "m-003-1"}
    assert len(flagged) == 6 and all(l["review_reasons"] for l in flagged)


def test_invalid_filter_value_is_rejected(client):
    assert client.get("/listings", params={"needs_review": "maybe"}).status_code == 422


def test_approve_moves_listing_out_of_review(client):
    client.post("/messages", json=BY_ID["m-007"])
    r = client.post("/listings/m-007-1/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_review"] is False and body["approved_at"]
    assert body["review_reasons"] == ["price_contradiction", "contact_phone_from_sender"]
    assert client.get("/listings", params={"needs_review": "true"}).json() == []
    assert client.get("/listings", params={"needs_review": "false"}).json()[0]["id"] == "m-007-1"


def test_approve_unknown_listing_is_404(client):
    assert client.post("/listings/nope/approve").status_code == 404


def test_default_db_is_created_lazily_from_env(tmp_path, monkeypatch):
    db = tmp_path / "sub" / "api.db"
    monkeypatch.setenv("DB_PATH", str(db))
    client = TestClient(create_app())
    assert not db.exists()
    client.post("/messages", json=BY_ID["m-009"])
    assert db.exists()


def test_llm_runs_endpoint_lists_calls_per_message():
    from tests.test_llm_extractor import M001_GOOD, llm
    client = TestClient(create_app(Store(":memory:"), extractor=llm(M001_GOOD)))
    client.post("/messages", json=BY_ID["m-001"])
    client.post("/messages", json=BY_ID["m-002"])
    assert [r["message_id"] for r in client.get("/llm-runs").json()] == ["m-001", "m-002"]
    (row,) = client.get("/llm-runs", params={"message_id": "m-002"}).json()
    assert row["status"] == "ok" and row["input_tokens"] == 900 and "latency_s" in row


def test_root_reports_success(client):
    r = client.get("/")
    assert r.status_code == 200 and r.json() == {"status": "success"}


def test_project_message_gets_sender_phone_with_flag(client):
    body = client.post("/messages", json=BY_ID["m-006"]).json()
    assert body["listings"] == []
    assert body["project"]["contact_phone"] == "+966512223333"
    assert body["project"]["review_reasons"] == ["contact_phone_from_sender"]
    assert body["project"]["needs_review"] is True
