"""Small helpers shared by handlers. No network calls except flood-wait retries."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from pyrogram.errors import FloodWait

log = logging.getLogger("kdrama.utils")

_DURATION = re.compile(r"^(\d+)\s*([mhd])$", re.I)
_POST_LINK = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/(?:c/)?([A-Za-z0-9_]+)/(\d+)",
    re.I,
)


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def human_size(num: int | None) -> str:
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def human_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec and not days:
        parts.append(f"{sec}s")
    return " ".join(parts) or "0s"


def uptime_string(started: datetime) -> str:
    started = aware(started) or utcnow()
    return human_duration(int((utcnow() - started).total_seconds()))


def parse_duration(value: str) -> timedelta:
    match = _DURATION.match(value.strip())
    if not match:
        raise ValueError("use a duration like 30m, 12h, or 7d")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError("duration must be greater than zero")
    unit = match.group(2).lower()
    if unit == "m":
        delta = timedelta(minutes=amount)
    elif unit == "h":
        delta = timedelta(hours=amount)
    else:
        delta = timedelta(days=amount)
    if delta > timedelta(days=365):
        raise ValueError("duration cannot be longer than 365 days")
    return delta


def parse_limits(text: str) -> tuple[datetime | None, int]:
    """Parse `<expiry> <uses>`.

    `0`, `skip`, or empty means unlimited. A single duration sets the expiry
    and leaves uses unlimited. `24h 10` expires in 24 hours after 10 opens.
    """
    cleaned = (text or "").strip()
    if cleaned == "" or cleaned.lower() in {"0", "skip", "none", "unlimited", "/skip"}:
        return None, 0
    parts = cleaned.split()
    expiry = _one_expiry(parts[0])
    uses = 0
    if len(parts) >= 2:
        if not parts[1].isdigit():
            raise ValueError("max uses must be a number (0 = unlimited)")
        uses = int(parts[1])
        if uses < 0 or uses > 1_000_000:
            raise ValueError("max uses must be between 0 and 1000000")
    if len(parts) > 2:
        raise ValueError("send `<expiry> <uses>`, for example `24h 10` or `0 0`")
    return expiry, uses


def _one_expiry(token: str) -> datetime | None:
    if token.lower() in {"0", "skip", "none", "unlimited"}:
        return None
    return utcnow() + parse_duration(token)


def format_user_text(template: str, user, *, extra: dict | None = None) -> str:
    values = SafeDict(
        first=html.escape(user.first_name or ""),
        last=html.escape(user.last_name or ""),
        id=user.id,
        mention=user.mention,
        username=f"@{user.username}" if user.username else "none",
    )
    if extra:
        values.update(extra)
    try:
        return str(template).format_map(values)
    except Exception:
        log.warning("could not format template", exc_info=True)
        return str(template)


def media_info(message) -> dict | None:
    for kind in ("document", "video", "audio", "voice", "video_note", "animation", "sticker", "photo"):
        media = getattr(message, kind, None)
        if not media:
            continue
        name = getattr(media, "file_name", None) or ("photo.jpg" if kind == "photo" else kind)
        return {
            "type": kind,
            "name": name,
            "size": int(getattr(media, "file_size", 0) or 0),
            "mime": getattr(media, "mime_type", "") or "",
            "unique_id": getattr(media, "file_unique_id", "") or "",
        }
    return None


def render_caption(message, template: str) -> str | None:
    """Return a caption override, or None to keep the original caption."""
    template = (template or "").strip()
    if not template:
        return None
    info = media_info(message) or {"name": "file", "size": 0, "type": "file"}
    previous = ""
    if message.caption:
        previous = message.html_text or html.escape(message.caption)
    try:
        rendered = template.format_map(
            SafeDict(
                filename=html.escape(info["name"]),
                previouscaption=previous,
                filesize=human_size(info["size"]),
                filetype=html.escape(info["type"]),
            )
        )
    except Exception:
        log.warning("custom caption failed", exc_info=True)
        return None
    return rendered[:1024]


def extract_message_id(message, channel_id: int, channel_username: str | None) -> int | None:
    """Return a database-channel message id from a forward or a t.me link."""
    origin = getattr(message, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
        origin_id = getattr(origin, "message_id", None)
        if chat is not None and origin_id and int(chat.id) == int(channel_id):
            return int(origin_id)
        if chat is not None or getattr(origin, "sender_user_name", None):
            return None
    text = (message.text or message.caption or "").strip()
    match = _POST_LINK.search(text)
    if not match:
        return None
    ident, mid_text = match.group(1), match.group(2)
    if ident.isdigit():
        if int(f"-100{ident}") == int(channel_id):
            return int(mid_text)
        return None
    if channel_username and ident.lower() == channel_username.lstrip("@").lower():
        return int(mid_text)
    return None


def message_range(start: int, end: int, limit: int) -> list[int]:
    count = abs(end - start) + 1
    if count > limit:
        raise ValueError(f"that range is {count} posts, and the limit is {limit}")
    if start <= end:
        return list(range(start, end + 1))
    return list(range(start, end - 1, -1))


def clip(text: str, length: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= length:
        return text
    return text[: max(0, length - 1)] + "…"


def describe_link(link: dict) -> str:
    expires = aware(link.get("expires_at"))
    if expires is None:
        expiry = "never"
    else:
        expiry = expires.strftime("%Y-%m-%d %H:%M UTC")
    uses = int(link.get("max_uses") or 0)
    use_text = "unlimited" if uses == 0 else str(uses)
    opened = int(link.get("uses") or 0)
    protect = "on" if link.get("protect") else "off"
    kind = html.escape(str(link.get("kind") or "single"))
    return (
        f"Type: <b>{kind}</b>\n"
        f"Posts: <b>{len(link.get('message_ids') or [])}</b>\n"
        f"Expires: <b>{expiry}</b>\n"
        f"Max opens: <b>{use_text}</b> (opened {opened})\n"
        f"Protect content: <b>{protect}</b>"
    )


class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        bucket = self._hits[user_id]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True


async def with_flood_retry(factory, attempts: int = 6):
    """Call an async factory, sleeping through Telegram flood waits."""
    last = None
    for _ in range(attempts):
        try:
            return await factory()
        except FloodWait as exc:
            wait = int(getattr(exc, "value", None) or getattr(exc, "seconds", None) or 1)
            wait = min(wait, 120)
            log.warning("flood wait %ss", wait)
            await asyncio.sleep(wait + 1)
            last = exc
    if last:
        raise last
    raise RuntimeError("flood retry failed")
