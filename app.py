"""TPF Community Bot — human-in-the-loop Slack monitoring assistant.

The bot observes Slack activity and gives a designated Human POC the context to
act. It never messages members or moderates on its own. Its jobs are:

  1. Watch channel messages and keep a rolling record (for context/digests).
  2. When a new member joins the workspace, DM the POC so they can welcome them
     personally — no automated welcome is ever sent.
  3. On request, build a daily digest (official updates + out-of-place activity)
     and DM it to the POC.

All user-facing decisions stay with the Human POC.
"""

import logging
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import notify
import store
from analysis import build_digest, format_digest
from slack_verify import is_valid_slack_request

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("tpf-community-bot")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")
DIGEST_TRIGGER_TOKEN = os.environ.get("DIGEST_TRIGGER_TOKEN")
DIGEST_WINDOW_HOURS = int(os.environ.get("DIGEST_WINDOW_HOURS", "24"))

if not SLACK_BOT_TOKEN:
    logger.warning("SLACK_BOT_TOKEN is not set — the bot cannot read or DM.")
if not SLACK_SIGNING_SECRET:
    logger.warning("SLACK_SIGNING_SECRET is not set — request verification will fail.")
if not notify.HUMAN_POC_USER_ID:
    logger.warning("HUMAN_POC_USER_ID is not set — the POC cannot be notified.")

slack_client = WebClient(token=SLACK_BOT_TOKEN)

app = Flask(__name__)

# Persistent store for observed messages and new-member dedup.
store.init_db()

# Remember processed event IDs so Slack's automatic retries aren't handled twice.
_processed_events = set()

# Cache of the bot's own user ID so we don't record the bot's own messages.
_bot_user_id = None


def get_bot_user_id():
    """Return (and cache) the bot's own Slack user ID, or None if unavailable."""
    global _bot_user_id
    if _bot_user_id is None:
        try:
            _bot_user_id = slack_client.auth_test().get("user_id")
        except SlackApiError as e:
            logger.error("auth.test failed: %s", e.response.get("error"))
    return _bot_user_id


# --------------------------------------------------------------------------- #
# Event handlers
# --------------------------------------------------------------------------- #


def handle_message(event):
    """Record a real user message for context. Ignore bot/system messages."""
    # Subtypes (joins, edits, deletes, bot posts...) aren't human conversation.
    if event.get("subtype") or event.get("bot_id"):
        return

    user_id = event.get("user")
    channel_id = event.get("channel")
    if not user_id or not channel_id:
        return
    if user_id == get_bot_user_id():
        return

    store.save_message(
        channel_id=channel_id,
        user_id=user_id,
        text=event.get("text", ""),
        ts=event.get("ts"),
    )


def handle_team_join(event):
    """A new member joined the workspace — inform the POC, don't message them."""
    user = event.get("user", {})
    user_id = user.get("id") if isinstance(user, dict) else user
    if not user_id:
        return

    # Dedup against Slack retries: only notify the first time we see this member.
    if not store.record_member(user_id):
        logger.info("Already notified POC about member %s; skipping.", user_id)
        return

    joined_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if notify.notify_new_member(slack_client, user_id, joined_at):
        logger.info("Notified POC about new member %s.", user_id)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@app.route("/", methods=["GET"])
def health():
    """Health check endpoint for Render."""
    return jsonify(status="ok", service="tpf-community-bot")


@app.route("/slack/events", methods=["POST"])
def slack_events():
    # Raw body is required for signature verification — read it before parsing.
    raw_body = request.get_data()

    if not is_valid_slack_request(
        signing_secret=SLACK_SIGNING_SECRET,
        request_body=raw_body,
        timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
        signature=request.headers.get("X-Slack-Signature", ""),
    ):
        logger.warning("Rejected request with invalid Slack signature.")
        return jsonify(error="invalid signature"), 403

    payload = request.get_json(silent=True) or {}
    payload_type = payload.get("type")

    # 1. URL verification handshake (sent once when configuring Event Subscriptions).
    if payload_type == "url_verification":
        return jsonify(challenge=payload.get("challenge"))

    # 2. Real event delivery.
    if payload_type == "event_callback":
        event_id = payload.get("event_id")
        if event_id and event_id in _processed_events:
            return jsonify(ok=True)  # Duplicate retry — already handled.
        if event_id:
            _processed_events.add(event_id)

        event = payload.get("event", {})
        event_type = event.get("type")

        if event_type == "message":
            handle_message(event)
        elif event_type == "team_join":
            handle_team_join(event)

    # Always 200 quickly so Slack doesn't retry.
    return jsonify(ok=True)


@app.route("/tasks/daily-digest", methods=["GET", "POST"])
def daily_digest():
    """Build the daily digest and DM it to the Human POC.

    Triggered manually (or later by a scheduler/cron). Protect it by setting
    DIGEST_TRIGGER_TOKEN and passing it as ?token=... or an X-Digest-Token
    header. If the token isn't configured, the endpoint is open (testing only).
    """
    if DIGEST_TRIGGER_TOKEN:
        supplied = request.args.get("token") or request.headers.get("X-Digest-Token")
        if supplied != DIGEST_TRIGGER_TOKEN:
            return jsonify(error="unauthorized"), 401

    since = time.time() - DIGEST_WINDOW_HOURS * 3600
    messages = store.get_messages_since(since)

    digest = build_digest(
        messages,
        channel_name_of=lambda cid: notify.channel_name_of(slack_client, cid),
        display_name_of=lambda uid: notify.display_name_of(slack_client, uid),
    )
    text = format_digest(digest, period_label=f"the last {DIGEST_WINDOW_HOURS} hours")

    delivered = notify.dm_poc(slack_client, text)
    return jsonify(
        ok=True,
        delivered_to_poc=delivered,
        messages_observed=digest["total"],
        official=len(digest["official"]),
        out_of_place=len(digest["out_of_place"]),
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
