"""Discord用の組み立て・制限処理と、同期・非同期Webhook送信。"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)
DISCORD_CONTENT_LIMIT = 2000
DISCORD_TITLE_LIMIT = 256
DISCORD_DESCRIPTION_LIMIT = 4096
DISCORD_FOOTER_LIMIT = 2048
MAX_EMBEDS_PER_MESSAGE = 10
MAX_EMBED_TEXT = 6000
_WEBHOOK = re.compile(r"(/api(?:/v\d+)?/webhooks/)[^/\s?'\"]+/[^\s'\"]+")


def mask_webhook_url(url: str) -> str:
    """Webhook識別子・トークン・クエリ・fragmentをログから除く。"""
    return _WEBHOOK.sub(r"\1***/***", url).split("?")[0].split("#")[0]


class _WebhookLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx自身のINFOログやhttpcoreのリクエスト行にもトークンを残さない。
        message = record.getMessage()
        if "/webhooks/" in message:
            record.msg = _WEBHOOK.sub(r"\1***/***", message)
            record.args = ()
        return True


for _name in ("httpx", "httpcore.http11", "httpcore.http2", "httpcore.proxy"):
    logging.getLogger(_name).addFilter(_WebhookLogFilter())


def _length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def clip(text: str, limit: int) -> str:
    """絵文字を分断せず、省略記号込みでUTF-16の上限に収める。"""
    if limit < 1:
        raise ValueError("limit は1以上が必要です")
    if _length(text) <= limit:
        return text
    return text.encode("utf-16-le")[: (limit - 1) * 2].decode("utf-16-le", "ignore") + "…"


def split_for_discord(text: str, *, limit: int = DISCORD_CONTENT_LIMIT) -> list[str]:
    """本文を欠落なく分割する。可能なら改行位置で区切る。"""
    if limit < 2:
        raise ValueError("limit は2以上が必要です")
    chunks: list[str] = []
    while text:
        if _length(text) <= limit:
            chunks.append(text)
            break
        prefix = text.encode("utf-16-le")[: limit * 2].decode("utf-16-le", "ignore")
        boundary = prefix.rfind("\n")
        size = boundary + 1 if boundary >= 0 else len(prefix)
        chunks.append(text[:size])
        text = text[size:]
    return chunks


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("embedの入れ子は辞書で指定してください")
    return dict(cast(Mapping[str, object], value))


def _trim_text(data: dict[str, object], key: str, limit: int, budget: int) -> int:
    value = data.get(key)
    if value is None:
        return budget
    if not isinstance(value, str):
        raise ValueError("embedのテキストは文字列で指定してください")
    if budget <= 0 or not value:
        data.pop(key)
        return budget
    data[key] = clip(value, min(limit, budget))
    return budget - _length(cast(str, data[key]))


def normalize_embed(embed: Mapping[str, object]) -> dict[str, object]:
    """各フィールドとembed全体の6000文字上限を適用する。入力は変更しない。"""
    result = dict(embed)
    budget = MAX_EMBED_TEXT
    for key, limit in (("title", 256), ("description", 4096)):
        budget = _trim_text(result, key, limit, budget)
    for key, text_key, limit in (("author", "name", 256), ("footer", "text", 2048)):
        if key in result:
            nested = _mapping(result[key])
            budget = _trim_text(nested, text_key, limit, budget)
            if text_key in nested:
                result[key] = nested
            else:
                result.pop(key)
    if "fields" in result:
        original = result["fields"]
        if not isinstance(original, list):
            raise ValueError("fieldsはリストで指定してください")
        fields: list[dict[str, object]] = []
        for value in original[:25]:
            entry = _mapping(value)
            # nameとvalueの両方が必要なので、残量が足りないものは除外する。
            if budget < 2:
                break
            budget = _trim_text(entry, "name", 256, budget - 1) + 1
            budget = _trim_text(entry, "value", 1024, budget)
            if "name" in entry and "value" in entry:
                fields.append(entry)
        result["fields"] = fields
    if not any(
        result.get(key)
        for key in ("title", "description", "fields", "image", "thumbnail", "author", "footer")
    ):
        raise ValueError("空のembedは送信できません")
    return result


def build_embed(
    *,
    title: str = "",
    description: str = "",
    url: str | None = None,
    color: int | None = None,
    footer: str | None = None,
    timestamp: str | None = None,
    fields: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """記事モデルに依存せずembedを組み立てる。"""
    embed: dict[str, object] = {"title": title, "description": description}
    for key, value in (("url", url), ("color", color), ("timestamp", timestamp)):
        if value is not None:
            embed[key] = value
    if footer is not None:
        embed["footer"] = {"text": footer}
    if fields:
        embed["fields"] = [dict(entry) for entry in fields]
    return normalize_embed(embed)


def _embed_length(embed: Mapping[str, object]) -> int:
    total = sum(_length(str(embed[key])) for key in ("title", "description") if key in embed)
    for key, text_key in (("author", "name"), ("footer", "text")):
        if key in embed:
            total += _length(str(_mapping(embed[key]).get(text_key, "")))
    fields = embed.get("fields", [])
    if isinstance(fields, list):
        for value in fields:
            entry = _mapping(value)
            total += sum(_length(str(entry.get(key, ""))) for key in ("name", "value"))
    return total


def build_payloads(
    *, content: str = "", embeds: Sequence[Mapping[str, object]] = ()
) -> list[dict[str, object]]:
    """本文2000文字・10 embed・embed合計6000文字ごとに分割する。"""
    payloads: list[dict[str, object]] = [
        {"content": chunk, "allowed_mentions": {"parse": []}}
        for chunk in split_for_discord(content)
    ]
    group: list[dict[str, object]] = []
    total = 0
    groups: list[list[dict[str, object]]] = []
    for raw in embeds:
        embed = normalize_embed(raw)
        size = _embed_length(embed)
        if group and (len(group) == MAX_EMBEDS_PER_MESSAGE or total + size > MAX_EMBED_TEXT):
            groups.append(group)
            group, total = [], 0
        group.append(embed)
        total += size
    if group:
        groups.append(group)
    for index, chunk_embeds in enumerate(groups):
        if index >= len(payloads):
            payloads.append({"allowed_mentions": {"parse": []}})
        payloads[index]["embeds"] = chunk_embeds
    return payloads


def parse_retry_after(response: httpx.Response) -> float:
    """JSON、ヘッダの順に有限の非負秒数を読む。不正値なら1秒。"""
    values: list[object] = []
    try:
        body: object = response.json()
        if isinstance(body, dict):
            values.append(body.get("retry_after"))
    except ValueError:
        pass
    values.append(response.headers.get("retry-after"))
    for value in values:
        try:
            delay = float(cast(str | float, value))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(delay) and delay >= 0:
            return delay
    return 1.0


@dataclass(frozen=True)
class MessageResult:
    """1メッセージの結果。失敗以前の送信成功も呼び出し元で記録できる。"""

    success: bool
    message_id: str | None = None
    channel_id: str | None = None
    status_code: int | None = None
    error: str | None = None


@dataclass
class SendResult:
    """分割した各メッセージの結果と、送信予定payload。"""

    messages: list[MessageResult] = field(default_factory=list)
    payloads: list[dict[str, object]] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success(self) -> bool:
        """dry-runは送信成功と扱わない。"""
        return bool(self.messages) and all(message.success for message in self.messages)

    @property
    def message_ids(self) -> list[str]:
        """確認できたメッセージIDだけを返す。"""
        return [message.message_id for message in self.messages if message.message_id is not None]


def _request_url(url: str, wait: bool) -> httpx.URL:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("URLの形式が不正です")
    except ValueError:
        raise httpx.InvalidURL("Webhook URLの形式が不正です") from None
    return httpx.URL(url).copy_set_param("wait", str(wait).lower())


def _response_result(response: httpx.Response, wait: bool) -> MessageResult:
    status = response.status_code
    if not 200 <= status < 300:
        return MessageResult(False, status_code=status, error=f"Discord送信失敗: HTTP {status}")
    if not wait:
        return MessageResult(True, status_code=status)
    try:
        body: object = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("id"), str) and body["id"]:
        channel_id = body.get("channel_id")
        return MessageResult(
            True, body["id"], channel_id if isinstance(channel_id, str) else None, status
        )
    return MessageResult(False, status_code=status, error="メッセージIDを確認できませんでした")


class DiscordClient:
    """同期クライアント。注入したHTTPクライアントの所有権は呼び出し元に残す。"""

    def __init__(
        self,
        webhook_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
    ) -> None:
        if not webhook_url.strip():
            raise ValueError("Webhook URLが空です")
        self._url = webhook_url
        self._client = client
        self._timeout = timeout
        self._sleep = sleep
        self._max_retries = max(0, max_retries)
        self._closed = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}(webhook_url={mask_webhook_url(self._url)!r})"

    def __enter__(self) -> DiscordClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """クライアントを使用済みにする。外部のHTTPクライアントは閉じない。"""
        self._closed = True

    def send(
        self,
        content: str = "",
        embeds: Sequence[Mapping[str, object]] = (),
        *,
        wait: bool = True,
        dry_run: bool = False,
    ) -> SendResult:
        """分割して順番に送信する。429のみ再試行し、結果を各メッセージごとに返す。"""
        if self._closed:
            raise RuntimeError("close後は再利用できません")
        result = SendResult(
            payloads=build_payloads(content=content, embeds=embeds), dry_run=dry_run
        )
        if dry_run or not result.payloads:
            return result
        own = self._client is None
        used = self._client if self._client is not None else httpx.Client()
        try:
            for payload in result.payloads:
                for attempt in range(self._max_retries + 1):
                    try:
                        url = _request_url(self._url, wait)
                        response = used.post(url, json=payload, timeout=self._timeout)
                    except (httpx.HTTPError, httpx.InvalidURL):
                        message = MessageResult(False, error="Discordに接続できません")
                        break
                    if response.status_code == 429 and attempt < self._max_retries:
                        self._sleep(parse_retry_after(response))
                        continue
                    message = _response_result(response, wait)
                    break
                result.messages.append(message)
                if not message.success:
                    logger.warning("Discord送信失敗: %s", mask_webhook_url(self._url))
            return result
        finally:
            if own:
                used.close()


class AsyncDiscordClient:
    """非同期版。sleepとHTTPクライアントを注入できる。"""

    def __init__(
        self,
        webhook_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = 2,
    ) -> None:
        if not webhook_url.strip():
            raise ValueError("Webhook URLが空です")
        self._url = webhook_url
        self._client = client
        self._timeout = timeout
        self._sleep = sleep
        self._max_retries = max(0, max_retries)
        self._closed = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}(webhook_url={mask_webhook_url(self._url)!r})"

    async def __aenter__(self) -> AsyncDiscordClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """外部のHTTPクライアントは閉じず、このラッパーを使用済みにする。"""
        self._closed = True

    async def send(
        self,
        content: str = "",
        embeds: Sequence[Mapping[str, object]] = (),
        *,
        wait: bool = True,
        dry_run: bool = False,
    ) -> SendResult:
        """同期版と同じ分割・結果契約で送信する。"""
        if self._closed:
            raise RuntimeError("close後は再利用できません")
        result = SendResult(
            payloads=build_payloads(content=content, embeds=embeds), dry_run=dry_run
        )
        if dry_run or not result.payloads:
            return result
        own = self._client is None
        used = self._client if self._client is not None else httpx.AsyncClient()
        try:
            for payload in result.payloads:
                for attempt in range(self._max_retries + 1):
                    try:
                        url = _request_url(self._url, wait)
                        response = await used.post(url, json=payload, timeout=self._timeout)
                    except (httpx.HTTPError, httpx.InvalidURL):
                        message = MessageResult(False, error="Discordに接続できません")
                        break
                    if response.status_code == 429 and attempt < self._max_retries:
                        await self._sleep(parse_retry_after(response))
                        continue
                    message = _response_result(response, wait)
                    break
                result.messages.append(message)
                if not message.success:
                    logger.warning("Discord送信失敗: %s", mask_webhook_url(self._url))
            return result
        finally:
            if own:
                await used.aclose()
