"""Telegram user-client manager: one Telethon client per linked account."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field

import httpx
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl import types

from . import db
from .config import APP_VERSION, DEVICE_MODEL, SYSTEM_VERSION
from .security import decrypt, encrypt
from .tl import to_json

log = logging.getLogger("tgapi.telegram")


def api_credentials() -> tuple[int, str]:
    api_id = os.getenv("TG_API_ID") or db.get_setting("tg_api_id")
    api_hash = os.getenv("TG_API_HASH") or db.get_setting("tg_api_hash")
    if not api_id or not api_hash:
        raise RuntimeError("Telegram api_id/api_hash are not configured (Settings or TG_API_ID/TG_API_HASH env)")
    return int(api_id), api_hash


def make_client(session: str = "") -> TelegramClient:
    api_id, api_hash = api_credentials()
    return TelegramClient(
        StringSession(session), api_id, api_hash,
        device_model=DEVICE_MODEL, system_version=SYSTEM_VERSION, app_version=APP_VERSION,
        flood_sleep_threshold=0,
    )


# ---------------------------------------------------------------- updates

@dataclass
class UpdateBuffer:
    items: deque = field(default_factory=lambda: deque(maxlen=2000))
    last_id: int = 0
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def push(self, payload: dict) -> dict:
        async with self.cond:
            self.last_id += 1
            item = {"update_id": self.last_id, "ts": int(time.time()), **payload}
            self.items.append(item)
            self.cond.notify_all()
            return item

    async def get(self, offset: int, limit: int, timeout: float) -> list[dict]:
        def ready():
            return [u for u in self.items if u["update_id"] > offset][:limit]

        res = ready()
        if res or timeout <= 0:
            return res
        async with self.cond:
            try:
                await asyncio.wait_for(self.cond.wait_for(lambda: bool(ready())), timeout)
            except asyncio.TimeoutError:
                pass
        return ready()


def chat_kind(chat) -> str:
    if isinstance(chat, types.User):
        return "private"
    if isinstance(chat, types.Channel):
        return "group" if chat.megagroup else "channel"
    return "group"


def message_json(msg, chat=None) -> dict:
    """Compact, stable message representation."""
    if msg is None:
        return {}
    media = None
    if msg.media:
        media = type(msg.media).__name__.replace("MessageMedia", "").lower()
        if msg.file:
            media = {"type": media, "name": msg.file.name, "mime": msg.file.mime_type, "size": msg.file.size}
        else:
            media = {"type": media}
    chat_id = msg.chat_id
    link = None
    if chat is not None and getattr(chat, "username", None) and isinstance(chat, types.Channel):
        link = f"https://t.me/{chat.username}/{msg.id}"
    reactions = None
    if getattr(msg, "reactions", None) and msg.reactions.results:
        reactions = [
            {"emoji": getattr(r.reaction, "emoticon", None) or getattr(r.reaction, "document_id", None), "count": r.count}
            for r in msg.reactions.results
        ]
    return {
        "id": msg.id,
        "chat_id": chat_id,
        "sender_id": msg.sender_id,
        "date": msg.date.isoformat() if msg.date else None,
        "edit_date": msg.edit_date.isoformat() if getattr(msg, "edit_date", None) else None,
        "out": bool(msg.out),
        "text": msg.message if hasattr(msg, "message") else None,
        "reply_to": msg.reply_to_msg_id if hasattr(msg, "reply_to_msg_id") else None,
        "grouped_id": getattr(msg, "grouped_id", None),
        "views": getattr(msg, "views", None),
        "forwards": getattr(msg, "forwards", None),
        "replies": msg.replies.replies if getattr(msg, "replies", None) else None,
        "reactions": reactions,
        "post_author": getattr(msg, "post_author", None),
        "fwd_from": to_json(msg.fwd_from) if getattr(msg, "fwd_from", None) else None,
        "media": media,
        "action": type(msg.action).__name__ if getattr(msg, "action", None) else None,
        "link": link,
    }


def entity_json(e) -> dict:
    if e is None:
        return {}
    d = {"id": e.id, "type": type(e).__name__.lower()}
    if isinstance(e, types.User):
        d.update(type="user", first_name=e.first_name, last_name=e.last_name, username=e.username,
                 phone=e.phone, bot=bool(e.bot), premium=bool(getattr(e, "premium", False)),
                 verified=bool(e.verified))
    elif isinstance(e, types.Channel):
        d.update(type="supergroup" if e.megagroup else "channel", title=e.title, username=e.username,
                 participants_count=getattr(e, "participants_count", None), verified=bool(e.verified),
                 broadcast=bool(e.broadcast))
    elif isinstance(e, types.Chat):
        d.update(type="group", title=e.title, participants_count=getattr(e, "participants_count", None))
    else:
        d.update(title=getattr(e, "title", None))
    return d


# ---------------------------------------------------------------- manager

class Manager:
    def __init__(self):
        self.clients: dict[int, TelegramClient] = {}
        self.buffers: dict[int, UpdateBuffer] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._cooldowns: dict[tuple[int, int], float] = {}
        self.logins: dict[str, "PendingLogin"] = {}
        self.http = httpx.AsyncClient(timeout=10)

    # ---- lifecycle
    async def start_all(self):
        for acc in db.q("SELECT id FROM accounts"):
            try:
                await self.get(acc["id"])
            except Exception as e:  # keep the panel alive even if one session is dead
                log.warning("account %s failed to start: %s", acc["id"], e)

    async def stop_all(self):
        for c in list(self.clients.values()):
            await c.disconnect()
        for p in list(self.logins.values()):
            await p.close()
        await self.http.aclose()

    def buffer(self, account_id: int) -> UpdateBuffer:
        return self.buffers.setdefault(account_id, UpdateBuffer())

    async def get(self, account_id: int) -> TelegramClient:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            client = self.clients.get(account_id)
            if client and client.is_connected():
                return client
            row = db.one("SELECT * FROM accounts WHERE id=?", (account_id,))
            if not row:
                raise KeyError("account not found")
            client = client or make_client(decrypt(row["session"]))
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                self.clients.pop(account_id, None)
                raise RuntimeError("session is no longer authorized — re-link the account")
            if account_id not in self.clients:
                self._attach_handlers(account_id, client)
                self.clients[account_id] = client
            return client

    async def remove(self, account_id: int, logout: bool = False):
        client = self.clients.pop(account_id, None)
        if client:
            if logout:
                try:
                    await client.log_out()
                except Exception:
                    pass
            await client.disconnect()
        self.buffers.pop(account_id, None)

    def status(self, account_id: int) -> str:
        c = self.clients.get(account_id)
        return "online" if c and c.is_connected() else "offline"

    # ---- events
    def _attach_handlers(self, account_id: int, client: TelegramClient):
        @client.on(events.Raw)
        async def on_raw(update):
            payload = {"type": type(update).__name__, "update": to_json(update)}
            item = await self.buffer(account_id).push(payload)
            await self._dispatch_webhooks(account_id, item)

        @client.on(events.NewMessage(incoming=True))
        async def on_message(event):
            try:
                await self._autoreply(account_id, event)
            except Exception as e:
                log.warning("autoreply failed: %s", e)

    async def _autoreply(self, account_id: int, event):
        rules = db.q("SELECT * FROM autoreplies WHERE account_id=? AND enabled=1 ORDER BY id", (account_id,))
        if not rules:
            return
        chat = await event.get_chat()
        sender = await event.get_sender()
        if isinstance(sender, types.User) and (sender.bot or sender.is_self):
            return
        kind = chat_kind(chat)
        text = event.raw_text or ""
        for r in rules:
            if r["chat_type"] != "all" and r["chat_type"] != kind:
                continue
            if not _matches(r["match_type"], r["pattern"], text):
                continue
            key = (r["id"], event.chat_id)
            if r["cooldown"] and time.monotonic() - self._cooldowns.get(key, -1e12) < r["cooldown"]:
                return
            self._cooldowns[key] = time.monotonic()
            reply = r["reply"].replace("{name}", getattr(sender, "first_name", None) or "").replace("{text}", text)
            await event.reply(reply)
            return

    async def _dispatch_webhooks(self, account_id: int, item: dict):
        hooks = db.q("SELECT * FROM webhooks WHERE account_id=? AND enabled=1", (account_id,))
        for h in hooks:
            wanted = [e.strip() for e in h["events"].split(",") if e.strip()]
            if wanted and item["type"] not in wanted:
                continue
            body = json.dumps({"account_id": account_id, **item}, ensure_ascii=False).encode()
            sig = hmac.new(h["secret"].encode(), body, hashlib.sha256).hexdigest()
            asyncio.create_task(self._post(h["url"], body, sig))

    async def _post(self, url: str, body: bytes, sig: str):
        try:
            await self.http.post(url, content=body, headers={
                "Content-Type": "application/json", "X-TgApi-Signature": f"sha256={sig}"})
        except Exception as e:
            log.info("webhook %s failed: %s", url, e)

    # ---- login flows
    def new_login(self, user_id: int) -> "PendingLogin":
        # drop stale flows
        for k, p in list(self.logins.items()):
            if time.time() - p.created > 900:
                asyncio.create_task(p.close())
                self.logins.pop(k, None)
        p = PendingLogin(id=secrets.token_urlsafe(16), user_id=user_id, client=make_client())
        self.logins[p.id] = p
        return p

    def login(self, login_id: str, user_id: int) -> "PendingLogin":
        p = self.logins.get(login_id)
        if not p or p.user_id != user_id:
            raise KeyError("login flow not found or expired")
        return p

    async def finish_login(self, p: "PendingLogin") -> int:
        me = await p.client.get_me()
        session = p.client.session.save()
        existing = db.one("SELECT id FROM accounts WHERE user_id=? AND tg_id=?", (p.user_id, me.id))
        if existing:
            await self.remove(existing["id"])
            db.ex("UPDATE accounts SET session=?, phone=?, username=?, first_name=?, last_name=? WHERE id=?",
                  (encrypt(session), me.phone, me.username, me.first_name, me.last_name, existing["id"]))
            account_id = existing["id"]
        else:
            account_id = db.ex(
                "INSERT INTO accounts(user_id, phone, tg_id, username, first_name, last_name, session, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (p.user_id, me.phone, me.id, me.username, me.first_name, me.last_name, encrypt(session), db.now()))
        # Hand the live connection over to the manager.
        self._attach_handlers(account_id, p.client)
        self.clients[account_id] = p.client
        p.client = None  # the flow stays in self.logins so the panel can poll the final state
        return account_id


def _matches(match_type: str, pattern: str, text: str) -> bool:
    t, p = text.lower(), pattern.lower()
    if match_type == "any":
        return True
    if match_type == "exact":
        return t.strip() == p.strip()
    if match_type == "startswith":
        return t.startswith(p)
    if match_type == "regex":
        try:
            return re.search(pattern, text, re.I) is not None
        except re.error:
            return False
    keywords = [k.strip() for k in p.split(",") if k.strip()]
    return any(k in t for k in keywords) if keywords else False


@dataclass
class PendingLogin:
    id: str
    user_id: int
    client: TelegramClient | None
    phone: str | None = None
    phone_code_hash: str | None = None
    state: str = "new"  # new | code_sent | password | qr | done | error
    qr: object = None
    qr_url: str | None = None
    qr_task: asyncio.Task | None = None
    account_id: int | None = None
    error: str | None = None
    password_hint: str | None = None
    created: float = field(default_factory=time.time)

    async def close(self):
        if self.qr_task:
            self.qr_task.cancel()
        if self.client:
            await self.client.disconnect()

    async def ensure(self):
        if self.client is None:
            raise RuntimeError("login flow already finished")
        if not self.client.is_connected():
            await self.client.connect()

    async def send_code(self, phone: str):
        await self.ensure()
        sent = await self.client.send_code_request(phone)
        self.phone, self.phone_code_hash, self.state = phone, sent.phone_code_hash, "code_sent"
        return type(sent.type).__name__.replace("SentCodeType", "").lower()

    async def submit_code(self, code: str) -> bool:
        """True when signed in, False when the cloud password (2FA) is required."""
        try:
            await self.client.sign_in(self.phone, code, phone_code_hash=self.phone_code_hash)
            return True
        except errors.SessionPasswordNeededError:
            await self._to_password()
            return False

    async def _to_password(self):
        self.state = "password"
        try:
            from telethon.tl.functions.account import GetPasswordRequest
            pw = await self.client(GetPasswordRequest())
            self.password_hint = pw.hint
        except Exception:
            pass

    async def submit_password(self, password: str):
        await self.client.sign_in(password=password)

    async def start_qr(self, manager: Manager):
        await self.ensure()
        self.qr = await self.client.qr_login()
        self.qr_url, self.state = self.qr.url, "qr"

        async def waiter():
            while True:
                try:
                    await self.qr.wait(timeout=25)
                    self.account_id = await manager.finish_login(self)
                    self.state = "done"
                    return
                except asyncio.TimeoutError:
                    await self.qr.recreate()
                    self.qr_url = self.qr.url
                except errors.SessionPasswordNeededError:
                    await self._to_password()
                    return
                except Exception as e:
                    self.state, self.error = "error", str(e)
                    return

        self.qr_task = asyncio.create_task(waiter())


manager = Manager()
