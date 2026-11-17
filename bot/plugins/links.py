"""Admin wizards that build single, batch, custom, and protected links."""

from pyrogram import filters

from bot.client import FileStoreBot
from bot.config import settings
from bot.state import set_state

ADMINS = filters.user(list(settings.admin_ids))


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("genlink"))
async def genlink_command(_client: FileStoreBot, message):
    set_state(message.from_user.id, flow="genlink", step="post")
    await message.reply_text(
        "Forward one database-channel post, or paste its t.me link.\n"
        "You will then set an expiry and an open limit. /cancel stops this.",
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("batch"))
async def batch_command(_client: FileStoreBot, message):
    set_state(message.from_user.id, flow="batch", step="first", protect=False)
    await message.reply_text(
        "Forward the <b>first</b> database-channel post of the range, or paste its link.\n/cancel stops this.",
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("rbatch"))
async def restricted_batch_command(_client: FileStoreBot, message):
    set_state(message.from_user.id, flow="batch", step="first", protect=True)
    await message.reply_text(
        "Protected batch. Forward the <b>first</b> post, then the last.\n"
        "Files from this link are sent with content protection. /cancel stops this.",
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("cbatch"))
async def custom_batch_command(_client: FileStoreBot, message):
    set_state(message.from_user.id, flow="cbatch", step="collect", ids=[], protect=False)
    await message.reply_text(
        "Forward the posts you want, or paste their links, one at a time.\n"
        "Send /done when the list is complete. /cancel stops this.",
    )


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("delink"))
async def delink_command(client: FileStoreBot, message):
    if len(message.command) < 2:
        await message.reply_text("Usage: /delink <code>token</code> or the full t.me link.")
        return
    token = message.command[1]
    if "start=" in token:
        token = token.split("start=", 1)[1].split("&", 1)[0]
    if await client.db.revoke_link(token.strip()):
        await message.reply_text("That link is revoked. Existing copies people already received are unchanged.")
    else:
        await message.reply_text("No link uses that token.")


@FileStoreBot.on_message(filters.private & ADMINS & filters.command("cancel"))
async def cancel_command(_client: FileStoreBot, message):
    await message.reply_text("Nothing is waiting for input.")
