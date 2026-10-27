"""Panel-only routes: Telegram account linking, API keys, settings, users, stats."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from telethon import errors

from . import db
from .auth import Principal, admin_user, panel_user, qr_svg
from .security import hash_password, new_api_key, sha256
from .telegram import manager

router = APIRouter(prefix="/panel/api", tags=["panel"])


def _account_row(r: dict) -> dict:
    return {k: r[k] for k in ("id", "phone", "tg_id", "username", "first_name", "last_name", "created_at")} | {
        "status": manager.status(r["id"])}


def owned_account(user_id: int, account_id: int) -> dict:
    row = db.one("SELECT * FROM accounts WHERE id=? AND user_id=?", (account_id, user_id))
    if not row:
        raise HTTPException(404, "account not found")
    return row


# ------------------------------------------------------------ accounts

@router.get("/accounts")
def accounts(p: Principal = Depends(panel_user)):
    return [_account_row(r) for r in db.q("SELECT * FROM accounts WHERE user_id=? ORDER BY id", (p.user_id,))]


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, logout: bool = True, p: Principal = Depends(panel_user)):
    owned_account(p.user_id, account_id)
    await manager.remove(account_id, logout=logout)
    db.ex("DELETE FROM accounts WHERE id=?", (account_id,))
    return {"ok": True}


@router.post("/accounts/{account_id}/reconnect")
async def reconnect(account_id: int, p: Principal = Depends(panel_user)):
    owned_account(p.user_id, account_id)
    await manager.remove(account_id)
    try:
        await manager.get(account_id)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"status": manager.status(account_id)}


class PhoneIn(BaseModel):
    phone: str


class LoginCodeIn(BaseModel):
    code: str


class LoginPasswordIn(BaseModel):
    password: str


def _flow_state(f) -> dict:
    return {"login_id": f.id, "state": f.state, "account_id": f.account_id, "error": f.error,
            "password_hint": f.password_hint,
            "qr_svg": qr_svg(f.qr_url) if f.state == "qr" and f.qr_url else None}


def _tg_error(e: Exception) -> HTTPException:
    msg = {
        errors.PhoneNumberInvalidError: "invalid phone number",
        errors.PhoneCodeInvalidError: "wrong code",
        errors.PhoneCodeExpiredError: "code expired — request a new one",
        errors.PasswordHashInvalidError: "wrong 2FA password",
        errors.PhoneNumberBannedError: "this number is banned by Telegram",
    }.get(type(e))
    if isinstance(e, errors.FloodWaitError):
        msg = f"flood wait: retry in {e.seconds}s"
    return HTTPException(400, msg or str(e))


@router.post("/login/phone")
async def login_phone(body: PhoneIn, p: Principal = Depends(panel_user)):
    try:
        f = manager.new_login(p.user_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    try:
        via = await f.send_code(body.phone.strip().replace(" ", ""))
    except Exception as e:
        raise _tg_error(e)
    return _flow_state(f) | {"sent_via": via}


@router.post("/login/qr")
async def login_qr(p: Principal = Depends(panel_user)):
    try:
        f = manager.new_login(p.user_id)
        await f.start_qr(manager)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise _tg_error(e)
    return _flow_state(f)


@router.get("/login/{login_id}")
def login_state(login_id: str, p: Principal = Depends(panel_user)):
    try:
        return _flow_state(manager.login(login_id, p.user_id))
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/login/{login_id}/code")
async def login_code(login_id: str, body: LoginCodeIn, p: Principal = Depends(panel_user)):
    try:
        f = manager.login(login_id, p.user_id)
        if await f.submit_code(body.code.strip()):
            f.account_id = await manager.finish_login(f)
            f.state = "done"
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise _tg_error(e)
    return _flow_state(f)


@router.post("/login/{login_id}/password")
async def login_password(login_id: str, body: LoginPasswordIn, p: Principal = Depends(panel_user)):
    try:
        f = manager.login(login_id, p.user_id)
        await f.submit_password(body.password)
        f.account_id = await manager.finish_login(f)
        f.state = "done"
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise _tg_error(e)
    return _flow_state(f)


@router.delete("/login/{login_id}")
async def login_cancel(login_id: str, p: Principal = Depends(panel_user)):
    try:
        f = manager.login(login_id, p.user_id)
    except KeyError:
        return {"ok": True}
    await f.close()
    manager.logins.pop(login_id, None)
    return {"ok": True}


# ------------------------------------------------------------ API keys

class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    account_id: int | None = None


@router.get("/keys")
def keys(p: Principal = Depends(panel_user)):
    return db.q("SELECT k.id, k.name, k.prefix, k.account_id, k.created_at, k.last_used_at, "
                "a.username AS account_username, a.phone AS account_phone "
                "FROM api_keys k LEFT JOIN accounts a ON a.id=k.account_id "
                "WHERE k.user_id=? AND k.revoked=0 ORDER BY k.id DESC", (p.user_id,))


@router.post("/keys")
def create_key(body: KeyIn, p: Principal = Depends(panel_user)):
    if body.account_id:
        owned_account(p.user_id, body.account_id)
    key = new_api_key()
    kid = db.ex("INSERT INTO api_keys(user_id, account_id, name, prefix, key_hash, created_at) VALUES(?,?,?,?,?,?)",
                (p.user_id, body.account_id, body.name.strip(), key[:10] + "…" + key[-4:], sha256(key), db.now()))
    return {"id": kid, "key": key}


@router.delete("/keys/{key_id}")
def revoke_key(key_id: int, p: Principal = Depends(panel_user)):
    db.ex("UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (key_id, p.user_id))
    return {"ok": True}


# ------------------------------------------------------------ stats

@router.get("/stats")
def stats(p: Principal = Depends(panel_user)):
    day = db.now() - 86400
    per_hour = db.q("SELECT (ts/3600)*3600 AS h, COUNT(*) AS n, SUM(status>=400) AS err FROM request_log "
                    "WHERE user_id=? AND ts>? GROUP BY h ORDER BY h", (p.user_id, day))
    return {
        "accounts": db.one("SELECT COUNT(*) AS n FROM accounts WHERE user_id=?", (p.user_id,))["n"],
        "keys": db.one("SELECT COUNT(*) AS n FROM api_keys WHERE user_id=? AND revoked=0", (p.user_id,))["n"],
        "requests_24h": sum(r["n"] for r in per_hour),
        "errors_24h": sum(r["err"] or 0 for r in per_hour),
        "per_hour": per_hour,
        "recent": db.q("SELECT method, path, status, ms, ts FROM request_log WHERE user_id=? ORDER BY id DESC LIMIT 25",
                       (p.user_id,)),
    }


# ------------------------------------------------------------ admin

class SettingsIn(BaseModel):
    tg_api_id: int | None = None
    tg_api_hash: str | None = None
    registration_open: bool | None = None


@router.get("/settings")
def get_settings(p: Principal = Depends(admin_user)):
    h = db.get_setting("tg_api_hash") or ""
    return {"tg_api_id": db.get_setting("tg_api_id"), "tg_api_hash_set": bool(h),
            "tg_api_hash_hint": (h[:4] + "…" + h[-4:]) if h else None,
            "registration_open": db.get_setting("registration_open", "0") == "1"}


@router.post("/settings")
def set_settings(body: SettingsIn, p: Principal = Depends(admin_user)):
    if body.tg_api_id:
        db.set_setting("tg_api_id", str(body.tg_api_id))
    if body.tg_api_hash:
        db.set_setting("tg_api_hash", body.tg_api_hash.strip())
    if body.registration_open is not None:
        db.set_setting("registration_open", "1" if body.registration_open else "0")
    return get_settings(p)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8)
    is_admin: bool = False


@router.get("/users")
def users(p: Principal = Depends(admin_user)):
    return db.q("SELECT u.id, u.username, u.is_admin, u.totp_enabled, u.created_at, "
                "(SELECT COUNT(*) FROM accounts a WHERE a.user_id=u.id) AS accounts FROM users u ORDER BY u.id")


@router.post("/users")
def create_user(body: UserIn, p: Principal = Depends(admin_user)):
    if db.one("SELECT 1 AS x FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "username taken")
    uid = db.ex("INSERT INTO users(username, pw_hash, is_admin, created_at) VALUES(?,?,?,?)",
                (body.username, hash_password(body.password), int(body.is_admin), db.now()))
    return {"id": uid}


@router.delete("/users/{uid}")
async def delete_user(uid: int, p: Principal = Depends(admin_user)):
    if uid == p.user_id:
        raise HTTPException(400, "you can't delete yourself")
    for a in db.q("SELECT id FROM accounts WHERE user_id=?", (uid,)):
        await manager.remove(a["id"])
    db.ex("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}
