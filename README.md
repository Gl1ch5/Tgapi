# grzxk tg API

Your own Telegram account as an HTTP API, similar to the OpenAI API but for Telegram.
You log in to the panel, link a Telegram account (by phone code + cloud password, or by QR),
create an API key and then do anything the account can do: autoresponders, channel search,
post parsers, mass reading, profile management and so on.

- **Every MTProto method**: `POST /v1/invoke` calls any of the 823 methods of layer 229
  (`messages.sendMessage`, `channels.getParticipants`, `account.updateStatus`, …).
  JSON in, JSON out. Peers are resolved from `@username`, phone, id or `me`.
- **REST shortcuts** for common tasks: dialogs, history/parser, send/edit/delete/forward,
  files, reactions, media download, global post search, chat search, join by invite, members, contacts, profile.
- **Automation**: server-side autoreply rules, long-poll `GET /v1/updates`, signed webhooks.
- **Panel**: minimal graphite UI, panel login with **TOTP 2FA**, multiple users and accounts,
  API keys (stored as hashes, shown once), an endpoint explorer with a "try it" console for every method,
  live update stream, built-in docs, Swagger at `/swagger` and ReDoc at `/redoc`.
- Telegram sessions are stored **encrypted** (Fernet, key in `data/secret.key` or `SECRET_KEY`).

## Quick start

```bash
cp .env.example .env          # set TG_API_ID / TG_API_HASH from https://my.telegram.org
docker compose up -d --build  # panel on http://127.0.0.1:8080
```

or without Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
COOKIE_SECURE=0 .venv/bin/uvicorn app.main:app --port 8080
```

Open the panel, create the admin, enable 2FA (Security), link an account, create a key:

```bash
curl https://tg.example.com/v1/me -H "Authorization: Bearer tgk_..."

curl -X POST https://tg.example.com/v1/invoke -H "Authorization: Bearer tgk_..." \
  -H "Content-Type: application/json" \
  -d '{"method": "contacts.search", "params": {"q": "news", "limit": 10}}'
```

## Production

- `deploy/install.sh` installs it as a systemd service in `/opt/tgapi`, or use `docker-compose.yml`.
- Put nginx with HTTPS in front (`deploy/nginx.conf`). The session cookie is `Secure` by default;
  set `COOKIE_SECURE=0` only for plain-http local testing.
- Run **one** worker process: Telegram connections live in memory.
- Back up `data/`: it holds the database and the encryption key.

## Configuration (`.env`)

| var | meaning |
| --- | --- |
| `TG_API_ID`, `TG_API_HASH` | Telegram app credentials (can also be set in the panel → Admin) |
| `DATA_DIR` | database, secret key (default `./data`) |
| `SECRET_KEY` | optional; otherwise generated into `data/secret.key` |
| `HOST`, `PORT` | bind address (default `127.0.0.1:8080`) |
| `ROOT_PATH` | public sub-path when served under a prefix, e.g. `/tgapi` (nginx strips it) |
| `COOKIE_SECURE` | `1` (default) behind HTTPS |
| `TG_DEVICE_MODEL` | device name shown in Telegram → Devices (default `grzxk tg API`) |

## Project layout

```
app/
  main.py       FastAPI app, error mapping, request log
  auth.py       panel users, sessions, TOTP 2FA, API key auth
  panel.py      account linking (code / 2FA password / QR), keys, admin
  api.py        /v1 REST API
  tl.py         reflection over the TL schema: method catalog, JSON <-> TL
  telegram.py   Telethon client manager, autoreplies, updates, webhooks
  static/       panel (vanilla JS, custom SVG icons)
```

> This drives a real user account. Telegram bans accounts that spam or flood, so throttle bulk actions.
