"""MongoDB access via Motor. Collections are created on first use."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from bot.config import EDITABLE, Settings
from bot.utils import aware, utcnow


def apply_runtime(base: dict, stored: dict | None) -> dict:
    """Overlay /settings values from MongoDB onto the environment defaults.

    A stored ``file_auto_delete`` of ``0`` is intentional and turns the timer off,
    even when ``FILE_AUTO_DELETE`` is set in the environment.
    """
    cfg = dict(base)
    stored = stored or {}
    for key in EDITABLE:
        if key in stored and stored[key] is not None:
            cfg[key] = stored[key]
    cfg["file_auto_delete"] = int(cfg.get("file_auto_delete") or 0)
    for flag in ("protect_content", "disable_channel_button", "shortener_enabled"):
        cfg[flag] = bool(cfg.get(flag))
    return cfg


def autodelete_source(stored: dict | None) -> str:
    stored = stored or {}
    if "file_auto_delete" in stored and stored.get("file_auto_delete") is not None:
        return "mongo"
    return "env"

log = logging.getLogger("kdrama.db")


def new_token(prefix: str) -> str:
    # Telegram start payloads allow only A-Z, a-z, 0-9, "_" and "-".
    raw = secrets.token_urlsafe(8).replace("=", "")
    return prefix + raw


class Database:
    def __init__(self, url: str, name: str):
        self._url = url
        self._name = name
        self.client = None
        self.db = None

    async def connect(self) -> None:
        from motor.motor_asyncio import AsyncIOMotorClient

        self.client = AsyncIOMotorClient(
            self._url,
            tz_aware=True,
            serverSelectionTimeoutMS=8000,
        )
        await self.client.admin.command("ping")
        self.db = self.client[self._name]
        await self._indexes()
        log.info("connected to MongoDB database %s", self._name)

    async def _indexes(self) -> None:
        await self.db.files.create_index("message_id", unique=True)
        await self.db.files.create_index("name_lc")
        await self.db.files.create_index([("added_at", -1)])
        await self.db.users.create_index("banned")
        await self.db.requests.create_index([("status", 1), ("created_at", -1)])
        await self.db.verifications.create_index("created_at", expireAfterSeconds=86_400)
        await self.db.join_requests.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
        await self.db.deletions.create_index("due_at")

    @property
    def users(self):
        return self.db.users

    @property
    def files(self):
        return self.db.files

    @property
    def links(self):
        return self.db.links

    @property
    def runtime(self):
        return self.db.runtime

    @property
    def requests(self):
        return self.db.requests

    @property
    def verifications(self):
        return self.db.verifications

    @property
    def join_requests(self):
        return self.db.join_requests

    @property
    def deletions(self):
        return self.db.deletions

    async def load_config(self, settings: Settings) -> dict:
        doc = await self.runtime.find_one({"_id": "runtime"}) or {}
        self.file_auto_delete_source = autodelete_source(doc)
        return apply_runtime(settings.overlay(), doc)

    async def add_deletion(self, doc: dict) -> dict:
        if "_id" not in doc:
            doc["_id"] = new_token("d")
        await self.deletions.insert_one(doc)
        return doc

    async def pending_deletions(self) -> list[dict]:
        cursor = self.deletions.find({})
        return [doc async for doc in cursor]

    async def mark_files_deleted(self, deletion_id: str) -> None:
        await self.deletions.update_one({"_id": deletion_id}, {"$set": {"files_deleted": True}})

    async def finish_deletion(self, deletion_id: str) -> None:
        await self.deletions.delete_one({"_id": deletion_id})

    async def set_runtime(self, key: str, value) -> None:
        await self.runtime.update_one({"_id": "runtime"}, {"$set": {key: value}}, upsert=True)

    async def touch_user(self, user) -> bool:
        """Upsert a user. Returns True when this is the first time we see them."""
        now = utcnow()
        existing = await self.users.find_one({"_id": user.id}, {"_id": 1})
        await self.users.update_one(
            {"_id": user.id},
            {
                "$set": {
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "username": user.username,
                    "last_seen": now,
                    "blocked": False,
                },
                "$setOnInsert": {
                    "joined_at": now,
                    "banned": False,
                    "premium": False,
                    "premium_until": None,
                    "verified_until": None,
                },
            },
            upsert=True,
        )
        return existing is None

    async def user_ids(self, *, skip_banned: bool = False) -> list[int]:
        query = {"blocked": {"$ne": True}}
        if skip_banned:
            query["banned"] = {"$ne": True}
        cursor = self.users.find(query, {"_id": 1})
        return [doc["_id"] async for doc in cursor]

    async def banned_ids(self) -> list[int]:
        cursor = self.users.find({"banned": True}, {"_id": 1})
        return [doc["_id"] async for doc in cursor]

    async def count_users(self) -> dict:
        total = await self.users.count_documents({})
        banned = await self.users.count_documents({"banned": True})
        blocked = await self.users.count_documents({"blocked": True})
        premium = await self.users.count_documents({"premium": True})
        return {"total": total, "banned": banned, "blocked": blocked, "premium": premium}

    async def set_banned(self, user_id: int, banned: bool, reason: str = "") -> None:
        await self.users.update_one(
            {"_id": user_id},
            {
                "$set": {"banned": banned, "ban_reason": reason if banned else ""},
                "$setOnInsert": {"joined_at": utcnow(), "premium": False, "blocked": False},
            },
            upsert=True,
        )

    async def set_blocked(self, user_id: int, blocked: bool) -> None:
        await self.users.update_one({"_id": user_id}, {"$set": {"blocked": blocked}})

    async def is_premium(self, user_id: int) -> bool:
        doc = await self.users.find_one({"_id": user_id}, {"premium": 1, "premium_until": 1})
        if not doc or not doc.get("premium"):
            return False
        until = aware(doc.get("premium_until"))
        if until is None:
            return True
        if until <= utcnow():
            await self.users.update_one({"_id": user_id}, {"$set": {"premium": False}})
            return False
        return True

    async def set_premium(self, user_id: int, enabled: bool, days: int | None) -> None:
        until = None
        if enabled and days:
            until = utcnow() + timedelta(days=days)
        await self.users.update_one(
            {"_id": user_id},
            {
                "$set": {"premium": enabled, "premium_until": until if enabled else None},
                "$setOnInsert": {"joined_at": utcnow(), "banned": False, "blocked": False},
            },
            upsert=True,
        )

    async def premium_users(self, limit: int = 30) -> list[dict]:
        cursor = self.users.find({"premium": True}).sort("last_seen", -1).limit(limit)
        return [doc async for doc in cursor]

    async def is_verified(self, user_id: int) -> bool:
        doc = await self.users.find_one({"_id": user_id}, {"verified_until": 1})
        until = aware((doc or {}).get("verified_until"))
        return bool(until and until > utcnow())

    async def verify_user(self, user_id: int, hours: int) -> datetime:
        until = utcnow() + timedelta(hours=hours)
        await self.users.update_one({"_id": user_id}, {"$set": {"verified_until": until}}, upsert=True)
        return until

    async def create_link(
        self,
        *,
        message_ids: list[int],
        kind: str,
        owner_id: int,
        protect: bool,
        expires_at: datetime | None,
        max_uses: int,
        title: str = "",
    ) -> dict:
        for _ in range(5):
            token = new_token("f")
            doc = {
                "_id": token,
                "message_ids": [int(i) for i in message_ids],
                "kind": kind,
                "owner_id": int(owner_id),
                "protect": bool(protect),
                "created_at": utcnow(),
                "expires_at": expires_at,
                "max_uses": int(max_uses or 0),
                "uses": 0,
                "title": title[:120],
                "revoked": False,
            }
            try:
                await self.links.insert_one(doc)
                return doc
            except DuplicateKeyError:
                continue
        raise RuntimeError("could not allocate a unique link token")

    async def get_link(self, token: str) -> dict | None:
        return await self.links.find_one({"_id": token})

    async def consume_link(self, token: str) -> tuple[dict | None, str]:
        link, status = await self.peek_link(token, enforce_uses=True)
        if status != "ok" or link is None:
            return link, status
        max_uses = int(link.get("max_uses") or 0)
        if max_uses:
            updated = await self.links.find_one_and_update(
                {"_id": token, "revoked": {"$ne": True}, "uses": {"$lt": max_uses}},
                {"$inc": {"uses": 1}, "$set": {"last_access": utcnow()}},
                return_document=ReturnDocument.AFTER,
            )
            if not updated:
                return link, "limit"
            return updated, "ok"
        await self.links.update_one(
            {"_id": token},
            {"$inc": {"uses": 1}, "$set": {"last_access": utcnow()}},
        )
        link["uses"] = int(link.get("uses") or 0) + 1
        return link, "ok"

    async def peek_link(self, token: str, *, enforce_uses: bool = True) -> tuple[dict | None, str]:
        link = await self.get_link(token)
        if not link:
            return None, "missing"
        if link.get("revoked"):
            return link, "revoked"
        expires = aware(link.get("expires_at"))
        if expires and expires <= utcnow():
            return link, "expired"
        max_uses = int(link.get("max_uses") or 0)
        if enforce_uses and max_uses and int(link.get("uses") or 0) >= max_uses:
            return link, "limit"
        return link, "ok"

    async def release_use(self, token: str) -> None:
        await self.links.update_one({"_id": token, "uses": {"$gt": 0}}, {"$inc": {"uses": -1}})

    async def revoke_link(self, token: str) -> bool:
        result = await self.links.update_one({"_id": token}, {"$set": {"revoked": True}})
        return result.matched_count > 0

    async def index_file(self, *, message_id: int, info: dict, caption: str, added_by: int, token: str) -> str:
        name = info["name"]
        doc = {
            "message_id": int(message_id),
            "file_name": name,
            "name_lc": name.lower(),
            "file_size": info["size"],
            "file_type": info["type"],
            "mime": info["mime"],
            "unique_id": info["unique_id"],
            "caption": (caption or "")[:1000],
            "caption_lc": (caption or "")[:1000].lower(),
            "link_token": token,
            "added_by": int(added_by),
            "added_at": utcnow(),
        }
        try:
            await self.files.insert_one(doc)
            return token
        except DuplicateKeyError:
            existing = await self.files.find_one({"message_id": int(message_id)})
            return (existing or {}).get("link_token") or token

    async def search_files(self, query: str, skip: int, limit: int) -> list[dict]:
        import re

        pattern = re.escape(query.lower())
        cursor = (
            self.files.find({"$or": [{"name_lc": {"$regex": pattern}}, {"caption_lc": {"$regex": pattern}}]})
            .sort("added_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def recent_files(self, skip: int, limit: int) -> list[dict]:
        cursor = self.files.find({}).sort("added_at", -1).skip(skip).limit(limit)
        return [doc async for doc in cursor]

    async def count_files(self) -> int:
        return await self.files.count_documents({})

    async def count_links(self) -> int:
        return await self.links.count_documents({"revoked": {"$ne": True}})

    async def save_verification(self, token: str, user_id: int, file_token: str) -> None:
        await self.verifications.insert_one(
            {
                "_id": token,
                "user_id": int(user_id),
                "file_token": file_token,
                "created_at": utcnow(),
            }
        )

    async def pop_verification(self, token: str) -> dict | None:
        return await self.verifications.find_one_and_delete({"_id": token})

    async def add_join_request(self, chat_id: int, user_id: int) -> None:
        await self.join_requests.update_one(
            {"chat_id": int(chat_id), "user_id": int(user_id)},
            {"$set": {"created_at": utcnow()}},
            upsert=True,
        )

    async def has_join_request(self, chat_id: int, user_id: int) -> bool:
        found = await self.join_requests.find_one({"chat_id": int(chat_id), "user_id": int(user_id)})
        return found is not None

    async def add_request(self, user, text: str) -> dict:
        doc = {
            "_id": secrets.token_hex(4),
            "user_id": user.id,
            "name": user.first_name or "",
            "username": user.username,
            "text": text[:300],
            "status": "pending",
            "created_at": utcnow(),
        }
        await self.requests.insert_one(doc)
        return doc

    async def recent_request_count(self, user_id: int, since: datetime) -> int:
        return await self.requests.count_documents({"user_id": user_id, "created_at": {"$gte": since}})

    async def pending_requests(self, limit: int = 10) -> list[dict]:
        cursor = self.requests.find({"status": "pending"}).sort("created_at", 1).limit(limit)
        return [doc async for doc in cursor]

    async def count_requests(self, status: str | None = None) -> int:
        query = {"status": status} if status else {}
        return await self.requests.count_documents(query)

    async def user_requests(self, user_id: int, limit: int = 8) -> list[dict]:
        cursor = self.requests.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
        return [doc async for doc in cursor]

    async def set_request_status(self, request_id: str, status: str) -> dict | None:
        return await self.requests.find_one_and_update(
            {"_id": request_id},
            {"$set": {"status": status, "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
