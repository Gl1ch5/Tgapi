"""Public REST API (/v1). Auth: `Authorization: Bearer tgk_...`."""
import mimetypes
import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from telethon import TelegramClient, types
from telethon.tl import functions

from . import db, tl
from .auth import Principal, api_principal
from .telegram import entity_json, manager, message_json

router = APIRouter(prefix="/v1")


# ------------------------------------------------------------ helpers

class Ctx:
    def __init__(self, principal: Principal, account_id: int, client: TelegramClient):
        self.p, self.account_id, self.client = principal, account_id, client


async def ctx(
    request: Request,
    p: Principal = Depends(api_principal),
    account: int | None = Query(None, description="Account id (when the key is not bound to one account)"),
    x_account_id: int | None = Header(None),
) -> Ctx:
    wanted = p.key_account_id or account or x_account_id
    if p.key_account_id and (account or x_account_id) and (account or x_account_id) != p.key_account_id:
        raise HTTPException(403, "this API key is bound to another account")
    if wanted:
        row = db.one("SELECT id FROM accounts WHERE id=? AND user_id=?", (wanted, p.user_id))
    else:
        row = db.one("SELECT id FROM accounts WHERE user_id=? ORDER BY id LIMIT 1", (p.user_id,))
    if not row:
        raise HTTPException(404, "no linked Telegram account — add one in the panel")
    try:
        client = await manager.get(row["id"])
    except Exception as e:
        raise HTTPException(503, f"account {row['id']} is unavailable: {e}")
    request.state.account_id = row["id"]
    return Ctx(p, row["id"], client)


def peer_of(value: str | int | None):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    v = value.strip()
    if v.lstrip("-").isdigit():
        return int(v)
    return v


async def resolve_entity(c: Ctx, peer):
    try:
        return await c.client.get_entity(peer_of(peer))
    except ValueError as e:
        raise HTTPException(404, f"peer not found: {e}")


FILTERS = {
    "photos": types.InputMessagesFilterPhotos, "videos": types.InputMessagesFilterVideo,
    "photo_video": types.InputMessagesFilterPhotoVideo, "documents": types.InputMessagesFilterDocument,
    "links": types.InputMessagesFilterUrl, "gifs": types.InputMessagesFilterGif,
    "voice": types.InputMessagesFilterVoice, "music": types.InputMessagesFilterMusic,
    "round": types.InputMessagesFilterRoundVideo, "mentions": types.InputMessagesFilterMyMentions,
    "pinned": types.InputMessagesFilterPinned, "geo": types.InputMessagesFilterGeo,
    "contacts": types.InputMessagesFilterContacts,
}
FilterName = Literal["photos", "videos", "photo_video", "documents", "links", "gifs", "voice", "music",
                     "round", "mentions", "pinned", "geo", "contacts"]


def _ts(value: int | str | None):
    if value is None:
        return None
    if isinstance(value, int) or str(value).isdigit():
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _msgs(messages) -> list[dict]:
    return [message_json(m, getattr(m, "chat", None)) for m in messages if m is not None]


# ------------------------------------------------------------ raw MTProto

class InvokeIn(BaseModel):
    method: str = Field(description="MTProto method, e.g. `messages.sendMessage`", examples=["messages.getHistory"])
    params: dict[str, Any] = Field(default_factory=dict, examples=[{"peer": "durov", "limit": 10}])


@router.post("/invoke", tags=["mtproto"], summary="Call ANY MTProto method")
async def invoke(body: InvokeIn, c: Ctx = Depends(ctx)):
    """Calls any of the ~820 Telegram API methods by name.

    * Peers (`peer`, `user_id`, `channel`, …) accept `"@username"`, `"username"`, phone, numeric id or `"me"`.
    * Nested objects: `{"_": "inputMessagesFilterPhotos"}`.
    * Bytes: plain string (utf-8), `"base64:...."`, or a list of ints.
    * Dates: unix time or ISO 8601.
    """
    try:
        req = tl.build_request(body.method, body.params)
    except (tl.TLBuildError, TypeError, ValueError) as e:
        raise HTTPException(400, str(e))
    result = await c.client(req)
    return {"method": body.method, "result": tl.to_json(result)}


@router.get("/methods", tags=["mtproto"], summary="List every MTProto method")
def methods(q: str | None = None, namespace: str | None = None, p: Principal = Depends(api_principal)):
    cat = tl.catalog()
    items = cat["methods"]
    if namespace:
        items = [m for m in items if m["namespace"] == namespace]
    if q:
        ql = q.lower()
        items = [m for m in items if ql in m["name"].lower()]
    return {"layer": cat["layer"], "count": len(items), "methods": items}


@router.get("/methods/{name}", tags=["mtproto"], summary="Describe one MTProto method")
def method(name: str, p: Principal = Depends(api_principal)):
    for m in tl.catalog()["methods"]:
        if m["name"].lower() == name.lower():
            return m
    raise HTTPException(404, "unknown method")


@router.get("/types/{name}", tags=["mtproto"], summary="Describe a TL type or constructor")
def tl_type(name: str, p: Principal = Depends(api_principal)):
    d = tl.describe_type(name)
    if not d:
        raise HTTPException(404, "unknown type")
    return d


# ------------------------------------------------------------ account

@router.get("/me", tags=["account"], summary="Current Telegram user")
async def me(c: Ctx = Depends(ctx)):
    return entity_json(await c.client.get_me()) | {"account_id": c.account_id}


@router.get("/accounts", tags=["account"], summary="Telegram accounts available to this key")
def accounts(p: Principal = Depends(api_principal)):
    if p.key_account_id:
        rows = db.q("SELECT id, phone, tg_id, username, first_name FROM accounts WHERE id=?", (p.key_account_id,))
    else:
        rows = db.q("SELECT id, phone, tg_id, username, first_name FROM accounts WHERE user_id=?", (p.user_id,))
    return [r | {"status": manager.status(r["id"])} for r in rows]


class ProfileIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    about: str | None = None
    username: str | None = None


@router.post("/profile", tags=["account"], summary="Update name, bio, username")
async def profile(body: ProfileIn, c: Ctx = Depends(ctx)):
    if any(v is not None for v in (body.first_name, body.last_name, body.about)):
        await c.client(functions.account.UpdateProfileRequest(
            first_name=body.first_name, last_name=body.last_name, about=body.about))
    if body.username is not None:
        await c.client(functions.account.UpdateUsernameRequest(body.username))
    return entity_json(await c.client.get_me())


@router.post("/profile/photo", tags=["account"], summary="Upload a new profile photo")
async def profile_photo(file: UploadFile = File(...), c: Ctx = Depends(ctx)):
    uploaded = await c.client.upload_file(await file.read(), file_name=file.filename or "photo.jpg")
    res = await c.client(functions.photos.UploadProfilePhotoRequest(file=uploaded))
    return {"ok": True, "photo_id": res.photo.id}


# ------------------------------------------------------------ chats

@router.get("/dialogs", tags=["chats"], summary="List chats / dialogs")
async def dialogs(limit: int = Query(100, le=1000), archived: bool | None = None,
                  only: Literal["users", "groups", "channels"] | None = None, c: Ctx = Depends(ctx)):
    folder = None if archived is None else (1 if archived else 0)
    out = []
    async for d in c.client.iter_dialogs(limit=limit, folder=folder):
        if only == "users" and not d.is_user or only == "groups" and not d.is_group \
                or only == "channels" and not d.is_channel or (only == "channels" and d.is_group):
            continue
        out.append(entity_json(d.entity) | {
            "dialog_id": d.id, "name": d.name, "unread_count": d.unread_count, "pinned": d.pinned,
            "archived": d.archived, "last_message": message_json(d.message)})
    return out


@router.get("/entity/{peer}", tags=["chats"], summary="Resolve a user / chat / channel")
async def entity(peer: str, full: bool = False, c: Ctx = Depends(ctx)):
    e = await resolve_entity(c, peer)
    out = entity_json(e)
    if full:
        if isinstance(e, types.User):
            res = await c.client(functions.users.GetFullUserRequest(e))
        elif isinstance(e, types.Channel):
            res = await c.client(functions.channels.GetFullChannelRequest(e))
        else:
            res = await c.client(functions.messages.GetFullChatRequest(e.id))
        out["full"] = tl.to_json(res.full_user if isinstance(e, types.User) else res.full_chat)
    return out


@router.get("/chats/{peer}/participants", tags=["chats"], summary="Members of a group / channel")
async def participants(peer: str, limit: int = Query(200, le=10000), search: str = "",
                       admins: bool = False, c: Ctx = Depends(ctx)):
    e = await resolve_entity(c, peer)
    flt = types.ChannelParticipantsAdmins() if admins else None
    users = await c.client.get_participants(e, limit=limit, search=search, filter=flt)
    return {"total": users.total, "items": [entity_json(u) for u in users]}


class JoinIn(BaseModel):
    peer: str = Field(description="@channel, t.me/channel or an invite link t.me/+hash")


@router.post("/chats/join", tags=["chats"], summary="Join a channel / group (also invite links)")
async def join(body: JoinIn, c: Ctx = Depends(ctx)):
    p = body.peer.strip()
    for marker in ("t.me/+", "t.me/joinchat/", "telegram.me/joinchat/"):
        if marker in p:
            res = await c.client(functions.messages.ImportChatInviteRequest(p.split(marker, 1)[1].strip("/")))
            return {"ok": True, "chats": [entity_json(x) for x in res.chats]}
    e = await resolve_entity(c, p)
    res = await c.client(functions.channels.JoinChannelRequest(e))
    return {"ok": True, "chats": [entity_json(x) for x in res.chats]}


class PeerIn(BaseModel):
    peer: str


@router.post("/chats/leave", tags=["chats"], summary="Leave a chat / channel and delete the dialog")
async def leave(body: PeerIn, c: Ctx = Depends(ctx)):
    await c.client.delete_dialog(peer_of(body.peer))
    return {"ok": True}


class CreateChatIn(BaseModel):
    title: str
    about: str = ""
    kind: Literal["channel", "supergroup"] = "supergroup"


@router.post("/chats/create", tags=["chats"], summary="Create a channel or supergroup")
async def create_chat(body: CreateChatIn, c: Ctx = Depends(ctx)):
    res = await c.client(functions.channels.CreateChannelRequest(
        title=body.title, about=body.about, megagroup=body.kind == "supergroup", broadcast=body.kind == "channel"))
    return {"chats": [entity_json(x) for x in res.chats]}


@router.get("/contacts", tags=["chats"], summary="Contact list")
async def contacts(c: Ctx = Depends(ctx)):
    res = await c.client(functions.contacts.GetContactsRequest(hash=0))
    return [entity_json(u) for u in res.users]


# ------------------------------------------------------------ messages

@router.get("/messages/{peer}", tags=["messages"], summary="Chat history / channel post parser")
async def history(
    peer: str,
    limit: int = Query(50, ge=1, le=5000),
    offset_id: int = 0, min_id: int = 0, max_id: int = 0,
    offset_date: str | None = Query(None, description="unix time or ISO date: messages before this date"),
    search: str | None = None,
    from_user: str | None = None,
    filter: FilterName | None = None,
    reverse: bool = Query(False, description="oldest first"),
    raw: bool = Query(False, description="include the full TL object for each message"),
    c: Ctx = Depends(ctx),
):
    e = await resolve_entity(c, peer)
    msgs = await c.client.get_messages(
        e, limit=limit, offset_id=offset_id, min_id=min_id, max_id=max_id, offset_date=_ts(offset_date),
        search=search, from_user=peer_of(from_user), filter=FILTERS[filter] if filter else None, reverse=reverse)
    items = [message_json(m, e) | ({"raw": tl.to_json(m)} if raw else {}) for m in msgs]
    return {"chat": entity_json(e), "count": len(items), "total": getattr(msgs, "total", None),
            "next_offset_id": items[-1]["id"] if items and not reverse else None, "messages": items}


@router.get("/messages/{peer}/{msg_id}", tags=["messages"], summary="One message (with comments)")
async def get_message(peer: str, msg_id: int, comments: int = Query(0, le=500), c: Ctx = Depends(ctx)):
    e = await resolve_entity(c, peer)
    m = await c.client.get_messages(e, ids=msg_id)
    if not m:
        raise HTTPException(404, "message not found")
    out = message_json(m, e) | {"raw": tl.to_json(m)}
    if comments:
        try:
            out["comments"] = _msgs(await c.client.get_messages(e, reply_to=msg_id, limit=comments))
        except Exception as ex:
            out["comments_error"] = str(ex)
    return out


class SendIn(BaseModel):
    peer: str
    text: str
    parse_mode: Literal["md", "html", "none"] = "md"
    reply_to: int | None = None
    silent: bool = False
    link_preview: bool = True
    schedule: int | str | None = Field(None, description="unix time / ISO date to schedule")
    file_url: str | None = Field(None, description="optional media URL to send with the text as caption")


@router.post("/messages/send", tags=["messages"], summary="Send a text message")
async def send(body: SendIn, c: Ctx = Depends(ctx)):
    pm = None if body.parse_mode == "none" else body.parse_mode
    target = peer_of(body.peer)
    if body.file_url:
        m = await c.client.send_file(target, body.file_url, caption=body.text, parse_mode=pm,
                                     reply_to=body.reply_to, silent=body.silent, schedule=_ts(body.schedule))
    else:
        m = await c.client.send_message(target, body.text, parse_mode=pm, reply_to=body.reply_to,
                                        silent=body.silent, link_preview=body.link_preview, schedule=_ts(body.schedule))
    return message_json(m)


@router.post("/messages/send-file", tags=["messages"], summary="Send photo / video / document (multipart)")
async def send_file(peer: str = Form(...), file: UploadFile = File(...), caption: str = Form(""),
                    force_document: bool = Form(False), voice_note: bool = Form(False),
                    reply_to: int | None = Form(None), c: Ctx = Depends(ctx)):
    data = await file.read()
    uploaded = await c.client.upload_file(data, file_name=file.filename or "file")
    m = await c.client.send_file(peer_of(peer), uploaded, caption=caption, force_document=force_document,
                                 voice_note=voice_note, reply_to=reply_to)
    return message_json(m)


class EditIn(BaseModel):
    peer: str
    id: int
    text: str
    parse_mode: Literal["md", "html", "none"] = "md"


@router.post("/messages/edit", tags=["messages"], summary="Edit a message")
async def edit(body: EditIn, c: Ctx = Depends(ctx)):
    m = await c.client.edit_message(peer_of(body.peer), body.id, body.text,
                                    parse_mode=None if body.parse_mode == "none" else body.parse_mode)
    return message_json(m)


class IdsIn(BaseModel):
    peer: str
    ids: list[int]
    revoke: bool = True


@router.post("/messages/delete", tags=["messages"], summary="Delete messages")
async def delete(body: IdsIn, c: Ctx = Depends(ctx)):
    res = await c.client.delete_messages(peer_of(body.peer), body.ids, revoke=body.revoke)
    return {"deleted": sum(r.pts_count for r in res)}


class ForwardIn(BaseModel):
    from_peer: str
    to_peer: str
    ids: list[int]
    drop_author: bool = False
    silent: bool = False


@router.post("/messages/forward", tags=["messages"], summary="Forward messages")
async def forward(body: ForwardIn, c: Ctx = Depends(ctx)):
    res = await c.client.forward_messages(peer_of(body.to_peer), body.ids, peer_of(body.from_peer),
                                          silent=body.silent, drop_author=body.drop_author)
    return _msgs(res if isinstance(res, list) else [res])


class ReadIn(BaseModel):
    peer: str
    max_id: int | None = None


@router.post("/messages/read", tags=["messages"], summary="Mark chat as read")
async def mark_read(body: ReadIn, c: Ctx = Depends(ctx)):
    await c.client.send_read_acknowledge(peer_of(body.peer), max_id=body.max_id)
    return {"ok": True}


class PinIn(BaseModel):
    peer: str
    id: int
    unpin: bool = False
    notify: bool = False


@router.post("/messages/pin", tags=["messages"], summary="Pin / unpin a message")
async def pin(body: PinIn, c: Ctx = Depends(ctx)):
    if body.unpin:
        await c.client.unpin_message(peer_of(body.peer), body.id)
    else:
        await c.client.pin_message(peer_of(body.peer), body.id, notify=body.notify)
    return {"ok": True}


class ReactIn(BaseModel):
    peer: str
    id: int
    emoji: str | None = Field("👍", description="empty / null removes the reaction")
    big: bool = False


@router.post("/messages/react", tags=["messages"], summary="Set a reaction")
async def react(body: ReactIn, c: Ctx = Depends(ctx)):
    reaction = [types.ReactionEmoji(emoticon=body.emoji)] if body.emoji else []
    await c.client(functions.messages.SendReactionRequest(
        peer=peer_of(body.peer), msg_id=body.id, reaction=reaction, big=body.big))
    return {"ok": True}


class TypingIn(BaseModel):
    peer: str
    action: Literal["typing", "record-audio", "record-video", "photo", "document", "cancel"] = "typing"


@router.post("/messages/typing", tags=["messages"], summary="Send a chat action (typing…)")
async def typing(body: TypingIn, c: Ctx = Depends(ctx)):
    actions = {"typing": types.SendMessageTypingAction(), "record-audio": types.SendMessageRecordAudioAction(),
               "record-video": types.SendMessageRecordVideoAction(),
               "photo": types.SendMessageUploadPhotoAction(progress=0),
               "document": types.SendMessageUploadDocumentAction(progress=0),
               "cancel": types.SendMessageCancelAction()}
    await c.client(functions.messages.SetTypingRequest(peer=peer_of(body.peer), action=actions[body.action]))
    return {"ok": True}


@router.get("/media/{peer}/{msg_id}", tags=["messages"], summary="Download media of a message")
async def media(peer: str, msg_id: int, thumb: bool = False, c: Ctx = Depends(ctx)):
    e = await resolve_entity(c, peer)
    m = await c.client.get_messages(e, ids=msg_id)
    if not m or not m.media:
        raise HTTPException(404, "message has no media")
    data = await c.client.download_media(m, file=bytes, thumb=-1 if thumb else None)
    if data is None:
        raise HTTPException(404, "nothing to download")
    name = (m.file.name if m.file and m.file.name else f"{msg_id}{(m.file.ext if m.file else '') or ''}")
    mime = (m.file.mime_type if m.file else None) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    if thumb:
        mime, name = "image/jpeg", f"{msg_id}_thumb.jpg"
    return Response(data, media_type=mime, headers={"Content-Disposition": f'inline; filename="{name}"'})


# ------------------------------------------------------------ search

@router.get("/search/messages", tags=["search"], summary="Search messages (global or inside one chat)")
async def search_messages(q: str, peer: str | None = None, limit: int = Query(50, le=1000),
                          filter: FilterName | None = None, c: Ctx = Depends(ctx)):
    e = await resolve_entity(c, peer) if peer else None
    msgs = await c.client.get_messages(e, search=q, limit=limit, filter=FILTERS[filter] if filter else None)
    return {"count": len(msgs), "messages": [
        message_json(m, e or m.chat) | {"chat": entity_json(e or m.chat)} for m in msgs]}


@router.get("/search/posts", tags=["search"], summary="Search posts across public channels")
async def search_posts(q: str, limit: int = Query(50, le=100), min_date: str | None = None,
                       max_date: str | None = None, c: Ctx = Depends(ctx)):
    res = await c.client(functions.messages.SearchGlobalRequest(
        q=q, filter=types.InputMessagesFilterEmpty(), min_date=_ts(min_date), max_date=_ts(max_date),
        offset_rate=0, offset_peer=types.InputPeerEmpty(), offset_id=0, limit=limit, broadcasts_only=True))
    chats = {ch.id: ch for ch in res.chats}
    out = []
    for m in res.messages:
        ch = chats.get(getattr(m.peer_id, "channel_id", None))
        item = message_json(m, ch) if hasattr(m, "message") else {"id": m.id}
        if ch is not None and getattr(ch, "username", None):
            item["link"] = f"https://t.me/{ch.username}/{m.id}"
        out.append(item | {"chat": entity_json(ch)})
    return {"count": len(out), "messages": out}


@router.get("/search/chats", tags=["search"], summary="Find public channels, groups and users")
async def search_chats(q: str, limit: int = Query(50, le=100), c: Ctx = Depends(ctx)):
    res = await c.client(functions.contacts.SearchRequest(q=q, limit=limit))
    return {"chats": [entity_json(x) for x in res.chats], "users": [entity_json(x) for x in res.users]}


# ------------------------------------------------------------ updates

@router.get("/updates", tags=["updates"], summary="Long-poll incoming updates (like getUpdates)")
async def updates(offset: int = 0, limit: int = Query(100, le=1000), timeout: int = Query(25, le=60),
                  types_: str | None = Query(None, alias="types", description="comma list, e.g. UpdateNewMessage"),
                  c: Ctx = Depends(ctx)):
    items = await manager.buffer(c.account_id).get(offset, limit, timeout)
    if types_:
        wanted = {t.strip() for t in types_.split(",")}
        items = [u for u in items if u["type"] in wanted]
    return {"updates": items}


# ------------------------------------------------------------ automation

class AutoReplyIn(BaseModel):
    name: str = ""
    match_type: Literal["contains", "exact", "startswith", "regex", "any"] = "contains"
    pattern: str = Field("", description="for 'contains' — comma separated keywords")
    chat_type: Literal["private", "group", "channel", "all"] = "private"
    reply: str = Field(description="reply text; {name} and {text} are substituted")
    cooldown: int = Field(0, description="seconds before the rule fires again in the same chat")
    enabled: bool = True


@router.get("/autoreplies", tags=["automation"], summary="List autoreply rules")
def list_autoreplies(c: Ctx = Depends(ctx)):
    return db.q("SELECT * FROM autoreplies WHERE account_id=? ORDER BY id", (c.account_id,))


@router.post("/autoreplies", tags=["automation"], summary="Create an autoreply rule")
def create_autoreply(body: AutoReplyIn, c: Ctx = Depends(ctx)):
    rid = db.ex("INSERT INTO autoreplies(account_id, name, match_type, pattern, chat_type, reply, cooldown, enabled, "
                "created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (c.account_id, body.name, body.match_type, body.pattern, body.chat_type, body.reply, body.cooldown,
                 int(body.enabled), db.now()))
    return db.one("SELECT * FROM autoreplies WHERE id=?", (rid,))


@router.put("/autoreplies/{rid}", tags=["automation"], summary="Update an autoreply rule")
def update_autoreply(rid: int, body: AutoReplyIn, c: Ctx = Depends(ctx)):
    db.ex("UPDATE autoreplies SET name=?, match_type=?, pattern=?, chat_type=?, reply=?, cooldown=?, enabled=? "
          "WHERE id=? AND account_id=?",
          (body.name, body.match_type, body.pattern, body.chat_type, body.reply, body.cooldown, int(body.enabled),
           rid, c.account_id))
    return db.one("SELECT * FROM autoreplies WHERE id=? AND account_id=?", (rid, c.account_id))


@router.delete("/autoreplies/{rid}", tags=["automation"], summary="Delete an autoreply rule")
def delete_autoreply(rid: int, c: Ctx = Depends(ctx)):
    db.ex("DELETE FROM autoreplies WHERE id=? AND account_id=?", (rid, c.account_id))
    return {"ok": True}


class WebhookIn(BaseModel):
    url: str = Field(pattern=r"^https?://")
    events: str = Field("", description="comma separated update types, empty = all")
    enabled: bool = True


@router.get("/webhooks", tags=["automation"], summary="List webhooks")
def list_webhooks(c: Ctx = Depends(ctx)):
    return db.q("SELECT * FROM webhooks WHERE account_id=? ORDER BY id", (c.account_id,))


@router.post("/webhooks", tags=["automation"], summary="Create a webhook (updates are POSTed, HMAC-signed)")
def create_webhook(body: WebhookIn, c: Ctx = Depends(ctx)):
    wid = db.ex("INSERT INTO webhooks(account_id, url, secret, events, enabled, created_at) VALUES(?,?,?,?,?,?)",
                (c.account_id, body.url, "whsec_" + secrets.token_urlsafe(24), body.events, int(body.enabled),
                 db.now()))
    return db.one("SELECT * FROM webhooks WHERE id=?", (wid,))


@router.delete("/webhooks/{wid}", tags=["automation"], summary="Delete a webhook")
def delete_webhook(wid: int, c: Ctx = Depends(ctx)):
    db.ex("DELETE FROM webhooks WHERE id=? AND account_id=?", (wid, c.account_id))
    return {"ok": True}
