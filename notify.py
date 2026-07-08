"""Helpers for giving the Human POC context via direct message.

Everything the bot surfaces goes to a single configured POC as a DM. The bot
never messages members or channels on its own — it only informs the POC, who
takes any user-facing action personally.
"""

import logging
import os

from slack_sdk.errors import SlackApiError

logger = logging.getLogger("tpf-community-bot.notify")

HUMAN_POC_USER_ID = os.environ.get("HUMAN_POC_USER_ID")

# Simple in-memory caches so we don't call Slack for the same name repeatedly.
_user_cache = {}
_channel_cache = {}


def display_name_of(client, user_id):
    """Return a readable name for a user id, cached. Falls back to the id."""
    if not user_id:
        return "unknown"
    if user_id in _user_cache:
        return _user_cache[user_id]
    name = user_id
    try:
        info = client.users_info(user=user_id).get("user", {})
        profile = info.get("profile", {})
        name = (
            profile.get("display_name")
            or profile.get("real_name")
            or info.get("name")
            or user_id
        )
    except SlackApiError as e:
        logger.warning("users_info failed for %s: %s", user_id, e.response.get("error"))
    _user_cache[user_id] = name
    return name


def channel_name_of(client, channel_id):
    """Return a readable channel name for a channel id, cached."""
    if not channel_id:
        return "unknown"
    if channel_id in _channel_cache:
        return _channel_cache[channel_id]
    name = channel_id
    try:
        info = client.conversations_info(channel=channel_id).get("channel", {})
        name = info.get("name") or channel_id
    except SlackApiError as e:
        logger.warning(
            "conversations_info failed for %s: %s", channel_id, e.response.get("error")
        )
    _channel_cache[channel_id] = name
    return name


def dm_user(client, user_id, text):
    """Open (or reuse) a DM with a user and post a message. Returns True/False."""
    if not user_id:
        return False
    try:
        im = client.conversations_open(users=user_id)
        channel = im["channel"]["id"]
        client.chat_postMessage(
            channel=channel,
            text=text,
            unfurl_links=False,
            unfurl_media=False,
        )
        return True
    except SlackApiError as e:
        logger.error("Failed to DM %s: %s", user_id, e.response.get("error"))
        return False


def dm_poc(client, text):
    """Send a direct message to the configured Human POC."""
    if not HUMAN_POC_USER_ID:
        logger.warning("HUMAN_POC_USER_ID is not set — cannot notify the POC.")
        return False
    return dm_user(client, HUMAN_POC_USER_ID, text)
