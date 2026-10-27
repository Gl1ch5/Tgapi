"""Panel accounts (password + TOTP 2FA), panel sessions and API key auth."""
import io
import secrets
import time
from dataclasses import dataclass

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import db
from .config import APP_NAME, COOKIE_SECURE, SESSION_TTL
from .security import (hash_password, login_limiter, new_token, sha256, verify_password)

COOKIE = "tgapi_session"
router = APIRouter(prefix="/panel/api/auth", tags=["panel"])


@dataclass
class Principal:
    user_id: int
    is_admin: bool
    key_id: int | None = None
    key_account_id: int | None = None
    via: str = "session"


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "?")


def _session_principal(request: Request) -> Principal | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = db.one(
        "SELECT u.id, u.is_admin FROM panel_sessions s JOIN users u ON u.id=s.user_id "
        "WHERE s.token_hash=? AND s.expires_at>?", (sha256(token), db.now()))
    return Principal(row["id"], bool(row["is_admin"])) if row else None


def _key_principal(request: Request) -> Principal | None:
    auth = request.headers.get("authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key")
    if not key:
        return None
    row = db.one(
        "SELECT k.id, k.user_id, k.account_id, u.is_admin FROM api_keys k JOIN users u ON u.id=k.user_id "
        "WHERE k.key_hash=? AND k.revoked=0", (sha256(key),))
    if not row:
        raise HTTPException(401, "invalid API key")
    db.ex("UPDATE api_keys SET last_used_at=? WHERE id=?", (db.now(), row["id"]))
    return Principal(row["user_id"], bool(row["is_admin"]), row["id"], row["account_id"], via="key")


def panel_user(request: Request) -> Principal:
    p = _session_principal(request)
    if not p:
        raise HTTPException(401, "not logged in")
    return p


def admin_user(p: Principal = Depends(panel_user)) -> Principal:
    if not p.is_admin:
        raise HTTPException(403, "admin only")
    return p


def api_principal(request: Request) -> Principal:
    """API key (Bearer / X-API-Key), or a panel session so the explorer can try requests."""
    p = _key_principal(request) or _session_principal(request)
    if not p:
        raise HTTPException(401, "missing API key: send 'Authorization: Bearer tgk_...'")
    request.state.principal = p
    return p


# ------------------------------------------------------------------ routes

class SetupIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=256)
    api_id: int | None = None
    api_hash: str | None = None


class LoginIn(BaseModel):
    username: str
    password: str


class TotpIn(BaseModel):
    ticket: str
    code: str


class CodeIn(BaseModel):
    code: str


class PasswordChangeIn(BaseModel):
    current: str
    new: str = Field(min_length=8, max_length=256)


# pending password-ok logins waiting for the TOTP step: ticket -> (user_id, expires)
_tickets: dict[str, tuple[int, float]] = {}
# TOTP codes already used (replay protection): (user_id, code) -> expires
_used_codes: dict[tuple[int, str], float] = {}


def _check_totp(user: dict, code: str) -> bool:
    code = "".join(ch for ch in code if ch.isdigit())
    if not user["totp_secret"] or len(code) != 6:
        return False
    now = time.time()
    for k, exp in list(_used_codes.items()):
        if exp < now:
            _used_codes.pop(k, None)
    if (user["id"], code) in _used_codes:
        return False
    if pyotp.TOTP(user["totp_secret"]).verify(code, valid_window=1):
        _used_codes[(user["id"], code)] = now + 120
        return True
    return False


def _start_session(request: Request, response: Response, user_id: int):
    token = new_token()
    db.ex("INSERT INTO panel_sessions(token_hash, user_id, ip, user_agent, created_at, expires_at) VALUES(?,?,?,?,?,?)",
          (sha256(token), user_id, _client_ip(request), request.headers.get("user-agent", "")[:200],
           db.now(), db.now() + SESSION_TTL))
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=COOKIE_SECURE,
                        samesite="strict", path="/")


@router.get("/state")
def state(request: Request):
    p = _session_principal(request)
    user = db.one("SELECT id, username, is_admin, totp_enabled FROM users WHERE id=?", (p.user_id,)) if p else None
    return {
        "app": APP_NAME,
        "setup_required": db.one("SELECT 1 AS x FROM users") is None,
        "registration_open": db.get_setting("registration_open", "0") == "1",
        "user": user,
    }


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response):
    if db.one("SELECT 1 AS x FROM users"):
        raise HTTPException(409, "already set up")
    uid = db.ex("INSERT INTO users(username, pw_hash, is_admin, created_at) VALUES(?,?,1,?)",
                (body.username.strip(), hash_password(body.password), db.now()))
    if body.api_id and body.api_hash:
        db.set_setting("tg_api_id", str(body.api_id))
        db.set_setting("tg_api_hash", body.api_hash.strip())
    _start_session(request, response, uid)
    return {"ok": True}


@router.post("/register")
def register(body: LoginIn, request: Request, response: Response):
    if db.get_setting("registration_open", "0") != "1":
        raise HTTPException(403, "registration is closed")
    if not login_limiter.hit("reg:" + _client_ip(request)):
        raise HTTPException(429, "too many attempts")
    if len(body.username) < 3 or len(body.password) < 8:
        raise HTTPException(400, "username ≥ 3 chars, password ≥ 8 chars")
    if db.one("SELECT 1 AS x FROM users WHERE username=?", (body.username.strip(),)):
        raise HTTPException(409, "username taken")
    uid = db.ex("INSERT INTO users(username, pw_hash, created_at) VALUES(?,?,?)",
                (body.username.strip(), hash_password(body.password), db.now()))
    _start_session(request, response, uid)
    return {"ok": True}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    if not login_limiter.hit("login:" + _client_ip(request)):
        raise HTTPException(429, "too many attempts, wait a few minutes")
    user = db.one("SELECT * FROM users WHERE username=?", (body.username.strip(),))
    if not user or not verify_password(body.password, user["pw_hash"]):
        raise HTTPException(401, "wrong username or password")
    if user["totp_enabled"]:
        ticket = secrets.token_urlsafe(24)
        _tickets[ticket] = (user["id"], time.time() + 300)
        return {"totp_required": True, "ticket": ticket}
    _start_session(request, response, user["id"])
    return {"ok": True}


@router.post("/totp")
def login_totp(body: TotpIn, request: Request, response: Response):
    if not login_limiter.hit("totp:" + _client_ip(request)):
        raise HTTPException(429, "too many attempts, wait a few minutes")
    entry = _tickets.get(body.ticket)
    if not entry or entry[1] < time.time():
        _tickets.pop(body.ticket, None)
        raise HTTPException(401, "login expired, start again")
    user = db.one("SELECT * FROM users WHERE id=?", (entry[0],))
    if not _check_totp(user, body.code):
        raise HTTPException(401, "wrong code")
    _tickets.pop(body.ticket, None)
    _start_session(request, response, user["id"])
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        db.ex("DELETE FROM panel_sessions WHERE token_hash=?", (sha256(token),))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/totp/init")
def totp_init(p: Principal = Depends(panel_user)):
    user = db.one("SELECT * FROM users WHERE id=?", (p.user_id,))
    if user["totp_enabled"]:
        raise HTTPException(409, "2FA already enabled")
    secret = pyotp.random_base32()
    db.ex("UPDATE users SET totp_secret=? WHERE id=?", (secret, p.user_id))
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["username"], issuer_name=APP_NAME)
    return {"secret": secret, "uri": uri, "qr_svg": qr_svg(uri)}


@router.post("/totp/enable")
def totp_enable(body: CodeIn, p: Principal = Depends(panel_user)):
    user = db.one("SELECT * FROM users WHERE id=?", (p.user_id,))
    if not _check_totp(user, body.code):
        raise HTTPException(400, "wrong code — check the time on your phone")
    db.ex("UPDATE users SET totp_enabled=1 WHERE id=?", (p.user_id,))
    return {"ok": True}


@router.post("/totp/disable")
def totp_disable(body: CodeIn, p: Principal = Depends(panel_user)):
    user = db.one("SELECT * FROM users WHERE id=?", (p.user_id,))
    if not user["totp_enabled"] or not _check_totp(user, body.code):
        raise HTTPException(400, "wrong code")
    db.ex("UPDATE users SET totp_enabled=0, totp_secret=NULL WHERE id=?", (p.user_id,))
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordChangeIn, request: Request, p: Principal = Depends(panel_user)):
    user = db.one("SELECT * FROM users WHERE id=?", (p.user_id,))
    if not verify_password(body.current, user["pw_hash"]):
        raise HTTPException(400, "current password is wrong")
    db.ex("UPDATE users SET pw_hash=? WHERE id=?", (hash_password(body.new), p.user_id))
    # sign out every other session
    token = request.cookies.get(COOKIE, "")
    db.ex("DELETE FROM panel_sessions WHERE user_id=? AND token_hash<>?", (p.user_id, sha256(token)))
    return {"ok": True}


@router.get("/sessions")
def sessions(request: Request, p: Principal = Depends(panel_user)):
    cur = sha256(request.cookies.get(COOKIE, ""))
    rows = db.q("SELECT token_hash, ip, user_agent, created_at, expires_at FROM panel_sessions "
                "WHERE user_id=? AND expires_at>? ORDER BY created_at DESC", (p.user_id, db.now()))
    return [{"id": r["token_hash"][:16], "ip": r["ip"], "user_agent": r["user_agent"],
             "created_at": r["created_at"], "current": r["token_hash"] == cur} for r in rows]


@router.delete("/sessions/{sid}")
def kill_session(sid: str, p: Principal = Depends(panel_user)):
    db.ex("DELETE FROM panel_sessions WHERE user_id=? AND substr(token_hash,1,16)=?", (p.user_id, sid))
    return {"ok": True}
