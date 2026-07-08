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

# Casual / personal markers — fine in social channels, noisy elsewhere.
CASUAL_MARKERS = (
    "lol", "haha", "lunch", "coffee", "weekend", "🎉", "😂", "😅", "gm ",
    "good morning", "good night", "happy birthday", "party", "dinner",
)

# Promotional / self-promo markers — belong in a promotions channel, not in
# discussion/official channels.
PROMO_MARKERS = (
    "discount", "% off", "buy now", "sale", "promo", "coupon", "sign up now",
    "register now", "limited offer", "dm me", "check out my", "use code",
    "early bird", "book your", "enroll", "referral", "giveaway",
)

# Channel-name hints for where certain content *is* allowed.
PROMO_CHANNEL_HINTS = ("promo", "promotion", "marketing", "self-promo", "showcase", "advertise")
SOCIAL_CHANNEL_HINTS = ("random", "social", "watercooler", "off-topic", "fun", "lounge", "chai")


def _contains_any(text, needles):
    low = text.lower()
    return any(n in low for n in needles)


def _name_has(channel_name, hints):
    name = (channel_name or "").lower()
    return any(h in name for h in hints)


def classify_message(text, channel_name):
    """Classify a single message with simple rules.

    Returns a dict::

        {"official": bool, "out_of_place": str | None, "needs_reply": bool}

    ``out_of_place`` is a short human-readable reason, or None.
    ``needs_reply`` is True when the message reads like a question/request the
    POC may want to respond to.

    Future: replace this body with an LLM call that reads recent channel
    context and returns the same shape.
    """
    text = text or ""
    result = {"official": False, "out_of_place": None, "needs_reply": False}

    is_official_channel = _name_has(channel_name, OFFICIAL_CHANNEL_HINTS)
    is_promo_channel = _name_has(channel_name, PROMO_CHANNEL_HINTS)
    is_social_channel = _name_has(channel_name, SOCIAL_CHANNEL_HINTS)

    # --- Official update detection -------------------------------------
    if _contains_any(text, OFFICIAL_KEYWORDS) or "<!channel>" in text or "<!here>" in text:
        result["official"] = True

    # --- Needs-a-reply detection ---------------------------------------
    # A question or an explicit ask usually wants a response.
    if "?" in text or _contains_any(text, SUPPORT_KEYWORDS):
        result["needs_reply"] = True

    # --- Out-of-place detection ----------------------------------------
    # Promotion outside a promotions channel (the product-vs-promo example).
    if _contains_any(text, PROMO_MARKERS) and not is_promo_channel:
        result["out_of_place"] = "Promotional content outside a promotions channel"
    # Casual/social chatter outside a social channel.
    elif _contains_any(text, CASUAL_MARKERS) and not is_social_channel:
        result["out_of_place"] = "Casual/personal chatter outside a social channel"
    # A question or support request in a broadcast/official channel.
    elif is_official_channel and "?" in text:
        result["out_of_place"] = "Question posted in a broadcast/official channel"
    elif is_official_channel and _contains_any(text, SUPPORT_KEYWORDS):
        result["out_of_place"] = "Support/help request in an official channel"

    return result


def _shorten(text, limit=140):
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_digest(messages, channel_name_of, display_name_of, verdicts=None):
    """Build a per-channel end-of-day report.

    Groups all observed messages by channel and, for each channel, records how
    much happened plus anything flagged as an official update or as out of
    place (with the user who posted it).

    Args:
        messages: list of dicts with channel_id, user_id, text, ts.
        channel_name_of: fn(channel_id) -> readable channel name.
        display_name_of: fn(user_id) -> readable user name.
        verdicts: optional list parallel to ``messages`` of precomputed
            {"official": bool, "out_of_place": str|None, "needs_reply": bool}
            (e.g. from ai.py). If omitted, keyword heuristics are used.

    Returns a dict with a per-channel breakdown::

        {
          "channels": [
            {
              "channel": str, "message_count": int, "active_users": int,
              "highlights":   [ {user, text}, ... ],   # who said what
              "out_of_place": [ {user, text, reason}, ... ],
              "needs_reply":  [ {user, text}, ... ],   # unanswered questions
            }, ...
          ],
          "total": int,
        }
    """
    by_channel = {}

    for idx, m in enumerate(messages):
        channel_id = m.get("channel_id")
        channel = channel_name_of(channel_id)
        user = display_name_of(m.get("user_id"))
        text = (m.get("text") or "").strip()

        bucket = by_channel.setdefault(
            channel_id,
            {
                "channel": channel,
                "message_count": 0,
                "users": set(),
                "highlights": [],
                "out_of_place": [],
                "needs_reply": [],
            },
        )
        bucket["message_count"] += 1
        bucket["users"].add(m.get("user_id"))

        # Use the AI verdict if provided, else fall back to keyword heuristics.
        verdict = verdicts[idx] if verdicts is not None else classify_message(text, channel)

        # Who said what — surface noteworthy/official messages with attribution.
        if verdict.get("official"):
            bucket["highlights"].append({"user": user, "text": _shorten(text)})

        # Out-of-place — exact message so the POC can judge it.
        if verdict.get("out_of_place"):
            bucket["out_of_place"].append(
                {"user": user, "text": text, "reason": verdict["out_of_place"]}
            )

        # Needs a reply — only flag if it hasn't already been answered in a
        # thread (reply_count == 0). Channel replies can't be detected, so this
        # may still include some that got an inline answer.
        if verdict.get("needs_reply") and not m.get("reply_count"):
            bucket["needs_reply"].append({"user": user, "text": _shorten(text)})

    channels = []
    for bucket in by_channel.values():
        channels.append(
            {
                "channel": bucket["channel"],
                "message_count": bucket["message_count"],
                "active_users": len(bucket["users"]),
                "highlights": bucket["highlights"],
                "out_of_place": bucket["out_of_place"],
                "needs_reply": bucket["needs_reply"],
            }
        )
    channels.sort(key=lambda c: c["message_count"], reverse=True)

    return {"channels": channels, "total": len(messages)}


def count_flags(digest):
    """Total number of out-of-place items across all channels."""
    return sum(len(ch["out_of_place"]) for ch in digest["channels"])


def count_replies(digest):
    """Total number of messages flagged as needing a reply."""
    return sum(len(ch["needs_reply"]) for ch in digest["channels"])


def format_daily_report(digest, new_members=None, period_label="today"):
    """Render the daily report for the POC DM.

    Sections: new members today, per-channel who-said-what, messages that look
    out of place, and messages that may need a reply. new_members is a list of
    readable names (strings).
    """
    lines = [f"📋 *Daily report* — {period_label}"]
    lines.append(
        f"_{digest['total']} message(s) across {len(digest['channels'])} channel(s)._"
    )

    # --- New members ---------------------------------------------------
    lines.append("\n🙋 *New members today*")
    if new_members:
        for name in new_members:
            lines.append(f"   • {name}")
        lines.append("   _Reach out and welcome them personally._")
    else:
        lines.append("   _No new members today._")

    # --- Per-channel activity -----------------------------------------
    if not digest["channels"]:
        lines.append("\n_No channel activity to report._")
    for ch in digest["channels"]:
        lines.append(
            f"\n*#{ch['channel']}* — {ch['message_count']} message(s), "
            f"{ch['active_users']} member(s) active"
        )
        if ch["highlights"]:
            lines.append("  🗣️ *Who said what:*")
            for item in ch["highlights"]:
                lines.append(f"   • {item['user']}: {item['text']}")
        if ch["needs_reply"]:
            lines.append("  💬 *Might need a reply:*")
            for item in ch["needs_reply"]:
                lines.append(f"   • {item['user']}: {item['text']}")
        if ch["out_of_place"]:
            lines.append("  ⚠️ *Looks out of place:*")
            for item in ch["out_of_place"]:
                lines.append(
                    f"   • {item['user']}: {item['text']}\n"
                    f"      ↳ _Why:_ {item['reason']}"
                )
        if not (ch["highlights"] or ch["needs_reply"] or ch["out_of_place"]):
            lines.append("  _Normal activity, nothing flagged._")

    lines.append(
        "\n_Flags are for your review — nothing was sent or actioned. "
        "You decide what to do._"
    )
    return "\n".join(lines)
