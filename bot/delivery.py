"""File delivery: force-subscribe, verification, copy, and auto-delete."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from html import escape

from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import UserNotParticipant
from bot.config import is_admin, settings
from bot.database import new_token
from bot.shortener import shorten
from bot.ui import NO_PREVIEW, again_markup, forcesub_markup, verify_markup
from bot.utils import (
    aware,
    auto_delete_label,
    format_user_text,
    human_duration,
    media_info,
    render_caption,
    utcnow,
    with_flood_retry,
)

log = logging.getLogger("kdrama.delivery")

_JOINED = {
    ChatMemberStatus.OWNER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
}


async def missing_channels(client, user_id: int) -> list[dict]:
    if is_admin(user_id):
        return []
    if settings.premium_bypass_forcesub and await client.db.is_premium(user_id):
        return []
    missing = []
    for channel in client.force_channels:
        state = await _membership(client, channel["id"], user_id)
        if state == "joined":
            continue
        if state == "absent" and channel.get("request"):
            if await client.db.has_join_request(channel["id"], user_id):
                continue
        missing.append(channel)
    return missing


async def _membership(client, chat_id: int, user_id: int) -> str:
    try:
        member = await client.get_chat_member(chat_id, user_id)
    except UserNotParticipant:
        return "absent"
    except Exception:
        log.warning("could not check membership of %s in %s", user_id, chat_id, exc_info=True)
        return "unknown"
    if member.status == ChatMemberStatus.BANNED:
        return "banned"
    if member.status in _JOINED:
        return "joined"
    return "absent"


async def gate_user(client, message, payload: str = "joined") -> bool:
    """Return True when the user may use member-only features."""
    user = message.from_user
    if user is None or is_admin(user.id):
        return True
    if client.force_channels and await missing_channels(client, user.id):
        await send_forcesub(client, message.chat.id, user, payload)
        return False
    return True


async def send_forcesub(client, chat_id: int, user, payload: str) -> None:
    missing = await missing_channels(client, user.id)
    text = format_user_text(client.cfg["force_message"], user)
    if not missing:
        missing = client.force_channels
    await client.send_message(
        chat_id,
        text,
        reply_markup=forcesub_markup(missing or client.force_channels, client.username, payload),
        link_preview_options=NO_PREVIEW,
    )


async def needs_verification(client, user_id: int) -> bool:
    if not client.cfg.get("shortener_enabled"):
        return False
    if not (settings.shortener_api and settings.shortener_url):
        return False
    if is_admin(user_id):
        return False
    if settings.premium_bypass_shortener and await client.db.is_premium(user_id):
        return False
    return not await client.db.is_verified(user_id)


async def send_verification(client, chat_id: int, user_id: int, file_token: str) -> None:
    token = new_token("v")
    await client.db.save_verification(token, user_id, file_token)
    destination = f"https://t.me/{client.username}?start={token}"
    try:
        short = await shorten(destination)
    except Exception:
        log.exception("shortener failed")
        await client.send_message(
            chat_id,
            "Link verification is enabled, but the shortener did not respond. Tell an admin.",
        )
        return
    hours = settings.token_hours
    await client.send_message(
        chat_id,
        "Open the verification link, complete the page, and you will be returned here.\n"
        f"One pass lasts <b>{hours} hours</b>. Premium accounts skip this step.",
        reply_markup=verify_markup(short, settings.verify_tutorial),
        link_preview_options=NO_PREVIEW,
    )


async def complete_verification(client, message) -> None:
    payload = message.command[1] if len(message.command) > 1 else ""
    record = await client.db.pop_verification(payload)
    if not record or int(record.get("user_id") or 0) != message.from_user.id:
        await message.reply_text("That verification link is invalid or was already used.")
        return
    created = record.get("created_at")
    if created is not None:
        from bot.utils import aware

        created = aware(created)
        if created and (utcnow() - created).total_seconds() > 6 * 3600:
            await message.reply_text("That verification link has expired. Open the file link again.")
            return
    until = await client.db.verify_user(message.from_user.id, settings.token_hours)
    await message.reply_text(
        f"Verified until <b>{until.strftime('%Y-%m-%d %H:%M UTC')}</b>. Sending the file."
    )
    await deliver_token(
        client,
        message.from_user,
        message.chat.id,
        record["file_token"],
        count_use=True,
    )


async def deliver_token(client, user, chat_id: int, token: str, *, count_use: bool) -> None:
    if not is_admin(user.id) and not client.limiter.allow(user.id):
        await client.send_message(chat_id, "Slow down a little, then try that link again.")
        return
    if client.force_channels and await missing_channels(client, user.id):
        await send_forcesub(client, chat_id, user, token)
        return
    if await needs_verification(client, user.id):
        await send_verification(client, chat_id, user.id, token)
        return

    if count_use:
        link, status = await client.db.consume_link(token)
    else:
        # Get-again recovers a delivery that already counted as an open.
        link, status = await client.db.peek_link(token, enforce_uses=False)
    if status != "ok" or link is None:
        await client.send_message(chat_id, _status_text(status))
        return

    ids = [int(i) for i in link.get("message_ids") or []]
    if not ids:
        if count_use:
            await client.db.release_use(token)
        await client.send_message(chat_id, "This link does not point at any posts.")
        return
    messages = await fetch_messages(client, ids)
    if not messages:
        if count_use:
            await client.db.release_use(token)
        await client.send_message(chat_id, "Those posts are no longer in the database channel.")
        return

    status_msg = await client.send_message(chat_id, f"Sending 1/{len(messages)}…")
    sent_ids: list[int] = []
    protect = bool(link.get("protect") or client.cfg.get("protect_content"))
    caption_template = client.cfg.get("custom_caption") or ""
    for index, source in enumerate(messages, start=1):
        caption = render_caption(source, caption_template) if media_info(source) else None

        async def _copy(source=source, caption=caption):
            kwargs = {"chat_id": chat_id, "protect_content": protect, "reply_markup": None}
            if caption is not None:
                kwargs["caption"] = caption
                kwargs["parse_mode"] = ParseMode.HTML
            return await source.copy(**kwargs)

        try:
            copied = await with_flood_retry(_copy)
        except Exception:
            log.exception("failed to copy message %s", getattr(source, "id", "?"))
            copied = None
        if copied is not None:
            sent_ids.append(copied.id)
        if index == len(messages) or index % 5 == 0:
            try:
                await status_msg.edit_text(f"Sending {index}/{len(messages)}…")
            except Exception:
                pass
        await asyncio.sleep(0.35)

    try:
        await status_msg.delete()
    except Exception:
        pass

    if not sent_ids:
        if count_use:
            await client.db.release_use(token)
        await client.send_message(chat_id, "I could not copy those files. Try again in a moment.")
        return

    delay = int(client.cfg.get("file_auto_delete") or 0)
    # Premium bypass is the only skip, and only when PREMIUM_BYPASS_AUTODELETE is on.
    # Admins and the owner follow the same timer so a test from the owner account is real.
    if settings.premium_bypass_autodelete and await client.db.is_premium(user.id):
        return
    if delay <= 0:
        return

    notice = format_user_text(
        "This file will be deleted in <b>{time}</b>. Save it to Saved Messages if you still need it.",
        user,
        extra={"time": human_duration(delay)},
    )
    warning = await client.send_message(chat_id, notice)
    doc = {
        "_id": new_token("d"),
        "chat_id": int(chat_id),
        "message_ids": list(sent_ids),
        "warning_id": int(warning.id),
        "token": token,
        "due_at": utcnow() + timedelta(seconds=delay),
        "user_id": int(user.id),
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username,
        "files_deleted": False,
    }
    try:
        await client.db.add_deletion(doc)
    except Exception:
        log.exception("could not persist auto-delete; it will still run until this process stops")
    asyncio.create_task(run_saved_deletion(client, doc))


def _user_from_deletion(doc: dict):
    name = doc.get("first_name") or "there"
    user_id = int(doc["user_id"])

    class _User:
        def __init__(self):
            self.id = user_id
            self.first_name = name
            self.last_name = doc.get("last_name") or ""
            self.username = doc.get("username")
            self.mention = f'<a href="tg://user?id={user_id}">{escape(name)}</a>'

    return _User()


async def run_saved_deletion(client, doc: dict) -> None:
    """Delete a delivered batch at ``due_at``. Safe to run again after a restart."""
    try:
        due = aware(doc.get("due_at")) or utcnow()
        delay = max(0.0, (due - utcnow()).total_seconds())
        if delay:
            await asyncio.sleep(delay)
        if not doc.get("files_deleted"):
            targets = [int(i) for i in doc.get("message_ids") or []]
            warning_id = doc.get("warning_id")
            if warning_id:
                targets.append(int(warning_id))
            chat_id = int(doc["chat_id"])
            for offset in range(0, len(targets), 100):
                chunk = targets[offset : offset + 100]

                async def _delete(chunk=chunk):
                    return await client.delete_messages(chat_id, chunk)

                try:
                    await with_flood_retry(_delete)
                except Exception:
                    log.warning("auto-delete failed in %s", chat_id, exc_info=True)
                    return
            doc["files_deleted"] = True
            try:
                await client.db.mark_files_deleted(doc["_id"])
            except Exception:
                log.exception("could not mark auto-delete %s", doc.get("_id"))
        user = _user_from_deletion(doc)
        text = format_user_text(client.cfg.get("auto_delete_text") or "Files deleted.", user)
        await client.send_message(int(doc["chat_id"]), text, reply_markup=again_markup(doc["token"]))
        try:
            await client.db.finish_deletion(doc["_id"])
        except Exception:
            log.exception("could not clear auto-delete %s", doc.get("_id"))
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("auto-delete task failed")


async def resume_autodeletes(client) -> None:
    """Restart timers that were still pending when the process stopped."""
    try:
        pending = await client.db.pending_deletions()
    except Exception:
        log.exception("could not load pending auto-deletes")
        return
    now = utcnow()
    overdue = 0
    for doc in pending:
        due = aware(doc.get("due_at"))
        if due is not None and due <= now:
            overdue += 1
        asyncio.create_task(run_saved_deletion(client, doc))
    if pending:
        log.info(
            "resumed %s auto-delete timer(s), %s already due (%s)",
            len(pending),
            overdue,
            auto_delete_label(int(client.cfg.get("file_auto_delete") or 0)),
        )


def _status_text(status: str) -> str:
    if status == "expired":
        return "This link has expired."
    if status == "limit":
        return "This link has reached its open limit."
    if status == "revoked":
        return "This link was revoked."
    return "This link does not exist."


async def fetch_messages(client, ids: list[int]):
    found = {}
    for offset in range(0, len(ids), 200):
        chunk = ids[offset : offset + 200]

        async def _get(chunk=chunk):
            return await client.get_messages(client.db_channel.id, chunk)

        result = await with_flood_retry(_get)
        messages = result if isinstance(result, list) else [result]
        for message in messages:
            if message is None or getattr(message, "empty", False):
                continue
            if media_info(message) is None and not (message.text or message.caption):
                continue
            found[message.id] = message
    return [found[i] for i in ids if i in found]


def public_link(username: str, token: str) -> str:
    return f"https://t.me/{username}?start={token}"
