import httpx
import pytest

from mcp_server.http_base import AuthenticatedERPClient


@pytest.mark.asyncio
async def test_idempotent_post_retries_after_server_error():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"code": 200, "data": {"token": "token"}})
        if calls == 2:
            return httpx.Response(503, json={"message": "temporary"})
        return httpx.Response(200, json={"code": 200, "data": {"id": 1}})

    client = AuthenticatedERPClient(
        base_url="http://erp/api", transport=httpx.MockTransport(handler),
        username="agent", password="secret", timeout=1,
    )
    response = await client.post("/orders/create", json={}, headers={"Idempotency-Key": "agent-order:12345678"})
    await client.aclose()
    assert response.status_code == 200
    assert calls == 3


@pytest.mark.asyncio
async def test_non_idempotent_post_is_not_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"code": 200, "data": {"token": "token"}})
        return httpx.Response(503)

    client = AuthenticatedERPClient(
        base_url="http://erp/api", transport=httpx.MockTransport(handler),
        username="agent", password="secret", timeout=1,
    )
    response = await client.post("/unsafe", json={})
    await client.aclose()
    assert response.status_code == 503
    assert calls == 2
