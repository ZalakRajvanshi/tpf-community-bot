"""Fetch recent channel history straight from Slack at report time.

This avoids storing messages. When the daily report runs, we list the channels
the bot is a member of and pull each channel's history for the window, then
hand the messages to analysis.build_digest.

Requires the bot to be a member of the channels (use /tasks/join-public for
public ones; an admin must invite it to private ones) and the
channels:history / groups:history scopes.
"""

import logging

from slack_sdk.errors import SlackApiError

logger = logging.getLogger("tpf-community-bot.collect")


def _member_channels(client):
    """Yield channels (public + private) the bot is currently a member of."""
    cursor = None
    while True:
        try:
            resp = client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
        except SlackApiError as e:
            logger.error("conversations_list failed: %s", e.response.get("error"))
            return
        for ch in resp.get("channels", []):
            if ch.get("is_member"):
                yield ch["id"]
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return


def _channel_history(client, channel_id, oldest_epoch):
    """Yield real user messages in a channel newer than oldest_epoch."""
    cursor = None
    while True:
        # Only include cursor when we actually have one — passing cursor=None
        # makes Slack return nothing.
        params = {"channel": channel_id, "oldest": str(oldest_epoch), "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = client.conversations_history(**params)
        except SlackApiError as e:
            logger.warning(
                "history failed for %s: %s", channel_id, e.response.get("error")
            )
            return
        for m in resp.get("messages", []):
            # Skip joins, edits, bot posts and other non-human system messages.
            if m.get("subtype") or m.get("bot_id"):
                continue
            yield {
                "channel_id": channel_id,
                "user_id": m.get("user"),
                "text": m.get("text", ""),
                "ts": m.get("ts"),
                # 0 if no thread replies yet — used to skip already-answered Qs.
                "reply_count": m.get("reply_count", 0),
            }
        if not resp.get("has_more"):
            return
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return


def fetch_recent_messages(client, oldest_epoch):
    """Return all real user messages across member channels since oldest_epoch."""
    messages = []
    channel_count = 0
    for channel_id in _member_channels(client):
        channel_count += 1
        messages.extend(_channel_history(client, channel_id, oldest_epoch))
    logger.info(
        "Scanned %d member channel(s); fetched %d messages for the report window.",
        channel_count,
        len(messages),
    )
    return messages


def channel_report(client, oldest_epoch):
    """Diagnostic: per-channel read status, for /tasks/peek.

    For each member channel it reports any read error, the timestamp of the most
    recent message (regardless of window), and how many messages fall inside the
    window — so we can tell "quiet window" from "can't read history".
    """
    per_channel = []
    total = 0
    for channel_id in _member_channels(client):
        entry = {"channel_id": channel_id}
        try:
            # Latest message regardless of time — proves history is readable.
            latest = client.conversations_history(channel=channel_id, limit=1)
            lm = latest.get("messages", [])
            entry["latest_ts"] = lm[0].get("ts") if lm else None
            # Messages inside the report window.
            windowed = client.conversations_history(
                channel=channel_id, oldest=str(oldest_epoch), limit=200
            )
            wm = windowed.get("messages", [])
            entry["window_raw"] = len(wm)
            entry["window_human"] = sum(
                1 for m in wm if not (m.get("subtype") or m.get("bot_id"))
            )
            total += entry["window_human"]
        except SlackApiError as e:
            entry["error"] = e.response.get("error")
        per_channel.append(entry)
    return {
        "member_channels": len(per_channel),
        "total_messages": total,
        "per_channel": per_channel,
    }
