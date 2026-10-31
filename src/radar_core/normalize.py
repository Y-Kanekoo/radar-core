"""正規化層. URL / 日付 / 本文 / ハッシュをDB保存可能な形に整える.

ai-radar 互換で、抜粋の上限は本文文字数と末尾の省略記号を別に数える。
"""

from __future__ import annotations

import calendar
import hashlib
import re
import time
from collections.abc import Iterable
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 削除する追跡パラメータ. 大文字小文字区別なし.
# Phase 1 で 10 → 19 個に拡張. `src`/`source` は一部サイトで意味のある値を持つので含めない.
TRACKING_PARAMS = frozenset(
    {
        # UTM (Google Analytics 系)
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        # 各広告プラットフォーム
        "fbclid",  # Facebook
        "gclid",  # Google Ads
        "yclid",  # Yandex
        "ref",
        "ref_src",
        "twclid",  # X / Twitter
        "msclkid",  # Microsoft Ads
        "dclid",  # DoubleClick
        # Mailchimp
        "mc_cid",
        "mc_eid",
        # HubSpot
        "_hsenc",
        "_hsmi",
        "hsctatracking",  # 小文字で比較する
        # Instagram
        "igshid",
        # Alibaba
        "spm",
    }
)

# arXiv URL から version suffix を除去するパターン.
# 例: /abs/2301.12345v2 -> /abs/2301.12345
_ARXIV_VERSION = re.compile(r"^(/abs/\d+\.\d+)v\d+/?$")


def normalize_url(url: str, *, extra_tracking_params: Iterable[str] = ()) -> str:
    """URL を正規化する (dedup 5層の層1).

    変換内容:
    1. scheme/host を小文字化
    2. 追跡パラメータ (TRACKING_PARAMS) と fragment を除去
    3. arXiv の version suffix (`v1`/`v2` 等) を除去
    4. ルート以外の末尾スラッシュを除去 (`/blog/` → `/blog`)

    空文字や scheme 無しの URL は無加工で返す (旧挙動互換).
    """
    parsed = urlparse(url)
    if not parsed.scheme:
        return url

    # 追跡パラメータ除去 (キーを lower-case で比較)
    removed = TRACKING_PARAMS | {key.lower() for key in extra_tracking_params}
    qs = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in removed]

    path = parsed.path
    netloc = parsed.netloc.lower()

    # arXiv version suffix 除去 (export.arxiv.org / arxiv.org の両方を含む)
    if netloc.endswith("arxiv.org"):
        match = _ARXIV_VERSION.match(path)
        if match:
            path = match.group(1)

    # 末尾スラッシュ統一: パスが "/" 単体でなければ末尾スラッシュを除去
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            parsed.params,
            urlencode(qs),
            "",  # fragment は削除
        )
    )


def normalize_published(struct_time: tuple[int, ...] | None) -> int:
    """published_parsed (UTC tuple) をUNIX秒に変換. None は現在時刻を返す."""
    if struct_time is None:
        return int(time.time())
    padded = struct_time + (0,) * (9 - len(struct_time))
    return calendar.timegm(padded)


class _StripTags(HTMLParser):
    """HTMLタグを除去しテキストノードのみ抽出する補助パーサ."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def strip_html(text: str) -> str:
    """HTMLタグを除去しエンティティをデコードする."""
    if not text:
        return ""
    parser = _StripTags()
    try:
        parser.feed(text)
        parser.close()
    except (ValueError, AssertionError):
        # 壊れたHTMLの場合は元テキストをそのまま返す
        return text
    return parser.get_text()


_WS = re.compile(r"\s+")


def make_snippet(body: str, max_chars: int = 100) -> str:
    """本文を既定100字に切り、必要なら省略記号を追加する。

    HTML を除去 → 連続空白を1つに → max_chars で切り詰め. 切る位置は60%以降に
    スペースが見つかればそこを単語境界として採用する (英語向け補正).

    Args:
        body: 元本文 (HTML 可).
        max_chars: 最大文字数. 既定100.

    Returns:
        抜粋. max_chars を超える場合は末尾に `…` を付ける.
    """
    if max_chars < 1:
        raise ValueError("max_chars は1以上が必要です")
    plain = strip_html(body)
    plain = _WS.sub(" ", plain).strip()
    if len(plain) <= max_chars:
        return plain
    truncated = plain[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.6:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "…"


def compute_body_hash(body: str) -> str:
    """正規化後本文の SHA256 hex digest. クロスソース重複検出用."""
    plain = _WS.sub(" ", strip_html(body)).strip()
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()
