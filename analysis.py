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
    """Group classified messages into the two digest sections.

    Args:
        messages: list of dicts with channel_id, user_id, text, ts.
        channel_name_of: fn(channel_id) -> readable channel name.
        display_name_of: fn(user_id) -> readable user name.

    Returns a dict::

        {
          "official":     [ {channel, user, text}, ... ],
          "out_of_place": [ {channel, user, text, reason}, ... ],
          "total": int,
        }
    """
    official = []
    out_of_place = []

    for m in messages:
        channel = channel_name_of(m.get("channel_id"))
        user = display_name_of(m.get("user_id"))
        verdict = classify_message(m.get("text"), channel)

        if verdict["official"]:
            official.append(
                {"channel": channel, "user": user, "text": _shorten(m.get("text"))}
            )
        if verdict["out_of_place"]:
            out_of_place.append(
                {
                    "channel": channel,
                    "user": user,
                    "text": _shorten(m.get("text")),
                    "reason": verdict["out_of_place"],
                }
            )

    return {"official": official, "out_of_place": out_of_place, "total": len(messages)}


def format_digest(digest, period_label="the last 24 hours"):
    """Render the digest dict as Slack markdown for the POC DM."""
    lines = [f"🗒️ *Daily digest* — {period_label}"]
    lines.append(f"_Observed {digest['total']} message(s)._\n")

    # Section A — official updates.
    lines.append("*A. Official Updates*")
    if digest["official"]:
        for item in digest["official"]:
            lines.append(f"• *#{item['channel']}* — {item['user']}: {item['text']}")
    else:
        lines.append("_Nothing flagged as an official update._")

    # Section B — out-of-place activity.
    lines.append("\n*B. Out-of-Context Activity*")
    if digest["out_of_place"]:
        for item in digest["out_of_place"]:
            lines.append(
                f"• *#{item['channel']}* — {item['user']}: {item['text']}\n"
                f"   ↳ _Why:_ {item['reason']}"
            )
    else:
        lines.append("_Nothing looked out of place._")

    lines.append(
        "\n_These are heuristic flags for your review — no action was taken. "
        "You decide what happens next._"
    )
    return "\n".join(lines)
