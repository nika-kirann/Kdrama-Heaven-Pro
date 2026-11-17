"""Join-request force-subscribe bookkeeping."""

import logging

from bot.client import FileStoreBot
from bot.config import settings

log = logging.getLogger("kdrama.forcesub")


@FileStoreBot.on_chat_join_request()
async def on_join_request(client: FileStoreBot, join_request):
    chat_id = join_request.chat.id
    allowed = {channel["id"] for channel in client.force_channels if channel.get("request")}
    if chat_id not in allowed:
        return
    user = join_request.from_user
    await client.db.add_join_request(chat_id, user.id)
    query_id = getattr(join_request, "query_id", None)
    if query_id:
        from pyrogram.enums import ChatJoinRequestQueryResult

        result = (
            ChatJoinRequestQueryResult.APPROVE
            if settings.auto_approve_join
            else ChatJoinRequestQueryResult.QUEUE
        )
        try:
            await client.answer_chat_join_request_query(query_id, result)
        except Exception:
            log.warning("could not answer join request in %s", chat_id)
        return
    if not settings.auto_approve_join:
        return
    try:
        await client.approve_chat_join_request(chat_id, user.id)
    except Exception:
        log.warning("could not approve join request in %s", chat_id)
