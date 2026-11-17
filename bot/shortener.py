"""Optional ad-link shortener used by the token verification gate.

The shortener URL may be either a plain endpoint (the bot sends `api` and `url`
query parameters) or a template containing `{api}` and `{url}`.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import aiohttp

from bot.config import settings

log = logging.getLogger("kdrama.shortener")

_KEYS = ("shortenedUrl", "shortened_url", "short_url", "shorturl", "short", "url")


def _pick(payload) -> str | None:
    if isinstance(payload, str) and payload.startswith("http"):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in _KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    for nested in ("data", "result", "response"):
        found = _pick(payload.get(nested))
        if found:
            return found
    return None


async def shorten(long_url: str) -> str:
    api = settings.shortener_api
    base = settings.shortener_url
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if "{url}" in base or "{api}" in base:
            endpoint = base.format(url=quote(long_url, safe=""), api=quote(api, safe=""))
            async with session.get(endpoint) as response:
                body = await response.json(content_type=None)
                status = response.status
        else:
            async with session.get(base, params={"api": api, "url": long_url}) as response:
                body = await response.json(content_type=None)
                status = response.status
    short = _pick(body)
    if not short:
        log.error("shortener returned HTTP %s without a short url", status)
        raise RuntimeError("link shortener did not return a short url")
    return short
