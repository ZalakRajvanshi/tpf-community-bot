"""TPF Community Bot — Slack welcome automation (Phase 1 demo).

Listens for `member_joined_channel` events via the Slack Events API and posts a
welcome message that mentions the new member in the same channel.
"""

import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slack_verify import is_valid_slack_request

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("tpf-community-bot")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

if not SLACK_BOT_TOKEN:
    logger.warning("SLACK_BOT_TOKEN is not set — the bot cannot post messages.")
if not SLACK_SIGNING_SECRET:
    logger.warning("SLACK_SIGNING_SECRET is not set — request verification will fail.")

slack_client = WebClient(token=SLACK_BOT_TOKEN)

app = Flask(__name__)

# Remember processed event IDs so Slack's automatic retries don't trigger
# duplicate welcome messages. In-memory is fine for a single-instance POC.
_processed_events = set()

# Cache of the bot's own user ID so we don't welcome the bot itself.
_bot_user_id = None


WELCOME_MESSAGE = (
    "👋 Welcome <@{user_id}> to The Product Folks!\n\n"
    "We're excited to have you here.\n\n"
    "📚 *Product Academy*\n"
    "https://www.theproductfolks.com/product-academy\n\n"
    "💼 *Product Management Jobs*\n"
    "https://www.theproductfolks.com/product-management-jobs\n\n"
    "🌐 *Explore The Product Folks*\n"
    "https://www.theproductfolks.com\n\n"
    "Feel free to introduce yourself and start engaging with the community."
)


def get_bot_user_id():
    """Return (and cache) the bot's own Slack user ID, or None if unavailable."""
    global _bot_user_id
    if _bot_user_id is None:
        try:
            _bot_user_id = slack_client.auth_test().get("user_id")
        except SlackApiError as e:
            logger.error("auth.test failed: %s", e.response.get("error"))
    return _bot_user_id


def post_welcome_message(channel_id, user_id):
    try:
        slack_client.chat_postMessage(
            channel=channel_id,
            text=WELCOME_MESSAGE.format(user_id=user_id),
        )
        logger.info("Posted welcome for user %s in channel %s", user_id, channel_id)
    except SlackApiError as e:
        logger.error("Failed to post welcome message: %s", e.response.get("error"))


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
        if event.get("type") == "member_joined_channel":
            user_id = event.get("user")
            channel_id = event.get("channel")

            if user_id and user_id == get_bot_user_id():
                logger.info("Ignoring join event for the bot itself.")
            elif user_id and channel_id:
                post_welcome_message(channel_id, user_id)

    # Always 200 quickly so Slack doesn't retry.
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
