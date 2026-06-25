# TPF Community Bot — Welcome Automation (Phase 1 Demo)

A small Flask service for **The Product Folks** Slack community. When a member
joins a channel the bot detects the `member_joined_channel` event and posts a
welcome message that mentions the new member in the same channel.

This is a proof-of-concept for stakeholder demonstration, initially tested in a
private channel.

---

## How it works

```
Member joins channel
        │
        ▼
Slack Events API  ──POST──▶  /slack/events   (signature verified)
                                   │
                                   ▼
                         chat.postMessage  ──▶  Welcome message in channel
```

- `app.py` — Flask app, event handling, welcome message.
- `slack_verify.py` — verifies the Slack request signature (signing secret + HMAC).

---

## Project structure

```
tpf-community-bot/
├── app.py              # Flask app + /slack/events handler
├── slack_verify.py     # Slack signature verification
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

- `chat:write`
- `channels:read`
- `groups:read`
- `users:read`

Install (or reinstall) the app to the workspace after changing scopes, then copy
the **Bot User OAuth Token** (`xoxb-...`).

### Signing secret
From **Basic Information → App Credentials**, copy the **Signing Secret**.

### Add the bot to your test channel
Invite the bot to the private channel:

```
/invite @Foldie
```

> Note: `member_joined_channel` events for a channel are only delivered after the
> bot is a member of that channel.

### Event subscriptions
Under **Event Subscriptions**:

1. Toggle **Enable Events** on.
2. Set the **Request URL** to your deployed endpoint:
   `https://<your-app>.onrender.com/slack/events`
   Slack sends a one-time `url_verification` challenge — the app responds
   automatically, and the URL should show **Verified**.
3. Under **Subscribe to bot events**, add `member_joined_channel`.
4. Save changes (reinstall the app if Slack prompts you to).

---

## 2. Environment variables

Copy the template and fill in real values:

```bash
cp .env.example .env
```

| Variable               | Description                                   |
| ---------------------- | --------------------------------------------- |
| `SLACK_BOT_TOKEN`      | Bot User OAuth Token (`xoxb-...`)             |
| `SLACK_SIGNING_SECRET` | App signing secret (request verification)     |
| `PORT`                 | Local port (optional; Render sets this)       |

---

## 3. Run locally

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The app starts on `http://localhost:3000`.

To let Slack reach your local server during development, expose it with a tunnel
(e.g. [ngrok](https://ngrok.com/)):

```bash
ngrok http 3000
```

Use the resulting `https://<id>.ngrok.io/slack/events` as the Request URL while
testing.

---

## 4. Deploy to Render

This repo includes a `render.yaml` Blueprint.

1. Push the project to a Git repository (GitHub/GitLab).
2. In Render: **New → Blueprint**, and select the repo. Render reads
   `render.yaml` and provisions a Web Service.
3. When prompted, set the environment variables `SLACK_BOT_TOKEN` and
   `SLACK_SIGNING_SECRET` (they are marked `sync: false` so they are never stored
   in the repo).
4. After the deploy succeeds, copy the service URL and set the Slack **Request
   URL** to `https://<your-app>.onrender.com/slack/events`.

Manual setup (without the Blueprint) works too:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app`
- Add the two environment variables under the service settings.

---

## 5. Test the demo

1. Confirm the service health check responds: open
   `https://<your-app>.onrender.com/` → `{"status":"ok",...}`.
2. Have a member (or yourself) join the test channel.
3. The bot posts the welcome message mentioning the new member. 🎉

---

## Notes & design decisions

- **Signature verification:** every request to `/slack/events` is validated
  against the signing secret with a constant-time HMAC comparison, and requests
  older than 5 minutes are rejected (replay protection).
- **Duplicate suppression:** Slack retries un-acknowledged events. Processed
  `event_id`s are remembered in memory to avoid double-posting. (For multi-instance
  production use, back this with a shared store like Redis.)
- **Self-join ignored:** the bot does not welcome itself when it is added to a
  channel.
- **Fast acknowledgement:** the endpoint returns `200` quickly so Slack does not
  retry.

---

## Future roadmap (not implemented)

- **Phase 2:** Welcome users joining the whole workspace; send onboarding resources.
- **Phase 3:** Spam / self-promotion detection and moderator alerts.
- **Phase 4:** Community analytics, active-member tracking, weekly reports.
