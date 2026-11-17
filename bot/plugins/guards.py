"""Early private-message gates: bans, then in-progress admin wizards."""

from pyrogram import StopPropagation, filters

from bot.client import FileStoreBot
from bot.config import is_admin
from bot.wizard import handle_wizard


@FileStoreBot.on_message(filters.private & filters.incoming, group=-2)
async def ban_gate(client: FileStoreBot, message):
    user = message.from_user
    if user is None or is_admin(user.id):
        return
    if user.id in client.banned:
        await message.reply_text("You are banned from this bot.")
        raise StopPropagation


@FileStoreBot.on_message(filters.private & filters.incoming, group=-1)
async def wizard_gate(client: FileStoreBot, message):
    if await handle_wizard(client, message):
        raise StopPropagation
