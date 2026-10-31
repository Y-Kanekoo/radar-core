"""取得のリトライ、解析形式、正規化の追加仕様を検証する。"""

import httpx
import pytest

from radar_core import fetch_feed, make_snippet, normalize_url, parse_feed


def test_extra_tracking():
    assert normalize_url(
        "https://example.com/?Custom=x&ID=1", extra_tracking_params=["CUSTOM"]
    ) == ("https://example.com/?ID=1")
    with pytest.raises(ValueError):
        make_snippet("x", 0)


async def test_fetch_retry_and_transport():
    calls = []
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("接続失敗")
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, content=b"feed")

    result = await fetch_feed(
        "https://example.com/feed",
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        max_retries=2,
        user_agent="test/1",
        timeout=2,
    )
    assert result.is_modified
    assert delays == [1, 2]
    assert calls[0].headers["user-agent"] == "test/1"
    assert calls[0].extensions["timeout"]["read"] == 2


async def test_fetch_permanent_and_general_errors():
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def permanent(request):
        raise httpx.UnsupportedProtocol("未対応")

    assert (
        await fetch_feed(
            "https://example.com",
            transport=httpx.MockTransport(permanent),
            sleep=sleep,
            max_retries=3,
        )
    ).status_code == 0
    assert delays == []

    def general(request):
        raise httpx.TooManyRedirects("転送が多すぎます")

    assert (await fetch_feed("https://example.com", transport=httpx.MockTransport(general))).error
    assert (await fetch_feed("https://[invalid")).error
    async with httpx.AsyncClient(transport=httpx.MockTransport(general)) as client:
        with pytest.raises(ValueError):
            await fetch_feed(
                "https://example.com", client=client, transport=httpx.MockTransport(general)
            )


def test_parse_rdf_and_fallbacks():
    rdf = """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        xmlns="http://purl.org/rss/1.0/">
        <item rdf:about="https://example.com/1"><title>記事</title>
        <link>https://example.com/1</link><description>本文</description></item></rdf:RDF>"""
    assert parse_feed(rdf).items[0].body == "本文"
    rss = """<rss version="2.0"><channel><title>番組</title>
        <item><enclosure url="https://example.com/a.mp3" type="audio/mpeg"/></item>
        <item><enclosure url="https://example.com/a.png" type="image/png"/></item>
        </channel></rss>"""
    items = parse_feed(rss).items
    assert items[0].url == "https://example.com/a.mp3"
    assert items[0].guid == items[0].url
    assert items[0].title == "(no title)"
    assert items[1].url == ""
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <title>記事</title><updated>2024-01-01T00:00:00Z</updated></entry></feed>"""
    assert parse_feed(atom).items[0].published_struct == (2024, 1, 1, 0, 0, 0)
    broken = parse_feed("<rss><channel><item><title>記事</title></item>")
    assert broken.bozo and broken.bozo_exception and broken.items
    assert parse_feed("https://example.com/feed").items == []
