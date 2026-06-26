# TPF Community Bot — Human-in-the-Loop Slack Assistant

A small Flask service for **The Product Folks** Slack community. The bot is an
**assistant, not an autonomous actor**: it observes Slack activity, keeps
context, and notifies a designated **Human POC** so they can decide what to do.
It never messages members or moderates on its own.

What it does:

1. **Monitors channel messages** and keeps a rolling record for context.
2. **New-member alerts** — when someone joins the workspace, it DMs the Human
   POC (name, join time, profile) so the POC can welcome them *personally*. No
   automated welcome is ever sent.
3. **Daily digest** — on request, it DMs the POC a report of **official
   updates** and **out-of-context activity** flagged by simple heuristics.

> Understanding is currently rule-based (no AI). The logic lives in one place
> (`analysis.py`) behind clean function boundaries, so a language model can be
> dropped in later without touching the rest of the app.

---

## How it works

```
Slack workspace activity
        │  (messages, team_join)
        ▼
Slack Events API  ──POST──▶  /slack/events   (signature verified)
                                   │
                ┌──────────────────┼─────────────────────┐
                ▼                  ▼                     ▼
        record message     new member joins      (heuristics)
        (SQLite, store.py)        │
                                  ▼
                        DM the Human POC ◀── notify.py
                                  ▲
   GET/POST /tasks/daily-digest ──┘  (build_digest → DM POC)
```

- `app.py` — Flask app, event routing, digest endpoint.
- `slack_verify.py` — verifies the Slack request signature (signing secret + HMAC).
- `store.py` — SQLite persistence for observed messages + new-member dedup.
- `analysis.py` — non-LLM heuristics; the single place to later add AI.
- `notify.py` — DM-the-POC helpers + user/channel name lookups.

---

## Project structure

```
tpf-community-bot/
├── app.py              # Flask app + /slack/events + /tasks/daily-digest
├── slack_verify.py     # Slack signature verification
├── store.py            # SQLite persistence
├── analysis.py         # Heuristics + digest builder (AI hook)
├── notify.py           # POC notification helpers
├── requirements.txt    # Python dependencies
├── Procfile            # Process definition (gunicorn)
├── render.yaml         # Render Blueprint config
├── .env.example        # Environment variable template
├── .gitignore
└── README.md
```

---

## 1. Slack app configuration

### OAuth scopes
In **OAuth & Permissions → Bot Token Scopes**, add:

- `chat:write` — DM the POC
- `im:write` — open a DM channel with the POC
- `channels:history`, `groups:history` — read channel messages it monitors
- `channels:read`, `groups:read` — resolve channel names
- `users:read`, `users:read.email` — resolve member names / profile in alerts
- `team:read` — receive `team_join` events

Install (or reinstall) the app after changing scopes, then copy the
**Bot User OAuth Token** (`xoxb-...`).

### Signing secret
From **Basic Information → App Credentials**, copy the **Signing Secret**.

### Add the bot to the channels it should watch
The bot only receives `message` events for channels it is a member of:

```
/invite @Foldie
```

### Event subscriptions
Under **Event Subscriptions**:

1. Toggle **Enable Events** on.
2. Set the **Request URL** to your endpoint:
   `https://<your-app>.onrender.com/slack/events`
   (Slack sends a one-time `url_verification` challenge — the app answers it
   automatically and the URL shows **Verified**.)
3. Under **Subscribe to bot events**, add:
   - `message.channels` (and `message.groups` for private channels)
   - `team_join`
4. Save changes (reinstall the app if Slack prompts you).

---

## 2. Environment variables

```bash
cp .env.example .env
```

| Variable               | Description                                                  |
| ---------------------- | ------------------------------------------------------------ |
| `SLACK_BOT_TOKEN`      | Bot User OAuth Token (`xoxb-...`)                            |
| `SLACK_SIGNING_SECRET` | App signing secret (request verification)                    |
| `HUMAN_POC_USER_ID`    | Slack user ID (`U...`) of the POC who receives all DMs       |
| `DIGEST_TRIGGER_TOKEN` | Shared secret to protect `/tasks/daily-digest` (recommended) |
| `DIGEST_WINDOW_HOURS`  | Hours of history the digest covers (default `24`)            |
| `DB_PATH`              | SQLite file path (default `bot_data.db`)                     |
| `PORT`                 | Local port (optional; Render sets this)                      |

> **Finding the POC user ID:** in Slack, open the person's profile → **⋮** →
> **Copy member ID** (looks like `U0XXXXXXX`).

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

## 4. Trigger the daily digest

The digest is **not** automated yet — trigger it manually during testing:

```bash
# token only required if DIGEST_TRIGGER_TOKEN is set
curl "http://localhost:3000/tasks/daily-digest?token=YOUR_TOKEN"
```

It returns a small JSON summary and DMs the full digest to the POC. Once it's
proven out, this endpoint can be hit on a schedule (Render Cron, cron-job.org,
or a local launchd/cron job on a Mac) to run automatically each day.

---

## 5. Deploy to Render

This repo includes a `render.yaml` Blueprint.

1. Push to a Git repository (GitHub/GitLab).
2. In Render: **New → Blueprint**, select the repo.
3. Set the environment variables (`SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`,
   `HUMAN_POC_USER_ID`, `DIGEST_TRIGGER_TOKEN`) — they are `sync: false`.
4. After deploy, set the Slack **Request URL** to
   `https://<your-app>.onrender.com/slack/events`.

Manual setup (without the Blueprint):

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app`

> ⚠️ **Storage on free tiers is ephemeral** — the SQLite file is wiped on
> redeploy and the instance sleeps when idle. Fine for testing; for production
> point `DB_PATH` at a persistent disk and run a single instance.

---

## Design decisions

- **Human-in-the-loop:** the bot only observes and notifies. It never sends
  welcome messages, corrects users, posts moderation notices, or acts on a
  member's behalf. The Human POC is the final decision-maker.
- **Heuristics, not judgements:** `analysis.py` *flags candidates* for review.
  Swap its two functions for an LLM later — signatures stay the same.
- **Signature verification:** every `/slack/events` request is validated with a
  constant-time HMAC; requests older than 5 minutes are rejected.
- **Duplicate suppression:** processed `event_id`s (in memory) and recorded
  member IDs (SQLite) prevent double-handling of Slack retries.
- **Fast acknowledgement:** the endpoint returns `200` quickly so Slack does
  not retry.

---

## Roadmap

- **Context understanding:** replace the heuristics in `analysis.py` with a
  language model that reads rolling channel context.
- **Scheduling:** automate the daily digest once the output is trusted.
- **Richer alerts:** immediate (not just daily) POC pings for high-signal events.
