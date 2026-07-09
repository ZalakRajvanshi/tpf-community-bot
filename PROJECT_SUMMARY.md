# TPF Community Bot — Project Summary

A complete overview of what the bot is, how it works, and how it was built.

---

## What it is

A **human-in-the-loop Slack assistant** for The Product Folks community. It
watches all channels and, **once a day, DMs a designated Human POC** a structured
report. It **never messages members or moderates** — it only surfaces
information; the POC decides what to do.

> It started as an auto-welcome bot but was pivoted entirely into a monitoring/
> reporting assistant (see [Evolution](#evolution)).

---

## What the daily report contains

Delivered to the POC's DM, covering the last 24 hours:

1. **🙋 New members** — who joined the workspace (clickable @mentions), so the POC
   welcomes them personally.
2. **💬 Might need a reply** — unanswered questions/requests (skips ones already
   answered in a thread).
3. **⚠️ Out of place** — messages that don't fit their channel (promotion in a
   discussion channel, etc.), with the message + reason.
4. **📢 Noteworthy** — announcements / launches / decisions worth seeing.
5. **📊 By channel** — one-line activity summary per channel.

Each section is **capped at 6 items** with "…and N more", and every author shows
as a **clickable @mention** so the POC knows exactly who said what.

---

## Architecture

| File | Role |
| --- | --- |
| `app.py` | Flask app — Slack event handling + all endpoints |
| `slack_verify.py` | Verifies Slack request signatures (HMAC + replay protection) |
| `collect.py` | Fetches the last N hours of channel history **live** from Slack (no message storage) |
| `ai.py` | Optional OpenAI classification (batched), with keyword fallback |
| `analysis.py` | Keyword heuristics + report builder/formatter |
| `notify.py` | DM-the-POC helper + user/channel name lookups |
| `store.py` | Tiny SQLite — only records who joined (for the new-members section) |

**Key design:** messages are **never stored** — the report pulls history from
Slack at run time, so a wiped or sleeping server loses nothing.

---

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Health check → `{"status":"ok"}` |
| `POST /slack/events` | Receives `team_join` events (records new members) |
| `GET/POST /tasks/daily-report` | Builds + DMs the report (the main one) |
| `GET/POST /tasks/join-public` | Bot auto-joins all public channels |
| `GET/POST /tasks/peek` | Diagnostic — per-channel read counts + debug info |

All `/tasks/*` endpoints are protected by `DIGEST_TRIGGER_TOKEN` (`?token=...`).

---

## Slack app configuration

**Bot Token Scopes:** `chat:write`, `im:write`, `channels:join`,
`channels:history`, `groups:history`, `channels:read`, `groups:read`,
`users:read`, `team:read` *(plus a leftover `mpim:read`)*

**Event subscriptions:** `team_join`

**Bot display name in Slack:** Foldie

---

## Environment variables

| Variable | Purpose |
| --- | --- |
| `SLACK_BOT_TOKEN` | Bot token (`xoxb-`) |
| `SLACK_SIGNING_SECRET` | Request verification |
| `HUMAN_POC_USER_ID` | `U0BCLG7AF7H` — who receives the report |
| `DIGEST_TRIGGER_TOKEN` | Protects the `/tasks/*` URLs |
| `DAILY_WINDOW_HOURS` | `24` (temporarily set to `360` for the one-time 15-day catch-up) |
| `OPENAI_API_KEY` | *(optional)* enables AI classification |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `AI_CLASSIFICATION` | `on` / `off` |
| `DB_PATH`, `PORT` | SQLite path / local port |

---

## Deployment & automation

- **Host:** Render (free web service), start command `gunicorn app:app --timeout 120`
- **Repo:** `github.com/ZalakRajvanshi/tpf-community-bot`, deploys on push to `main`
- **URL:** `https://tpf-community-bot.onrender.com`
- **Schedule:** cron-job.org hits `/tasks/daily-report` daily at **11:00**
  - ⚠️ Verify the cron job's timezone is **Asia/Kolkata**, not UTC (else it fires at 4:30 PM IST).

---

## AI classification

- Runs **only at report time**, **batched** (40 messages/call), small model
  (`gpt-4o-mini`).
- Judges each message: noteworthy? out-of-place (fit for its channel)? needs a
  reply?
- **Cost:** ~a tenth of a cent per daily run (~$0.03–0.30 / month).
- Falls back to keyword heuristics if the key is unset or a call fails.

---

## Bugs found & fixed

1. **Missing `channels:join`** → couldn't self-join channels. *(Fix: add scope + reinstall.)*
2. **Missing `channels:history`** → could join but not read. *(Fix: add scope + reinstall.)*
3. **`cursor=None`** passed to `conversations.history` → Slack returned nothing.
   *(Fix: only pass the cursor once we have one.)*
4. **Timestamp precision** — the big one. `str(time.time() - ...)` sometimes
   produced **7 decimals**, which Slack's `oldest` filter silently rejected →
   intermittent **0 messages**. *(Fix: format to exactly 6 decimals, `%.6f`.)*
   This was the root cause of all the "random zeros."
5. **Gunicorn 30s timeout** → could kill the report mid-run on a cold start.
   *(Fix: `--timeout 120`.)*

---

## Current status

- ✅ Bot healthy (`200 ok`), joined all **21** public channels
- ✅ History reading reliable (verified even with a 7-decimal float)
- ✅ 15-day catch-up report delivered to the POC with @mentions
- ✅ Daily 11 AM auto-run scheduled
- ✅ Window back to **24h** for normal daily use

---

## Known limitations

- **New members** only catches joins **after** deploy (via `team_join`); on a
  free-tier redeploy the small SQLite record can reset.
- **"Needs a reply"** only treats *thread* replies as answered — an inline
  channel answer can't be detected, so a few may be over-flagged.
- **Coverage** = public channels the bot joined; **private channels** need a
  manual admin invite.
- **Heuristics** (without AI) over-flag; turning on `OPENAI_API_KEY` sharpens it.

---

## Evolution

Auto-welcome bot → human-in-the-loop monitor (POC alerts) → per-channel digest →
monthly then **daily** report → removed auto-welcome entirely (POC welcomes
manually) → live history fetch (no storage) → AI classification → clickable
@mentions → automated at 11 AM.

---

## Quick reference — running it

```bash
# Join all public channels (run once after install)
curl "https://tpf-community-bot.onrender.com/tasks/join-public?token=YOUR_TOKEN"

# Trigger the daily report manually
curl "https://tpf-community-bot.onrender.com/tasks/daily-report?token=YOUR_TOKEN"

# Diagnostic: what can the bot see right now?
curl "https://tpf-community-bot.onrender.com/tasks/peek?token=YOUR_TOKEN"
```
