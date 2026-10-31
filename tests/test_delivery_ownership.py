"""内部クライアントの破棄とログのクエリ保護を追加検証する。"""

import logging

import httpx

from radar_core import AsyncDiscordClient, DiscordClient


def test_internal_sync_client_closed(monkeypatch, caplog):
    original = httpx.Client
    clients = []

    def factory():
        client = original(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"id": "1"}))
        )
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", factory)
    caplog.set_level(logging.INFO)
    url = "https://discord.com/api/webhooks/123/dummy-token?thread_id=456&secret=dummy-query"
    result = DiscordClient(url).send("通知")
    assert result.success
    assert clients[0].is_closed
    assert "dummy-token" not in caplog.text
    assert "dummy-query" not in caplog.text


async def test_internal_async_client_closed(monkeypatch):
    original = httpx.AsyncClient
    clients = []

    def factory():
        client = original(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"id": "1"}))
        )
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    result = await AsyncDiscordClient("https://discord.com/api/webhooks/123/dummy-token").send(
        "通知"
    )
    assert result.success
    assert clients[0].is_closed
