"""TPF Community Bot — daily community report for the Human POC.

The bot observes Slack and, once a day, DMs the Human POC a single report:

  1. New members who joined (so the POC can welcome them personally — the bot
     never messages members itself).
  2. Per-channel "who said what" — noteworthy messages with attribution.
  3. Messages that may need a reply (unanswered questions/requests).
  4. Messages that look out of place for their channel (with the exact message).

Everything is a flag for the POC to review — the bot takes no action on users.
"""

import logging
import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import ai
import collect
import notify
import store
from analysis import build_digest, count_flags, count_replies, format_daily_report
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
# The daily report covers this many hours of history (default 24).
DAILY_WINDOW_HOURS = int(os.environ.get("DAILY_WINDOW_HOURS", "24"))

if not SLACK_BOT_TOKEN:
    logger.warning("SLACK_BOT_TOKEN is not set — the bot cannot read or DM.")
if not SLACK_SIGNING_SECRET:
    logger.warning("SLACK_SIGNING_SECRET is not set — request verification will fail.")
if not notify.HUMAN_POC_USER_ID:
    logger.warning("HUMAN_POC_USER_ID is not set — the POC cannot be notified.")

slack_client = WebClient(token=SLACK_BOT_TOKEN)

app = Flask(__name__)

# Persistent store: records who joined (for the daily "new members" section).
store.init_db()

# Remember processed event IDs so Slack's automatic retries aren't handled twice.
_processed_events = set()

# Cache of the bot's own user ID so we don't count the bot as a joiner.
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


def handle_new_member(user_id):
    """Record a new member so they appear in the next daily report.

    The bot does NOT message the member — the POC welcomes them personally.
    """
    if not user_id or user_id == get_bot_user_id():
        return
    if store.record_member(user_id):
        logger.info("Recorded new member %s for the daily report.", user_id)


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
        if event.get("type") == "team_join":
            user = event.get("user", {})
            handle_new_member(user.get("id") if isinstance(user, dict) else user)

    # Always 200 quickly so Slack doesn't retry.
    return jsonify(ok=True)


def _authorized(req):
    """Token check shared by the task endpoints (open if no token configured)."""
    if not DIGEST_TRIGGER_TOKEN:
        return True
    supplied = req.args.get("token") or req.headers.get("X-Digest-Token")
    return supplied == DIGEST_TRIGGER_TOKEN


@app.route("/tasks/daily-report", methods=["GET", "POST"])
def daily_report():
    """Build the daily report and DM it to the Human POC.

    Fetches the last DAILY_WINDOW_HOURS of history live, classifies it (AI if a
    key is set, else heuristics), lists members who joined in the window, and
    DMs the POC one combined report. Protect with DIGEST_TRIGGER_TOKEN.
    """
    if not _authorized(request):
        return jsonify(error="unauthorized"), 401

    since = time.time() - DAILY_WINDOW_HOURS * 3600

    # 1. Live history → classify → per-channel breakdown.
    messages = collect.fetch_recent_messages(slack_client, since)
    channel_name_of = lambda cid: notify.channel_name_of(slack_client, cid)
    display_name_of = lambda uid: notify.display_name_of(slack_client, uid)
    verdicts = ai.classify_messages(messages, channel_name_of)  # None → heuristics
    digest = build_digest(messages, channel_name_of, display_name_of, verdicts=verdicts)

    # 2. Members who joined in the window (as clickable mentions).
    new_member_ids = store.get_members_since(since)
    new_members = [f"<@{uid}>" for uid in new_member_ids]

    text = format_daily_report(
        digest, new_members=new_members, period_label=f"last {DAILY_WINDOW_HOURS}h"
    )
    delivered = notify.dm_poc(slack_client, text)
    return jsonify(
        ok=True,
        delivered_to_poc=delivered,
        new_members=len(new_members),
        messages_observed=digest["total"],
        needs_reply=count_replies(digest),
        out_of_place=count_flags(digest),
    )


@app.route("/tasks/peek", methods=["GET", "POST"])
def peek():
    """Diagnostic: show how many member channels and messages are visible in the
    report window, per channel. Helps confirm the bot can actually read history."""
    if not _authorized(request):
        return jsonify(error="unauthorized"), 401
    now = time.time()
    since = now - DAILY_WINDOW_HOURS * 3600
    result = collect.channel_report(slack_client, since)
    result["debug"] = {
        "server_now": now,
        "oldest_epoch": since,
        "window_hours": DAILY_WINDOW_HOURS,
    }
    return jsonify(result)


@app.route("/tasks/join-public", methods=["GET", "POST"])
def join_public_channels():
    """Make the bot join every public channel so it can monitor them all.

    Saves inviting it to ~20+ channels by hand. Private channels can't be
    self-joined — an admin must invite the bot to those.
    """
    if not _authorized(request):
        return jsonify(error="unauthorized"), 401

    joined, already, failed = [], 0, []
    cursor = None
    try:
        while True:
            resp = slack_client.conversations_list(
                types="public_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
            for ch in resp.get("channels", []):
                if ch.get("is_member"):
                    already += 1
                    continue
                try:
                    slack_client.conversations_join(channel=ch["id"])
                    joined.append(ch.get("name", ch["id"]))
                except SlackApiError as e:
                    failed.append({"channel": ch.get("name"), "error": e.response.get("error")})
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        return jsonify(ok=False, error=e.response.get("error")), 500

    logger.info("join-public: joined %d, already %d, failed %d",
                len(joined), already, len(failed))
    return jsonify(
        ok=True,
        joined=joined,
        joined_count=len(joined),
        already_member=already,
        failed=failed,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
