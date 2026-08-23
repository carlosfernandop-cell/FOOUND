"""RestDb wire-contract tests — the PRODUCTION transport, HTTP mocked.

These prove the exact bytes the production adapter puts on the wire and the
exact way it interprets what comes back, without touching any server:

W1  discovery GET: URL, query, ordering on requested_at, headers, row/None
W2  claim RPC: endpoint name, {"p_job": ...} body, response passthrough
W3  settle RPC: endpoint name, exact p_job/p_results/p_policy body with the
    full nested results payload serialized verbatim
W4  finalize RPC: endpoint name, p_outcome='failed', p_error null and frozen
W5  PostgREST error mapping: 400 {"message":"job_not_queued"} -> DoorError
    named 'job_not_queued'; non-JSON error body -> DoorError('http_<status>')
W6  janitor stale query: running filter + started_at=lt.<iso cutoff>
W7  evidence_rows: id=in.("a","b") quoting and column list
W8  existing_memory: active,tension filter, order, reconciliation cap

The adapter under test is the real RestDb — only its `requests` module is
replaced with a recorder. No second implementation exists.
"""

import json

import pytest

import synthesis_runner as sr
from synthesis_runner import DoorError, RestDb, FROZEN_CLIENT_COPY

BASE = "https://example-project.supabase.co"
KEY = "service-key-placeholder"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text_body=None):
        self.status_code = status_code
        self._payload = payload
        self._text = text_body

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected http {self.status_code}")


class FakeRequests:
    """Records every call the adapter makes; replays scripted responses."""

    def __init__(self):
        self.calls = []
        self.responses = []

    def queue(self, resp):
        self.responses.append(resp)
        return self

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return self.responses.pop(0)

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append(
            {"method": "POST", "url": url, "headers": headers, "data": data}
        )
        return self.responses.pop(0)


@pytest.fixture()
def wire():
    db = RestDb(BASE, KEY)
    fake = FakeRequests()
    db._requests = fake
    return db, fake


def _assert_headers(headers, with_content_type):
    assert headers["apikey"] == KEY
    assert headers["Authorization"] == f"Bearer {KEY}"
    if with_content_type:
        assert headers["Content-Type"] == "application/json"


# -- W1 · discovery ---------------------------------------------------------

def test_w1_discovery_url_headers_and_row(wire):
    db, fake = wire
    fake.queue(FakeResponse(200, [
        {"id": "j1", "agent_id": "a1", "requested_at": "2026-08-22T00:00:00Z"}
    ]))
    row = db.oldest_queued_synthesize_job()
    call = fake.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == (
        f"{BASE}/rest/v1/jobs?type=eq.synthesize&status=eq.queued"
        "&select=id,agent_id,requested_at&order=requested_at.asc&limit=1"
    )
    _assert_headers(call["headers"], with_content_type=True)
    assert row == {"id": "j1", "agent_id": "a1", "requested_at": "2026-08-22T00:00:00Z"}


def test_w1b_discovery_empty_is_none(wire):
    db, fake = wire
    fake.queue(FakeResponse(200, []))
    assert db.oldest_queued_synthesize_job() is None


# -- W2 · claim -------------------------------------------------------------

def test_w2_claim_wire(wire):
    db, fake = wire
    fake.queue(FakeResponse(200, {"status": "empty"}))
    out = db.claim("11111111-1111-4111-8111-111111111111")
    call = fake.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{BASE}/rest/v1/rpc/claim_synthesis_batch"
    assert json.loads(call["data"]) == {"p_job": "11111111-1111-4111-8111-111111111111"}
    _assert_headers(call["headers"], with_content_type=True)
    assert out == {"status": "empty"}


# -- W3 · settle ------------------------------------------------------------

def test_w3_settle_wire_full_payload(wire):
    db, fake = wire
    results = {
        "read": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "failed": [{"item": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "code": "unreadable"}],
        "memory": [{
            "layer": "record", "statement": "Fact.", "provenance": "stated",
            "evidence": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            "is_direction": False,
        }],
        "reinforce": [{
            "memory": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "evidence": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
            "is_direction": True,
        }],
    }
    fake.queue(FakeResponse(200, {"status": "settled", "outcome": "needs_more_evidence"}))
    out = db.settle("22222222-2222-4222-8222-222222222222", results, sr.POLICY)
    call = fake.calls[0]
    assert call["url"] == f"{BASE}/rest/v1/rpc/settle_synthesis_results"
    body = json.loads(call["data"])
    assert set(body.keys()) == {"p_job", "p_results", "p_policy"}
    assert body["p_job"] == "22222222-2222-4222-8222-222222222222"
    assert body["p_results"] == results          # nested payload verbatim
    assert body["p_policy"] == sr.POLICY
    assert out["outcome"] == "needs_more_evidence"


# -- W4 · finalize ----------------------------------------------------------

def test_w4_finalize_wire(wire):
    db, fake = wire
    fake.queue(FakeResponse(200, {"status": "finalized", "outcome": "failed"}))
    db.finalize_failed("33333333-3333-4333-8333-333333333333", None)
    body = json.loads(fake.calls[0]["data"])
    assert fake.calls[0]["url"] == f"{BASE}/rest/v1/rpc/finalize_synthesis"
    assert body == {
        "p_job": "33333333-3333-4333-8333-333333333333",
        "p_outcome": "failed", "p_error": None,
    }
    fake.queue(FakeResponse(200, {"status": "finalized", "outcome": "failed"}))
    db.finalize_failed(
        "33333333-3333-4333-8333-333333333333",
        FROZEN_CLIENT_COPY["batch_too_large"],
    )
    body = json.loads(fake.calls[1]["data"])
    assert body["p_error"] == FROZEN_CLIENT_COPY["batch_too_large"]


# -- W5 · PostgREST error mapping ------------------------------------------

def test_w5_error_mapping_named_door(wire):
    db, fake = wire
    fake.queue(FakeResponse(400, {
        "code": "P0001", "message": "job_not_queued",
        "details": None, "hint": None,
    }))
    with pytest.raises(DoorError) as exc:
        db.claim("44444444-4444-4444-8444-444444444444")
    assert exc.value.name == "job_not_queued"


def test_w5b_error_mapping_non_json(wire):
    db, fake = wire
    fake.queue(FakeResponse(500, payload=None))
    with pytest.raises(DoorError) as exc:
        db.claim("44444444-4444-4444-8444-444444444444")
    assert exc.value.name == "http_500"


def test_w5c_error_mapping_permission_denied(wire):
    db, fake = wire
    fake.queue(FakeResponse(403, {
        "code": "42501",
        "message": "permission denied for function settle_synthesis_results",
    }))
    with pytest.raises(DoorError) as exc:
        db.settle("55555555-5555-4555-8555-555555555555", {}, sr.POLICY)
    assert "permission denied" in exc.value.name


# -- W6 · janitor stale query ----------------------------------------------

def test_w6_janitor_query(wire):
    db, fake = wire
    fake.queue(FakeResponse(200, []))
    out = db.stale_running_synthesize_jobs(sr.JANITOR_STALE_MINUTES)
    url = fake.calls[0]["url"]
    assert url.startswith(f"{BASE}/rest/v1/jobs?type=eq.synthesize&status=eq.running")
    assert "&started_at=lt." in url
    assert "&select=id,agent_id,started_at" in url
    # cutoff must be a URL-SAFE ISO-8601 UTC timestamp: a '+00:00' offset puts
    # a raw '+' in the query string, which HTTP decodes as a space and
    # PostgREST rejects with 400 (found live in Fire #1 run 1).
    import re as _re

    cutoff = url.split("started_at=lt.")[1].split("&")[0]
    assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", cutoff), cutoff
    # the whole URL must contain no characters that change meaning in a query
    assert not set(url) & {"+", " ", '"', "'"}, url
    assert out == []


# -- W7 · evidence rows -----------------------------------------------------

def test_w7_evidence_rows_query(wire):
    db, fake = wire
    fake.queue(FakeResponse(200, []))
    db.evidence_rows(["aaaa", "bbbb"])
    url = fake.calls[0]["url"]
    assert "id=in.(aaaa,bbbb)" in url          # bare UUIDs, no quoting
    assert not set(url) & {"+", " ", '"', "'"}, url
    for col in ("kind", "label", "storage_path", "body", "mime_type",
                "byte_size", "status", "submitted_in"):
        assert col in url


# -- W8 · existing memory (reconciliation) ---------------------------------

def test_w8a_comparison_memory_query(wire):
    db, fake = wire
    fake.queue(FakeResponse(200, []))
    db.comparison_memory("agent-1")
    url = fake.calls[0]["url"]
    assert "memory?agent_id=eq.agent-1" in url
    assert "status=in.(active,tension)" in url
    assert "order=created_at.asc" in url
    assert f"limit={sr.RECONCILIATION_ROW_CAP}" in url


def test_w8b_retracted_memory_query(wire):
    # 009 correction 1: retracted suppression context is a SEPARATE query —
    # the comparison cap can never truncate it — fetched with a fail-closed
    # sentinel limit of RETRACTED_FETCH_LIMIT + 1.
    db, fake = wire
    fake.queue(FakeResponse(200, []))
    db.retracted_memory("agent-1")
    url = fake.calls[0]["url"]
    assert "memory?agent_id=eq.agent-1" in url
    assert "status=eq.retracted" in url
    assert f"limit={sr.RETRACTED_FETCH_LIMIT + 1}" in url
    assert not set(url) & {"+", " ", '"', "'"}, url
