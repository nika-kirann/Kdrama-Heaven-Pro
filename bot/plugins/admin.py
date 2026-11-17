"""Admin commands: panel, stats, bans, premium, settings, broadcast, restart."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import secrets
import sys

from pyrogram import filters
from pyrogram.errors import FloodWait, InputUserDeactivated, PeerIdInvalid, UserIsBlocked
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.client import FileStoreBot
from bot.config import is_owner, settings
from bot.state import set_state
from bot.ui import NO_PREVIEW, broadcast_markup, panel_markup, settings_markup, settings_text
from bot.utils import uptime_string, with_flood_retry
from bot.wizard import apply_ban, apply_premium_text, apply_unban

log = logging.getLogger("kdrama.admin")
ADMINS = filters.user(list(settings.admin_ids))


async def render_stats(client: FileStoreBot) -> str:
    users = await client.db.count_users()
    files = await client.db.count_files()
    links = await client.db.count_links()
    pending = await client.db.count_requests("pending")
    return (
        "<b>Stats</b>\n"
        f"Users: <b>{users['total']}</b>\n"
        f"Banned: <b>{users['banned']}</b> · Blocked the bot: <b>{users['blocked']}</b>\n"
        f"Premium: <b>{users['premium']}</b>\n"
        f"Indexed files: <b>{files}</b>\n"
        f"Active links: <b>{links}</b>\n"
        f"Open requests: <b>{pending}</b>\n"
        f"Uptime: <b>{uptime_string(client.started_at)}</b>\n"
        f"Force-sub channels: <b>{len(client.force_channels)}</b>"
    )


def restart_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Restart now", callback_data="restart:yes"),
            InlineKeyboardButton("Cancel", callback_data="close"),
        ]]
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("requests"))
async def requests_command(client: FileStoreBot, message):
    text, markup = await render_requests(client)
    await message.reply_text(text, reply_markup=markup)


@FileStoreBot.on_message(filters.private & ADMINS & filters.command(["stats", "users"]))
async def stats_command(client: FileStoreBot, message):
    wait = await message.reply_text("Collecting stats…")
    await wait.edit_text(await render_stats(client))


@FileStoreBot.on_message(filters.private & ADMINS & filters.command(["panel", "admin"]))
async def panel_command(client: FileStoreBot, message):
    await message.reply_text(
        f"<b>Admin panel</b>\nSigned in as <code>{message.from_user.id}</code>.",
        reply_markup=panel_markup(),
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("admins"))
async def admins_command(_client: FileStoreBot, message):
    lines = ["<b>Admins</b>"]
    for admin_id in settings.admin_ids:
        mark = " (owner)" if admin_id == settings.owner_id else ""
        lines.append(f"• <code>{admin_id}</code>{mark}")
    await message.reply_text("\n".join(lines))


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("settings"))
async def settings_command(client: FileStoreBot, message):
    await message.reply_text(
        settings_text(client.cfg),
        reply_markup=settings_markup(client.cfg),
        link_preview_options=NO_PREVIEW,
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("ban"))
async def ban_command(client: FileStoreBot, message):
    if len(message.command) < 2:
        set_state(message.from_user.id, flow="ban")
        await message.reply_text("Send the user id and an optional reason, or /cancel.")
        return
    reason = message.text.split(maxsplit=2)[2] if len(message.command) > 2 else ""
    if not message.command[1].isdigit():
        await message.reply_text("The user id has to be a number.")
        return
    await apply_ban(client, message, int(message.command[1]), reason)


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("unban"))
async def unban_command(client: FileStoreBot, message):
    if len(message.command) < 2:
        set_state(message.from_user.id, flow="unban")
        await message.reply_text("Send the user id to unban, or /cancel.")
        return
    if not message.command[1].isdigit():
        await message.reply_text("The user id has to be a number.")
        return
    await apply_unban(client, message, int(message.command[1]))


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("premium"))
async def premium_command(client: FileStoreBot, message):
    if len(message.command) < 2:
        set_state(message.from_user.id, flow="premium")
        await message.reply_text(
            "Send <code>add USER_ID [days]</code> or <code>del USER_ID</code>.\n"
            "Or send <code>list</code>. /cancel stops this.",
        )
        return
    if message.command[1].lower() == "list":
        people = await client.db.premium_users()
        if not people:
            await message.reply_text("No premium users.")
            return
        lines = ["<b>Premium</b>"]
        for doc in people:
            until = doc.get("premium_until")
            when = "no expiry" if until is None else until.strftime("%Y-%m-%d")
            lines.append(f"• <code>{doc['_id']}</code> ({when})")
        await message.reply_text("\n".join(lines))
        return
    await apply_premium_text(client, message, message.text.split(maxsplit=1)[1])


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("broadcast"))
async def broadcast_command(client: FileStoreBot, message):
    if message.reply_to_message is None:
        await message.reply_text("Reply to the message you want to broadcast, then send /broadcast.")
        return
    if client.broadcasting:
        await message.reply_text("A broadcast is already running.")
        return
    token = secrets.token_hex(4)
    client.broadcasts[token] = {
        "chat_id": message.chat.id,
        "message_id": message.reply_to_message.id,
        "owner": message.from_user.id,
    }
    await message.reply_text(
        "Send this message to every user who has not blocked the bot?",
        reply_markup=broadcast_markup(token),
    )


async def run_broadcast(client: FileStoreBot, progress, token: str, pin: bool) -> None:
    job = client.broadcasts.pop(token, None)
    if job is None:
        await progress.edit_text("This broadcast prompt expired. Send /broadcast again.")
        return
    if client.broadcasting:
        await progress.edit_text("A broadcast is already running.")
        return
    client.broadcasting = True
    try:
        source = await client.get_messages(job["chat_id"], job["message_id"])
        if source is None or getattr(source, "empty", False):
            await progress.edit_text("The source message is gone.")
            return
        user_ids = await client.db.user_ids(skip_banned=True)
        total = len(user_ids)
        delivered = blocked = failed = pinned = pin_failed = 0
        await progress.edit_text(f"Broadcasting to {total} users…")
        for index, user_id in enumerate(user_ids, start=1):

            async def _copy(user_id=user_id):
                return await source.copy(user_id)

            try:
                sent = await with_flood_retry(_copy)
                delivered += 1
                if pin and sent is not None:
                    try:
                        await client.pin_chat_message(user_id, sent.id, both_sides=True, disable_notification=True)
                        pinned += 1
                    except Exception:
                        pin_failed += 1
            except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
                await client.db.set_blocked(user_id, True)
                blocked += 1
            except FloodWait as exc:
                wait = int(getattr(exc, "value", None) or 5)
                await asyncio.sleep(min(wait, 120) + 1)
                failed += 1
            except Exception:
                log.warning("broadcast to %s failed", user_id, exc_info=True)
                failed += 1
            if index == total or index % 25 == 0:
                try:
                    await progress.edit_text(
                        f"Broadcasting… {index}/{total}\n"
                        f"Delivered: {delivered}\nRemoved: {blocked}\nFailed: {failed}"
                    )
                except Exception:
                    pass
        summary = (
            "<b>Broadcast finished</b>\n"
            f"Users: {total}\n"
            f"Delivered: {delivered}\n"
            f"Removed (blocked or deleted): {blocked}\n"
            f"Failed: {failed}"
        )
        if pin:
            summary += f"\nPinned: {pinned}\nPin failed: {pin_failed}"
        await progress.edit_text(summary)
    finally:
        client.broadcasting = False


def perform_restart() -> None:
    # Drop inherited sockets (the health server, Mongo, Telegram) before re-exec
    # so the new process can bind $PORT.
    try:
        limit = min(int(os.sysconf("SC_OPEN_MAX")), 65536)
    except (OSError, ValueError):
        limit = 1024
    os.closerange(3, max(limit, 1024))
    os.execv(sys.executable, [sys.executable, "-m", "bot"])


async def render_requests(client: FileStoreBot):
    rows = await client.db.pending_requests(8)
    if not rows:
        return "No open requests.", InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back", callback_data="panel:home")]]
        )
    lines = ["<b>Open requests</b>"]
    buttons = []
    for doc in rows:
        lines.append(
            f"• <code>{doc['_id']}</code> {html.escape(doc.get('text') or '')}\n"
            f"  from <code>{doc['user_id']}</code>"
        )
        buttons.append(
            [
                InlineKeyboardButton(f"Done {doc['_id']}", callback_data=f"rq:done:{doc['_id']}"),
                InlineKeyboardButton("Reject", callback_data=f"rq:no:{doc['_id']}"),
            ]
        )
    buttons.append([InlineKeyboardButton("Back", callback_data="panel:home")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def resolve_request(client: FileStoreBot, request_id: str, status: str) -> str:
    doc = await client.db.set_request_status(request_id, status)
    if not doc:
        return "That request no longer exists."
    verb = "ready" if status == "done" else "declined"
    try:
        await client.send_message(
            doc["user_id"],
            f"Your request <b>{html.escape(doc.get('text') or '')}</b> was marked {verb}.",
        )
    except Exception:
        log.info("could not notify requester %s", doc.get("user_id"))
    return f"Request <code>{request_id}</code> marked {status}."


def is_restart_allowed(user_id: int) -> bool:
    return is_owner(user_id)
