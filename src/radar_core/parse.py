"""RSS・Atom・RDFを、アプリケーションに依存しない型へ変換する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import feedparser


@dataclass
class ParsedItem:
    """HTMLを含み得る、正規化前のエントリ。"""

    guid: str
    url: str
    title: str
    body: str
    author: str | None
    published_struct: tuple[int, ...] | None


@dataclass
class ParsedFeed:
    """抽出結果と、壊れたXMLの診断情報。"""

    items: list[ParsedItem]
    bozo: bool
    bozo_exception: str | None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _records(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(Mapping[str, object], entry) for entry in value if isinstance(entry, dict)]


def parse_feed(content: bytes | str) -> ParsedFeed:
    """本文だけを解析する。文字列もバイト列化し、URLやパスとして開かせない。"""
    parsed = cast(
        Mapping[str, object],
        feedparser.parse(content.encode("utf-8") if isinstance(content, str) else content),
    )
    items: list[ParsedItem] = []
    for entry in _records(parsed.get("entries")):
        url = _text(entry.get("link"))
        if not url:
            url = next(
                (
                    _text(enclosure.get("href"))
                    for enclosure in _records(entry.get("enclosures"))
                    if _text(enclosure.get("type")).startswith("audio/") and enclosure.get("href")
                ),
                "",
            )
        contents = _records(entry.get("content"))
        body = _text(contents[0].get("value")) if contents else ""
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        published_struct = None
        if isinstance(published, tuple):
            published_struct = tuple(int(value) for value in published[:6])
        items.append(
            ParsedItem(
                guid=_text(entry.get("id")) or url,
                url=url,
                title=_text(entry.get("title")) or "(no title)",
                body=body or _text(entry.get("summary")) or _text(entry.get("description")),
                author=_text(entry.get("author")) or None,
                published_struct=published_struct,
            )
        )
    bozo = bool(parsed.get("bozo"))
    error = parsed.get("bozo_exception")
    return ParsedFeed(items, bozo, str(error) if bozo and error else None)
