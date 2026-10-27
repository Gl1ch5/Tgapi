# CLAUDE.md

@AGENTS.md

## Notes for Claude

- Read `AGENTS.md` first: layout, conventions, security rules and deploy steps live there.
- Reply to the owner in Russian; code, comments and commit messages in English.
- Before pushing: `python -c "import app.main"` and, for UI changes, screenshot desktop (1360px) and
  mobile (390px) with Playwright and check there's no horizontal scroll and no console errors.
- You can't reach Telegram's MTProto servers from a sandbox; test login and `/v1` calls on the server.
- Never print or commit the owner's `TG_API_HASH`, session strings or `tgk_` keys.
