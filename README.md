# TPF Community Bot — Daily Community Report

A small Flask service for **The Product Folks** Slack community. The bot is an
**assistant, not an autonomous actor**: it watches Slack and, once a day, DMs a
designated **Human POC** a single report. It never messages members or moderates
on its own — the POC decides what to do.

The daily report contains:

1. **New members** who joined (so the POC can welcome them **personally** — the
   bot never DMs members).
2. **Who said what** — per channel, the noteworthy messages with attribution.
3. **Might need a reply** — unanswered questions/requests.
4. **Looks out of place** — messages that don't fit their channel (promotion in
   a discussion channel, etc.), with the **exact message + user + channel**.

> Understanding uses **OpenAI when `OPENAI_API_KEY` is set** (`ai.py`), and falls
> back to keyword heuristics (`analysis.py`) otherwise. AI runs only at report
> time and is batched to keep token use low.

---

## How it works

```
New member joins (team_join)  ──▶  /slack/events  ──▶  record member (store.py)

Daily report (run on demand):
GET/POST /tasks/daily-report
        │
        ▼
collect.py ── fetch last 24h of history live from Slack (no message storage)
        │
        ▼
ai.py / analysis.py ── classify each message (fit-per-channel, needs-reply)
        │
        ▼
analysis.build_digest + format_daily_report
        │
        ▼
DM the Human POC ◀── notify.py   (+ new members from store.py)
```

- `app.py` — Flask app, event routing, task endpoints.
- `slack_verify.py` — verifies the Slack request signature (signing secret + HMAC).
- `collect.py` — fetches recent channel history live at report time.
- `ai.py` — optional OpenAI classification (batched), with heuristic fallback.
- `analysis.py` — keyword heuristics + report builder/formatter.
- `notify.py` — DM-the-POC helper and name lookups.
- `store.py` — tiny SQLite store recording who joined (for the report).

---

## 1. Slack app configuration

### OAuth scopes
In **OAuth & Permissions → Bot Token Scopes**, add:

- `chat:write` — DM the POC
- `im:write` — open the DM channel with the POC
- `channels:join` — join all public channels (`/tasks/join-public`)
- `channels:history`, `groups:history` — read channel history for the report
- `channels:read`, `groups:read` — list/resolve channel names
- `users:read` — resolve member names
- `team:read` — receive `team_join` (new member) events

Reinstall the app after changing scopes, then copy the **Bot User OAuth Token**
(`xoxb-...`).

### Signing secret
From **Basic Information → App Credentials**, copy the **Signing Secret**.

### Event subscriptions
Under **Event Subscriptions**:

1. Enable events and set the **Request URL** to
   `https://<your-app>.onrender.com/slack/events` (the app answers Slack's
   `url_verification` automatically — it should show **Verified**).
2. Under **Subscribe to bot events**, add **`team_join`**.
3. Save (reinstall if prompted).

> The bot reads history only for channels it's a member of — run
> `/tasks/join-public` (below) so it joins them all.

---

## 2. Environment variables

```bash
cp .env.example .env
```

| Variable               | Description                                              |
| ---------------------- | -------------------------------------------------------- |
| `SLACK_BOT_TOKEN`      | Bot User OAuth Token (`xoxb-...`)                        |
| `SLACK_SIGNING_SECRET` | App signing secret (request verification)                |
| `HUMAN_POC_USER_ID`    | Slack user ID (`U...`) of the POC who gets the report    |
| `DIGEST_TRIGGER_TOKEN` | Shared secret protecting the `/tasks/*` endpoints        |
| `DAILY_WINDOW_HOURS`   | Hours of history the report covers (default `24`)        |
| `OPENAI_API_KEY`       | *(optional)* enables AI classification                   |
| `OPENAI_MODEL`         | OpenAI model (default `gpt-4o-mini`)                     |
| `AI_CLASSIFICATION`    | `on`/`off` toggle even when a key is set (default `on`)  |
| `DB_PATH`              | SQLite file path (default `bot_data.db`)                 |
| `PORT`                 | Local port (optional; Render sets this)                  |

> **POC user ID:** in Slack, open the person's profile → **⋮** → **Copy member ID**.

---

## 3. Run locally

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Expose it to Slack with a tunnel (e.g. [ngrok](https://ngrok.com/)):

```bash
ngrok http 3000
```

Use `https://<id>.ngrok.io/slack/events` as the Request URL while testing.

---

## 4. Task endpoints

Both are protected by `DIGEST_TRIGGER_TOKEN` (pass `?token=...`); if the token
isn't set they're open (testing only).

**Join all public channels** — run once after install, and again when new public
channels are added:

```bash
curl "http://localhost:3000/tasks/join-public?token=YOUR_TOKEN"
```
Private channels can't be self-joined — an admin must invite the bot to those.

**Daily report** — builds the report and DMs it to the POC:

```bash
curl "http://localhost:3000/tasks/daily-report?token=YOUR_TOKEN"
```
Returns a small JSON summary and DMs the full report. It's **not automated
yet** — once proven out, hit it on a schedule (Render Cron, cron-job.org, or a
local launchd/cron job on a Mac) once a day.

---

## 5. Deploy to Render

This repo includes a `render.yaml` Blueprint.

1. Push to a Git repository.
2. In Render: **New → Blueprint**, select the repo.
3. Set the environment variables (`SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`,
   `HUMAN_POC_USER_ID`, `DIGEST_TRIGGER_TOKEN`, and `OPENAI_API_KEY` if using AI).
4. After deploy, set the Slack **Request URL** to
   `https://<your-app>.onrender.com/slack/events`, and run `/tasks/join-public`.

Manual setup (without the Blueprint):

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app`

> ℹ️ **No message storage.** The report fetches history live from Slack, so a
> wiped disk loses nothing. The only local state is the small "who joined" list
> for the new-members section; if it's wiped, that section may miss recent
> joins. Point `DB_PATH` at a persistent disk to avoid that.

---

## Design decisions

- **Human-in-the-loop:** the bot only reports to the POC. It never welcomes,
  replies, corrects, or moderates — the POC does all user-facing actions.
- **No message storage:** the report pulls the last day from Slack at run time
  (`collect.py`).
- **AI with a safety net:** with `OPENAI_API_KEY` set, `ai.py` judges fit and
  reply-worthiness (batched, report-time only); otherwise keyword heuristics run.
  Either way the bot only *flags candidates*.
- **Already-answered filter:** a question isn't flagged "needs a reply" if it
  already has thread replies (`reply_count`).
- **Signature verification:** every `/slack/events` request is validated with a
  constant-time HMAC; requests older than 5 minutes are rejected.

---

## Roadmap

- **Scheduling:** automate the daily report once trusted (Render Cron / Mac).
- **Per-channel rules for the AI:** feed each channel's stated purpose to
  `ai.py` for sharper judgement.
- **Suggested reply drafts:** optionally include an AI-drafted reply the POC can
  send (currently we only flag that a reply may be needed).
