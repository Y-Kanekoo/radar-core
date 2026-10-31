"""汎用Discord APIの境界値・送信・秘密情報の保護を検証する。"""

import json
import logging

import httpx
import pytest

from radar_core import (
    AsyncDiscordClient,
    DiscordClient,
    build_embed,
    build_payloads,
    clip,
    mask_webhook_url,
    normalize_embed,
    parse_retry_after,
    split_for_discord,
)

# テスト専用の架空URLであり、実在する認証情報は使わない。
WEBHOOK = "https://discord.com/api/v10/webhooks/123/test-token?wait=false&thread_id=456"


def units(text):
    return len(text.encode("utf-16-le")) // 2


def test_clip_and_split():
    assert clip("abc", 3) == "abc"
    assert clip("😀" * 3, 5) == "😀😀…"
    assert clip("😀", 1) == "…"
    text = "😀" * 2001 + "\n本文\n" + "a" * 3000
    chunks = split_for_discord(text)
    assert "".join(chunks) == text
    assert all(units(chunk) <= 2000 for chunk in chunks)
    assert split_for_discord("") == []
    assert split_for_discord("abc\ndef", limit=4) == ["abc\n", "def"]
    with pytest.raises(ValueError):
        clip("x", 0)
    with pytest.raises(ValueError):
        split_for_discord("x", limit=1)


def test_embed_limits_and_no_mutation():
    raw = {
        "title": "😀" * 300,
        "description": "d" * 5000,
        "author": {"name": "a" * 400},
        "footer": {"text": "f" * 3000},
        "fields": [{"name": "n" * 300, "value": "v" * 2000}] * 30,
    }
    output = normalize_embed(raw)
    assert units(output["title"]) <= 256
    assert units(output["description"]) <= 4096
    assert units(output["author"]["name"]) <= 256
    assert units(output["footer"]["text"]) <= 2048
    assert len(raw["fields"]) == 30
    assert len(raw["footer"]["text"]) == 3000
    assert len(output["fields"]) == 0
    fields = normalize_embed({"fields": [{"name": "n", "value": "v"}] * 30})
    assert len(fields["fields"]) == 25
    bounded = normalize_embed({"fields": [{"name": "n" * 500, "value": "v" * 2000}]})
    assert units(bounded["fields"][0]["name"]) == 256
    assert units(bounded["fields"][0]["value"]) == 1024
    remaining = normalize_embed(
        {
            "description": "x" * 4096,
            "footer": {"text": "y" * 1903},
            "fields": [{"name": "n", "value": "v"}],
        }
    )
    assert remaining["fields"] == []


@pytest.mark.parametrize(
    "embed",
    [
        {},
        {"title": 123},
        {"footer": "bad"},
        {"fields": "bad"},
        {"fields": [123]},
    ],
)
def test_bad_embeds(embed):
    with pytest.raises(ValueError):
        normalize_embed(embed)


def test_empty_nested_text_removed():
    assert normalize_embed({"title": "t", "author": {"name": ""}}) == {"title": "t"}
    result = normalize_embed({"title": "t", "fields": [{"name": "", "value": "v"}]})
    assert result["fields"] == []


def test_build_embed_and_payloads():
    embed = build_embed(
        title="記事",
        description="要約",
        footer="出所",
        color=0x0969DA,
        url="https://example.com",
        timestamp="2026-01-01T00:00:00Z",
        fields=[{"name": "項目", "value": "値"}],
    )
    assert embed["footer"]["text"] == "出所"
    payloads = build_payloads(content="本文", embeds=[embed] * 11)
    assert len(payloads) == 2
    assert len(payloads[0]["embeds"]) == 10
    assert len(payloads[1]["embeds"]) == 1
    assert payloads[0]["content"] == "本文"
    assert "content" not in payloads[1]
    assert payloads[0]["allowed_mentions"] == {"parse": []}
    assert len(build_payloads(embeds=[{"description": "a" * 4000}] * 2)) == 2
    assert len(build_payloads(content="a" * 5000)) == 3
    assert build_payloads() == []


@pytest.mark.parametrize(
    ("body", "header", "expected"),
    [
        ({"retry_after": 0.25}, "4", 0.25),
        ({}, "0.5", 0.5),
        ([], "2", 2),
        ({"retry_after": -1}, "-2", 1),
        ({"retry_after": "NaN"}, "inf", 1),
        ({"retry_after": {}}, "bad", 1),
        ({"retry_after": 0}, None, 0),
    ],
)
def test_retry_parser(body, header, expected):
    response = httpx.Response(429, json=body, headers={"retry-after": header} if header else {})
    assert parse_retry_after(response) == expected


def test_retry_parser_invalid_json():
    assert parse_retry_after(httpx.Response(429, text="invalid")) == 1


@pytest.mark.parametrize("client_type", [DiscordClient, AsyncDiscordClient])
def test_empty_url(client_type):
    with pytest.raises(ValueError):
        client_type(" ")


def test_sync_retries_ids_and_logging(caplog):
    calls = []
    delays = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"retry_after": 0.25})
        return httpx.Response(200, json={"id": str(len(calls)), "channel_id": "7"})

    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with DiscordClient(WEBHOOK, client=http, sleep=delays.append) as client:
            assert "test-token" not in repr(client)
            result = client.send("本文", [{"title": "x"}] * 11)
            assert result.success
            assert result.message_ids == ["2", "3"]
            assert result.messages[0].channel_id == "7"
            assert delays == [0.25]
            assert all(request.url.params["wait"] == "true" for request in calls)
            assert calls[0].url.params["thread_id"] == "456"
            assert calls[0].extensions["timeout"]["read"] == 20
        assert not http.is_closed
        with pytest.raises(RuntimeError):
            client.send("x")
    assert "test-token" not in caplog.text
    assert "test-token" not in mask_webhook_url(WEBHOOK)
    assert mask_webhook_url(WEBHOOK).endswith("/***/***")


@pytest.mark.parametrize(
    ("status", "body", "wait", "success"),
    [
        (204, None, False, True),
        (200, {"id": "1"}, True, True),
        (204, None, True, False),
        (200, [], True, False),
        (200, {"id": ""}, True, False),
        (400, {"token": "secret"}, True, False),
        (429, {}, True, False),
    ],
)
def test_sync_outcomes(status, body, wait, success):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = DiscordClient(WEBHOOK, client=http, max_retries=0).send("x", wait=wait)
    assert result.success is success
    assert len(calls) == 1
    assert "secret" not in repr(result)


def test_sync_network_and_partial_failure(caplog):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ConnectError(WEBHOOK)
        return httpx.Response(200, json={"id": "2"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = DiscordClient(WEBHOOK, client=http).send("a" * 3000)
    assert not result.success
    assert result.message_ids == ["2"]
    assert "test-token" not in caplog.text
    assert "test-token" not in repr(result)


def test_sync_dry_run_and_invalid_url():
    result = DiscordClient(WEBHOOK).send("x", dry_run=True)
    assert result.dry_run and not result.success
    assert result.payloads[0]["content"] == "x"
    assert not DiscordClient(WEBHOOK).send().success
    with DiscordClient("https://[invalid") as client:
        assert not client.send("x").success


async def test_async_retries_and_ids(caplog):
    count = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429, headers={"retry-after": "0.5"})
        assert request.url.params["wait"] == "true"
        assert json.loads(request.content)["content"]
        return httpx.Response(200, json={"id": str(count)})

    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with AsyncDiscordClient(WEBHOOK, client=http, sleep=sleep) as client:
            assert "test-token" not in repr(client)
            result = await client.send("x" * 3000)
            assert result.success
            assert result.message_ids == ["2", "3"]
        assert not http.is_closed
        with pytest.raises(RuntimeError):
            await client.send("x")
    assert delays == [0.5]
    assert "test-token" not in caplog.text


async def test_async_failure_and_dry_run(caplog):
    def handler(request):
        raise httpx.ReadTimeout(WEBHOOK)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AsyncDiscordClient(WEBHOOK, client=http)
        assert not (await client.send("x")).success
    assert "test-token" not in caplog.text
    assert (await AsyncDiscordClient(WEBHOOK).send("x", dry_run=True)).dry_run
    assert not (await AsyncDiscordClient(WEBHOOK).send()).success
    assert not (await AsyncDiscordClient("https://[invalid").send("x")).success


async def test_async_giveup_and_no_wait():
    async def sleep(delay):
        pass

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(429))
    ) as http:
        result = await AsyncDiscordClient(WEBHOOK, client=http, sleep=sleep).send("x")
        assert not result.success
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(204))
    ) as http:
        assert (await AsyncDiscordClient(WEBHOOK, client=http).send("x", wait=False)).success
