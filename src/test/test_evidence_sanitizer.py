import pytest

from agent.evidence_sanitizer import (
    REDACTED,
    sanitize_event,
    sanitize_request,
    sanitize_result,
    sanitize_source,
)


@pytest.mark.parametrize(
    "field",
    ["password", "access_token", "Authorization", "apiKey", "secret", "hidden_reasoning", "messages"],
)
def test_secret_and_unrelated_chat_fields_are_rejected_before_evidence(field):
    with pytest.raises(ValueError, match="forbidden evidence field"):
        sanitize_request({"partName": "sensor", "quantity": 1, field: "sensitive"})


def test_configured_secret_field_is_rejected(monkeypatch):
    monkeypatch.setenv("EVIDENCE_SECRET_FIELDS", "internal_code")
    with pytest.raises(ValueError, match="internal_code"):
        sanitize_source({"parts": [{"id": 1, "internal_code": "x"}]})


def test_allowlists_drop_unrelated_fields_but_keep_reproducible_facts():
    source = sanitize_source({
        "parts": [{"id": 1, "name": "传感器", "purchasePrice": 1.2, "marketingCopy": "drop"}],
        "suppliers": {1: {"id": 1, "name": "甲", "status": 1, "address": "drop"}},
        "inventories": {1: {"partId": 1, "currentQuantity": 2, "internalNote": "drop"}},
        "unrelated": "drop",
    })
    assert source["parts"] == [{"id": 1, "name": "传感器", "purchasePrice": 1.2}]
    assert source["suppliers"]["1"] == {"id": 1, "name": "甲", "status": 1}
    assert source["inventories"]["1"] == {"partId": 1, "currentQuantity": 2}
    assert "unrelated" not in source


def test_event_payload_redacts_secrets_and_rejects_oversize():
    event = sanitize_event({"traceId": "TRC-1", "authorization": "Bearer x", "nested": {"api_key": "x"}})
    assert event == {"traceId": "TRC-1", "authorization": REDACTED, "nested": {"api_key": REDACTED}}
    with pytest.raises(ValueError, match="exceeds storage limit"):
        sanitize_event({"detail": "x" * 65_000})
