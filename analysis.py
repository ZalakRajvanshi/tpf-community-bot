"""Non-LLM heuristics for turning observed messages into POC-ready context.

This is deliberately simple, rule-based logic — no AI yet. The goal is a clean,
single place to reason about messages so it can later be swapped for a real
language model. To add AI understanding, replace the bodies of
``classify_message`` and ``summarize`` (and keep their signatures) — nothing
else in the app needs to change.

These rules are heuristics, not judgements: they only *flag candidates* for a
human to look at. The Human POC always decides what (if anything) to do.
"""

import re

# Channels whose name suggests broadcast/official use. Questions, chit-chat, or
# support requests in these are candidates for "out of place".
OFFICIAL_CHANNEL_HINTS = (
    "announce", "official", "general", "updates", "news", "leadership",
)

# Words that hint a message is an official update worth surfacing.
OFFICIAL_KEYWORDS = (
    "announce", "announcement", "update", "released", "launching", "launch",
    "decision", "decided", "action item", "deadline", "roadmap", "milestone",
    "shipped", "rollout", "important", "heads up", "fyi", "blocker", "outage",
)

# Words that hint a message is a support/help request (often posted in the
# wrong channel).
SUPPORT_KEYWORDS = (
    "help", "how do i", "how to", "can someone", "not working", "broken",
    "error", "issue", "stuck", "anyone know", "support", "bug",
)

# Casual / personal markers — fine in social channels, noisy in official ones.
CASUAL_MARKERS = (
    "lol", "haha", "lunch", "coffee", "weekend", "🎉", "😂", "😅", "gm ",
    "good morning", "good night", "happy birthday",
)


def _contains_any(text, needles):
    low = text.lower()
    return any(n in low for n in needles)


def _looks_official_channel(channel_name):
    name = (channel_name or "").lower()
    return any(hint in name for hint in OFFICIAL_CHANNEL_HINTS)


def classify_message(text, channel_name):
    """Classify a single message with simple rules.

    Returns a dict::

        {"official": bool, "out_of_place": str | None}

    ``out_of_place`` is a short human-readable reason, or None.

    Future: replace this body with an LLM call that reads recent channel
    context and returns the same shape.
    """
    text = text or ""
    result = {"official": False, "out_of_place": None}

    is_official_channel = _looks_official_channel(channel_name)

    # --- Official update detection -------------------------------------
    if _contains_any(text, OFFICIAL_KEYWORDS) or "<!channel>" in text or "<!here>" in text:
        result["official"] = True

    # --- Out-of-place detection ----------------------------------------
    # A question in a broadcast/official channel usually belongs elsewhere.
    if is_official_channel and "?" in text:
        result["out_of_place"] = "Question posted in a broadcast/official channel"
    elif is_official_channel and _contains_any(text, SUPPORT_KEYWORDS):
        result["out_of_place"] = "Support/help request in an official channel"
    elif is_official_channel and _contains_any(text, CASUAL_MARKERS):
        result["out_of_place"] = "Casual/personal chatter in an official channel"

    return result


def _shorten(text, limit=140):
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_digest(messages, channel_name_of, display_name_of):
    """Build a per-channel end-of-day report.

    Groups all observed messages by channel and, for each channel, records how
    much happened plus anything flagged as an official update or as out of
    place (with the user who posted it).

    Args:
        messages: list of dicts with channel_id, user_id, text, ts.
        channel_name_of: fn(channel_id) -> readable channel name.
        display_name_of: fn(user_id) -> readable user name.

    Returns a dict::

        {
          "channels": [
            {
              "channel": str,
              "message_count": int,
              "active_users": int,
              "official":     [ {user, text}, ... ],
              "out_of_place": [ {user, text, reason}, ... ],
            }, ...
          ],
          "total": int,
        }
    """
    by_channel = {}

    for m in messages:
        channel_id = m.get("channel_id")
        channel = channel_name_of(channel_id)
        user = display_name_of(m.get("user_id"))

        bucket = by_channel.setdefault(
            channel_id,
            {
                "channel": channel,
                "message_count": 0,
                "users": set(),
                "official": [],
                "out_of_place": [],
            },
        )
        bucket["message_count"] += 1
        bucket["users"].add(m.get("user_id"))

        verdict = classify_message(m.get("text"), channel)
        if verdict["official"]:
            bucket["official"].append({"user": user, "text": _shorten(m.get("text"))})
        if verdict["out_of_place"]:
            bucket["out_of_place"].append(
                {
                    "user": user,
                    "text": _shorten(m.get("text")),
                    "reason": verdict["out_of_place"],
                }
            )

    # Sort channels by how busy they were, most active first.
    channels = []
    for bucket in by_channel.values():
        channels.append(
            {
                "channel": bucket["channel"],
                "message_count": bucket["message_count"],
                "active_users": len(bucket["users"]),
                "official": bucket["official"],
                "out_of_place": bucket["out_of_place"],
            }
        )
    channels.sort(key=lambda c: c["message_count"], reverse=True)

    return {"channels": channels, "total": len(messages)}


def format_digest(digest, period_label="the last 24 hours"):
    """Render the per-channel digest as Slack markdown for the POC DM."""
    lines = [f"🗒️ *Daily digest* — {period_label}"]
    lines.append(
        f"_Observed {digest['total']} message(s) across "
        f"{len(digest['channels'])} channel(s)._"
    )

    if not digest["channels"]:
        lines.append("\n_No channel activity to report._")
        return "\n".join(lines)

    for ch in digest["channels"]:
        lines.append(
            f"\n*#{ch['channel']}* — {ch['message_count']} message(s), "
            f"{ch['active_users']} member(s) active"
        )

        if ch["official"]:
            lines.append("  📢 *Official / noteworthy:*")
            for item in ch["official"]:
                lines.append(f"   • {item['user']}: {item['text']}")

        if ch["out_of_place"]:
            lines.append("  ⚠️ *Looks out of place:*")
            for item in ch["out_of_place"]:
                lines.append(
                    f"   • {item['user']}: {item['text']}\n"
                    f"      ↳ _Why:_ {item['reason']}"
                )

        if not ch["official"] and not ch["out_of_place"]:
            lines.append("  _Normal activity, nothing flagged._")

    lines.append(
        "\n_These are heuristic flags for your review — no action was taken. "
        "You decide what happens next._"
    )
    return "\n".join(lines)
