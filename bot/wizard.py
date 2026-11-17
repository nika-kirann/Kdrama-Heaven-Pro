"""Multi-step admin prompts for links, settings, bans, and premium."""

from __future__ import annotations

import html

from bot.config import EDITABLE, is_admin, settings
from bot.delivery import public_link
from bot.state import get_state, pop_state, set_state
from bot.ui import NO_PREVIEW, share_markup
from bot.utils import auto_delete_line, describe_link, extract_message_id, message_range, parse_limits


async def handle_wizard(client, message) -> bool:
    """Consume a private message that belongs to an admin wizard."""
    user = message.from_user
    if user is None or not is_admin(user.id):
        return False
    state = get_state(user.id)
    if not state:
        return False

    text = (message.text or "").strip()
    command = ""
    if text.startswith("/"):
        command = text.split()[0].split("@", 1)[0].lower()
    if command == "/cancel":
        pop_state(user.id)
        await message.reply_text("Cancelled.")
        return True

    flow = state.get("flow")
    if flow in {"genlink", "batch", "cbatch"}:
        return await _links(client, message, state, command)
    if flow == "setting":
        return await _setting(client, message, state)
    if flow == "ban":
        return await _ban(client, message, text)
    if flow == "unban":
        return await _unban(client, message, text)
    if flow == "premium":
        return await _premium(client, message, text)
    pop_state(user.id)
    return False


async def _links(client, message, state: dict, command: str) -> bool:
    user_id = message.from_user.id
    step = state.get("step")
    channel = client.db_channel

    if step == "collect":
        if command == "/done":
            ids = list(state.get("ids") or [])
            if not ids:
                await message.reply_text("Add at least one post, or /cancel.")
                return True
            set_state(user_id, flow="cbatch", step="limits", ids=ids, protect=False)
            await message.reply_text(_limits_prompt())
            return True
        if command:
            pop_state(user_id)
            return False
        msg_id = extract_message_id(message, channel.id, channel.username)
        if not msg_id:
            await message.reply_text(
                "That post is not from the database channel. Forward it with the forward tag, "
                "or paste its t.me link. Send /done when you are finished."
            )
            return True
        ids = list(state.get("ids") or [])
        if msg_id not in ids:
            if len(ids) >= settings.max_batch:
                await message.reply_text(f"The batch limit is {settings.max_batch}. Send /done.")
                return True
            ids.append(msg_id)
        set_state(user_id, flow="cbatch", step="collect", ids=ids, protect=False)
        await message.reply_text(
            f"Added post <code>{msg_id}</code>. Total: <b>{len(ids)}</b>. Send more, or /done."
        )
        return True

    if step in {"post", "first", "second"}:
        if command:
            pop_state(user_id)
            return False
        msg_id = extract_message_id(message, channel.id, channel.username)
        if not msg_id:
            await message.reply_text(
                "I could not match that to the database channel. Forward the post or paste its link."
            )
            return True
        if step == "post":
            set_state(user_id, flow="genlink", step="limits", ids=[msg_id], protect=False)
            await message.reply_text(_limits_prompt())
            return True
        if step == "first":
            set_state(
                user_id,
                flow="batch",
                step="second",
                first=msg_id,
                protect=bool(state.get("protect")),
            )
            await message.reply_text("Now send the <b>last</b> post in the range.")
            return True
        try:
            ids = message_range(int(state["first"]), msg_id, settings.max_batch)
        except ValueError as exc:
            await message.reply_text(str(exc))
            return True
        set_state(user_id, flow="batch", step="limits", ids=ids, protect=bool(state.get("protect")))
        await message.reply_text(_limits_prompt())
        return True

    if step == "limits":
        if command and command != "/skip":
            pop_state(user_id)
            return False
        try:
            expires, uses = parse_limits("0 0" if command == "/skip" else (message.text or ""))
        except ValueError as exc:
            await message.reply_text(f"{exc}\n\n{_limits_prompt()}")
            return True
        pop_state(user_id)
        kind = "single"
        if state.get("flow") == "cbatch":
            kind = "custom"
        elif len(state["ids"]) > 1:
            kind = "batch"
        if state.get("protect"):
            kind = "restricted"
        link = await client.db.create_link(
            message_ids=list(state["ids"]),
            kind=kind,
            owner_id=user_id,
            protect=bool(state.get("protect") or client.cfg.get("protect_content")),
            expires_at=expires,
            max_uses=uses,
        )
        await _send_link(message, client.username, link)
        return True

    pop_state(user_id)
    return False


def _limits_prompt() -> str:
    return (
        "Send limits as <code>expiry uses</code>.\n"
        "Examples: <code>24h 10</code>, <code>7d 0</code>, <code>30m 1</code>, or <code>0 0</code>.\n"
        "/skip means no expiry and no open limit. /cancel aborts."
    )


async def _send_link(message, username: str, link: dict) -> None:
    url = public_link(username, link["_id"])
    await message.reply_text(
        f"<b>Link ready</b>\n{url}\n\n{describe_link(link)}\n"
        + auto_delete_line(int(client.cfg.get("file_auto_delete") or 0)),
        reply_markup=share_markup(url),
        link_preview_options=NO_PREVIEW,
    )


async def _setting(client, message, state: dict) -> bool:
    key = state.get("key")
    kind = EDITABLE.get(key)
    text = (message.text or "").strip()
    if text.startswith("/") and text.split()[0].split("@", 1)[0].lower() not in {"/skip"}:
        pop_state(message.from_user.id)
        return False
    if kind == "pic":
        value = _read_pic(message)
        if value is None:
            await message.reply_text("Send a photo, an http(s) URL, or /skip to clear the picture.")
            return True
    elif kind == "int":
        if not text.isdigit():
            await message.reply_text("Send the auto-delete time in seconds. Use 0 to turn it off.")
            return True
        value = int(text)
        if value > 7 * 24 * 3600:
            await message.reply_text("Keep the timer at 7 days (604800 seconds) or less.")
            return True
    else:
        if text.lower() in {"/skip", "off", "clear", "-"}:
            value = ""
        elif not text:
            await message.reply_text("Send the new text, or /skip to clear it.")
            return True
        elif len(text) > 3500:
            await message.reply_text("That text is too long.")
            return True
        else:
            value = message.text
    pop_state(message.from_user.id)
    await client.db.set_runtime(key, value)
    client.cfg[key] = value
    await message.reply_text("Saved. /settings shows the current values.")
    return True


def _read_pic(message) -> str | None:
    if message.photo:
        return message.photo.file_id
    text = (message.text or "").strip()
    if text.lower() in {"/skip", "off", "clear", "-"}:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return None


async def _ban(client, message, text: str) -> bool:
    if text.startswith("/"):
        pop_state(message.from_user.id)
        return False
    parts = text.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await message.reply_text("Send a numeric user id and an optional reason, or /cancel.")
        return True
    pop_state(message.from_user.id)
    await apply_ban(client, message, int(parts[0]), parts[1] if len(parts) > 1 else "")
    return True


async def _unban(client, message, text: str) -> bool:
    if text.startswith("/"):
        pop_state(message.from_user.id)
        return False
    if not text.isdigit():
        await message.reply_text("Send the user id to unban, or /cancel.")
        return True
    pop_state(message.from_user.id)
    await apply_unban(client, message, int(text))
    return True


async def _premium(client, message, text: str) -> bool:
    if text.startswith("/"):
        pop_state(message.from_user.id)
        return False
    pop_state(message.from_user.id)
    await apply_premium_text(client, message, text)
    return True


async def apply_ban(client, message, user_id: int, reason: str) -> None:
    if user_id in settings.admin_ids:
        await message.reply_text("Admins cannot be banned.")
        return
    await client.db.set_banned(user_id, True, reason.strip())
    client.banned.add(user_id)
    extra = f"\nReason: {html.escape(reason.strip())}" if reason.strip() else ""
    await message.reply_text(f"Banned <code>{user_id}</code>.{extra}")


async def apply_unban(client, message, user_id: int) -> None:
    await client.db.set_banned(user_id, False, "")
    client.banned.discard(user_id)
    await message.reply_text(f"Unbanned <code>{user_id}</code>.")


async def apply_premium_text(client, message, text: str) -> None:
    parts = (text or "").split()
    if len(parts) < 2 or parts[0].lower() not in {"add", "del", "delete", "remove"} or not parts[1].isdigit():
        await message.reply_text(
            "Send <code>add USER_ID [days]</code> or <code>del USER_ID</code>.\n"
            "Leave days out for premium that does not expire."
        )
        return
    user_id = int(parts[1])
    if parts[0].lower() == "add":
        days = None
        if len(parts) >= 3:
            if not parts[2].isdigit() or int(parts[2]) <= 0 or int(parts[2]) > 3650:
                await message.reply_text("Days must be a number from 1 to 3650.")
                return
            days = int(parts[2])
        await client.db.set_premium(user_id, True, days)
        span = f"{days} days" if days else "no expiry"
        await message.reply_text(f"Premium is on for <code>{user_id}</code> ({span}).")
        return
    await client.db.set_premium(user_id, False, None)
    await message.reply_text(f"Premium removed from <code>{user_id}</code>.")
