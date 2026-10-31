"""収集・正規化・Discord通知の公開API。"""

from radar_core._version import __version__
from radar_core.discord import (
    AsyncDiscordClient,
    DiscordClient,
    MessageResult,
    SendResult,
    build_embed,
    build_payloads,
    clip,
    mask_webhook_url,
    normalize_embed,
    parse_retry_after,
    split_for_discord,
)
from radar_core.fetch import FetchResult, RobotsCache, fetch_feed
from radar_core.normalize import (
    compute_body_hash,
    make_snippet,
    normalize_published,
    normalize_url,
    strip_html,
)
from radar_core.parse import ParsedFeed, ParsedItem, parse_feed

__all__ = [
    "__version__",
    "AsyncDiscordClient",
    "DiscordClient",
    "FetchResult",
    "MessageResult",
    "ParsedFeed",
    "ParsedItem",
    "RobotsCache",
    "SendResult",
    "build_embed",
    "build_payloads",
    "clip",
    "compute_body_hash",
    "fetch_feed",
    "make_snippet",
    "mask_webhook_url",
    "normalize_embed",
    "normalize_published",
    "normalize_url",
    "parse_feed",
    "parse_retry_after",
    "split_for_discord",
    "strip_html",
]
