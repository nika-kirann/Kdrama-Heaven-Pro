"""Store admin uploads in the database channel and index them for search."""

from __future__ import annotations

import asyncio
import logging

from pyrogram import filters

from bot.client import FileStoreBot
from bot.config import settings
from bot.delivery import public_link
from bot.ui import NO_PREVIEW, share_markup
from bot.utils import auto_delete_line, describe_link, extract_message_id, media_info, with_flood_retry

log = logging.getLogger("kdrama.store")

MEDIA = (
    filters.document
    | filters.video
    | filters.audio
    | filters.voice
    | filters.video_note
    | filters.photo
    | filters.animation
    | filters.sticker
)
ADMINS = filters.user(list(settings.admin_ids))
_INDEX = asyncio.Lock()
_ALBUMS: dict[str, dict] = {}


async def register_message(client: FileStoreBot, message, added_by: int) -> str | None:
    """Index a database-channel message and return its public token."""
    if message is None or getattr(message, "empty", False):
        return None
    info = media_info(message)
    caption = message.caption or message.text or ""
    if info is None:
        info = {
            "name": (caption or "post")[:80] or "post",
            "size": 0,
            "type": "text",
            "mime": "",
            "unique_id": "",
        }
    async with _INDEX:
        existing = await client.db.files.find_one({"message_id": message.id})
        current = None
        if existing:
            current = await client.db.get_link(existing.get("link_token"))
        if existing and current and not current.get("revoked"):
            token = existing["link_token"]
        else:
            link = await client.db.create_link(
                message_ids=[message.id],
                kind="single",
                owner_id=added_by,
                protect=bool(client.cfg.get("protect_content")),
                expires_at=None,
                max_uses=0,
                title=info["name"],
            )
            token = link["_id"]
            if existing:
                await client.db.files.update_one({"message_id": message.id}, {"$set": {"link_token": token}})
            else:
                token = await client.db.index_file(
                    message_id=message.id,
                    info=info,
                    caption=caption,
                    added_by=added_by,
                    token=token,
                )
    await _ensure_button(client, message, token)
    return token


async def _ensure_button(client: FileStoreBot, message, token: str) -> None:
    if client.cfg.get("disable_channel_button"):
        return
    url = public_link(client.username, token)
    try:
        await message.edit_reply_markup(share_markup(url))
    except Exception:
        log.debug("channel button was not updated for %s", getattr(message, "id", "?"))


async def _copy_in(client: FileStoreBot, message):
    async def _go():
        return await message.copy(client.db_channel.id, disable_notification=True)

    return await with_flood_retry(_go)


async def _reply_link(message, client: FileStoreBot, token: str) -> None:
    link = await client.db.get_link(token)
    url = public_link(client.username, token)
    body = f"<b>Stored</b>\n{url}"
    if link:
        body += f"\n\n{describe_link(link)}"
    body += "\n" + auto_delete_line(int(client.cfg.get("file_auto_delete") or 0))
    await message.reply_text(body, reply_markup=share_markup(url), link_preview_options=NO_PREVIEW)


@FileStoreBot.on_message(filters.private & ADMINS & MEDIA)
async def store_private(client: FileStoreBot, message):
    if message.media_group_id:
        await _collect_album(client, message)
        return
    existing = extract_message_id(message, client.db_channel.id, client.db_channel.username)
    if existing:
        origin = await client.get_messages(client.db_channel.id, existing)
        token = await register_message(client, origin, message.from_user.id)
        if not token:
            await message.reply_text("That database post is empty.")
            return
        await _reply_link(message, client, token)
        return
    try:
        copied = await _copy_in(client, message)
    except Exception as exc:
        log.exception("copy into the database channel failed")
        await message.reply_text(
            f"I could not post that into the database channel ({type(exc).__name__}). "
            "Check that the bot is an admin and can post messages.",
        )
        return
    token = await register_message(client, copied, message.from_user.id)
    if token:
        await _reply_link(message, client, token)


async def _collect_album(client: FileStoreBot, message) -> None:
    group_id = str(message.media_group_id)
    bucket = _ALBUMS.setdefault(group_id, {"messages": [], "task": None})
    bucket["messages"].append(message)
    if bucket["task"] is None:
        bucket["task"] = asyncio.create_task(_flush_album(client, group_id, message.from_user.id))


async def _flush_album(client: FileStoreBot, group_id: str, user_id: int) -> None:
    await asyncio.sleep(1.2)
    bucket = _ALBUMS.pop(group_id, None)
    if not bucket:
        return
    messages = sorted(bucket["messages"], key=lambda item: item.id)
    stored_ids = []
    reply_to = messages[0]
    for item in messages:
        try:
            copied = await _copy_in(client, item)
        except Exception:
            log.exception("album copy failed")
            continue
        token = await register_message(client, copied, user_id)
        if token:
            stored_ids.append(copied.id)
    if not stored_ids:
        await reply_to.reply_text("I could not store that album.")
        return
    if len(stored_ids) == 1:
        existing = await client.db.files.find_one({"message_id": stored_ids[0]})
        token = (existing or {}).get("link_token")
        if token:
            await _reply_link(reply_to, client, token)
        return
    batch = await client.db.create_link(
        message_ids=stored_ids,
        kind="batch",
        owner_id=user_id,
        protect=bool(client.cfg.get("protect_content")),
        expires_at=None,
        max_uses=0,
        title=f"Album ({len(stored_ids)})",
    )
    url = public_link(client.username, batch["_id"])
    await reply_to.reply_text(
        f"<b>Album stored</b> ({len(stored_ids)} files)\n{url}\n\n{describe_link(batch)}\n"
        + auto_delete_line(int(client.cfg.get("file_auto_delete") or 0)),
        reply_markup=share_markup(url),
        link_preview_options=NO_PREVIEW,
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.text & ~filters.regex(r"^/"))
async def quick_link(client: FileStoreBot, message):
    msg_id = extract_message_id(message, client.db_channel.id, client.db_channel.username)
    if not msg_id:
        return
    origin = await client.get_messages(client.db_channel.id, msg_id)
    token = await register_message(client, origin, message.from_user.id)
    if not token:
        await message.reply_text("That database post is empty.")
        return
    await _reply_link(message, client, token)


@FileStoreBot.on_message(filters.chat(settings.channel_id) & filters.incoming & ~filters.service)
async def index_channel_post(client: FileStoreBot, message):
    if media_info(message) is None and not (message.text or message.caption):
        return
    try:
        await register_message(client, message, settings.owner_id)
    except Exception:
        log.exception("failed to index channel post %s", message.id)
