"""Optional AI message classification via OpenAI, with heuristic fallback.

Used ONLY at report time (the daily report), and only when OPENAI_API_KEY is
set. Messages are sent in batches with their channel name so the model can judge
whether each fits its channel and whether it needs a reply — far better than
keywords at spotting "this doesn't belong here".

Token-conscious by design:
  - runs at report time only (not per message, not every day unless you trigger it)
  - batches many messages per request (AI_BATCH_SIZE)
  - truncates long messages and uses a small model (OPENAI_MODEL)

If the key is missing, the openai package isn't installed, or a call fails, the
caller falls back to the keyword heuristics in analysis.py — nothing breaks.
"""

import json
import logging
import os

logger = logging.getLogger("tpf-community-bot.ai")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
AI_ENABLED = bool(OPENAI_API_KEY) and os.environ.get("AI_CLASSIFICATION", "on").lower() != "off"
BATCH_SIZE = int(os.environ.get("AI_BATCH_SIZE", "40"))
MAX_CHARS = 500  # cap per-message text sent to the model

_client = None

_SYSTEM = (
    "You help a product community manager review Slack. For each message you "
    "get its channel name and text. Judge it against what that channel is for.\n"
    "Return STRICT JSON: {\"results\":[{\"i\":int,\"official\":bool,"
    "\"out_of_place\":string|null,\"needs_reply\":bool}]} with one entry per "
    "input message.\n"
    "- official: true only for genuine announcements, decisions, launches, "
    "action items, or important updates worth surfacing.\n"
    "- out_of_place: a SHORT reason (max ~8 words) if the message does not fit "
    "the channel — e.g. promotion/self-promo in a discussion channel, a support "
    "question in an announcements channel, personal/off-topic chatter in a "
    "focused channel, or spam. Otherwise null.\n"
    "- needs_reply: true if it's a genuine question or request the community "
    "manager would likely want to respond to; false for statements/greetings.\n"
    "Be conservative: when unsure, use null/false. Do not invent entries."
)


def enabled():
    return AI_ENABLED


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI  # imported lazily so it's optional

        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _call(payload):
    resp = _get_client().chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps({"messages": payload})},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("results", [])


def classify_messages(messages, channel_name_of):
    """Return a list of verdicts parallel to ``messages``.

    Each verdict is ``{"official": bool, "out_of_place": str | None}``.
    Returns None on any failure / when disabled, so the caller falls back to
    the heuristics.
    """
    if not AI_ENABLED or not messages:
        return None

    verdicts = [None] * len(messages)
    try:
        for start in range(0, len(messages), BATCH_SIZE):
            chunk = messages[start : start + BATCH_SIZE]
            payload = [
                {
                    "i": start + idx,
                    "channel": channel_name_of(m.get("channel_id")),
                    "text": (m.get("text") or "")[:MAX_CHARS],
                }
                for idx, m in enumerate(chunk)
            ]
            for item in _call(payload):
                i = item.get("i")
                if isinstance(i, int) and 0 <= i < len(verdicts):
                    verdicts[i] = {
                        "official": bool(item.get("official")),
                        "out_of_place": item.get("out_of_place") or None,
                        "needs_reply": bool(item.get("needs_reply")),
                    }
    except Exception as e:  # any SDK/network/parse error → fall back
        logger.error("AI classification failed, using heuristics: %s", e)
        return None

    # Anything the model didn't return for → treat as not flagged.
    for i, v in enumerate(verdicts):
        if v is None:
            verdicts[i] = {"official": False, "out_of_place": None, "needs_reply": False}
    logger.info("AI classified %d messages.", len(messages))
    return verdicts
