# AGENTS.md

Guide for AI coding agents (and humans) working on **grzxk tg API**: a self-hosted service that
exposes a user's Telegram account as an HTTP API (MTProto via Telethon) with a web panel.

## Stack

- Python 3.11+, FastAPI, Telethon (MTProto user client), SQLite (stdlib `sqlite3`), `cryptography` (Fernet), `pyotp`.
- Frontend: one vanilla-JS SPA in `app/static/` (no build step, no frameworks, no external CDNs).
- Runs as **one** uvicorn process: Telegram connections and update buffers live in memory.

## Layout

| path | what |
| --- | --- |
| `app/main.py` | FastAPI app, error mapping (`{"error": {...}}`), request log middleware, `/swagger`, `/redoc` |
| `app/auth.py` | panel users, cookie sessions, TOTP 2FA, API-key auth (`api_principal`) |
| `app/panel.py` | `/panel/api/*`: Telegram login flows (code → cloud password, QR), keys, stats, admin |
| `app/api.py` | public `/v1/*` REST API; `ctx` dependency resolves the account + live client |
| `app/tl.py` | reflection over Telethon's TL schema: catalog of all methods, JSON → TL, TL → JSON |
| `app/telegram.py` | `Manager`: one `TelegramClient` per account, autoreplies, update buffer, webhooks, `PendingLogin` |
| `app/db.py` | schema + tiny helpers `q`, `one`, `ex`, settings |
| `app/security.py` | scrypt passwords, Fernet session encryption, key generation, rate limiter |
| `app/static/` | `index.html` (SVG icon sprite), `app.css` (graphite theme tokens), `app.js` (router + views) |
| `deploy/` | systemd unit, nginx example, bare-metal `install.sh` |

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
COOKIE_SECURE=0 DATA_DIR=./data .venv/bin/uvicorn app.main:app --port 8080 --reload
```

Telegram itself is needed only for account linking and `/v1` calls; the panel, auth, keys and the
method catalog work without network access. Quick checks:

```bash
python -c "import app.main"                                     # imports / syntax
python -c "from app import tl; print(tl.catalog()['count'])"    # TL reflection (≈823)
curl -s localhost:8080/health
```

## Conventions

- **Errors**: raise `HTTPException(status, "message")`; handlers in `main.py` wrap everything into
  `{"error": {"code", "message", ...}}`. Telethon `RPCError`/`FloodWaitError` are mapped there, don't catch them in routes.
- **New REST endpoint**: add it to `app/api.py` with `c: Ctx = Depends(ctx)`, a `tags=[...]` and a short
  `summary=` — the panel's endpoint explorer and the docs are generated from OpenAPI automatically.
- **Raw MTProto** is already covered by `POST /v1/invoke`; add REST shortcuts only for frequent tasks.
- **Peers** from users are strings: pass them through `peer_of()` / `resolve_entity()`.
- **Message output** goes through `message_json()` / `entity_json()` in `telegram.py` to keep a stable shape.
- **DB**: add tables/columns in `SCHEMA` (`CREATE TABLE IF NOT EXISTS`); for new columns on existing
  tables add an idempotent `ALTER TABLE` guarded by `PRAGMA table_info`.
- **Ownership**: every query touching accounts/keys/rules must filter by `user_id` or the resolved `account_id`.
- **Frontend**: keep it dependency-free. Icons are `<symbol>`s in `index.html` (24×24, stroke 1.4, round caps),
  used via `ico("name")`. Colors only via CSS variables in `:root`. All UI text is Russian.
  Every request goes through `api()` (it prefixes `BASE`, so the panel works under a sub-path like `/tgapi/`).
  Check the 390px mobile layout: no horizontal scroll, tables collapse into cards (`table.t`) or one-liners (`table.t.compact`).

## Security rules

- Never commit `.env`, `data/`, session strings, API keys or Telegram `api_id`/`api_hash` values.
- Telegram sessions are stored only Fernet-encrypted (`security.encrypt`). API keys are stored only as SHA-256.
- Panel cookie is `HttpOnly; SameSite=Strict; Secure` (unless `COOKIE_SECURE=0`). Don't loosen SameSite:
  it is the CSRF protection for cookie-authenticated `/v1` calls made by the explorer.
- Login and TOTP endpoints are rate-limited; keep new auth endpoints behind `login_limiter`.

## Deploy

Production lives on the owner's server under `/opt/tgapi` as systemd service `tgapi`
(`127.0.0.1:8095`, `ROOT_PATH=/tgapi`), proxied by nginx at `https://grzxk.ru/tgapi/`.
Update: pull the code into `/opt/tgapi`, `pip install -r requirements.txt` in `/opt/tgapi/.venv`,
`systemctl restart tgapi`. `data/` (DB + `secret.key`) must survive deploys and be backed up.
