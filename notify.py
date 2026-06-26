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


def dm_poc(client, text):
    """Send a direct message to the configured Human POC.

    Returns True on success. Logs and returns False if the POC isn't
    configured or Slack rejects the message.
    """
    if not HUMAN_POC_USER_ID:
        logger.warning("HUMAN_POC_USER_ID is not set — cannot notify the POC.")
        return False
    try:
        # Open (or reuse) the DM channel, then post into it.
        im = client.conversations_open(users=HUMAN_POC_USER_ID)
        channel = im["channel"]["id"]
        client.chat_postMessage(
            channel=channel,
            text=text,
            unfurl_links=False,
            unfurl_media=False,
        )
        return True
    except SlackApiError as e:
        logger.error("Failed to DM the POC: %s", e.response.get("error"))
        return False


def notify_new_member(client, user_id, channel_id, joined_at_iso):
    """DM the POC that a member joined a channel, so they can welcome them.

    Fires in real time at join time and names the channel they joined.
    """
    name = display_name_of(client, user_id)
    channel = channel_name_of(client, channel_id)
    profile_line = ""
    try:
        info = client.users_info(user=user_id).get("user", {})
        profile = info.get("profile", {})
        bits = []
        if profile.get("real_name"):
            bits.append(profile["real_name"])
        if profile.get("title"):
            bits.append(profile["title"])
        if profile.get("email"):
            bits.append(profile["email"])
        if bits:
            profile_line = "\n   ↳ " + " · ".join(bits)
    except SlackApiError:
        pass  # Profile details are best-effort.

    text = (
        "🙋 *New member joined*\n"
        f"• *Name:* {name} (<@{user_id}>)\n"
        f"• *Joined channel:* #{channel}\n"
        f"• *When:* {joined_at_iso}{profile_line}\n\n"
        "_No automated message was sent. Send them a welcome when you're ready._"
    )
    return dm_poc(client, text)
