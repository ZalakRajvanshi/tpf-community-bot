"""Slack request signature verification.

Implements the signing-secret scheme described at
https://api.slack.com/authentication/verifying-requests-from-slack so we can
confirm that incoming requests really came from Slack before acting on them.
"""

import hashlib
import hmac
import time


def is_valid_slack_request(
    signing_secret,
    request_body,
    timestamp,
    signature,
    max_age_seconds=60 * 5,
):
    """Return True if the request carries a valid Slack signature.

    Args:
        signing_secret: The app's Slack signing secret.
        request_body: The raw (unparsed) request body, bytes or str.
        timestamp: Value of the X-Slack-Request-Timestamp header.
        signature: Value of the X-Slack-Signature header.
        max_age_seconds: Reject requests older than this (replay protection).
    """
    if not signing_secret or not timestamp or not signature:
        return False

    # Reject stale timestamps to guard against replay attacks.
    try:
        if abs(time.time() - int(timestamp)) > max_age_seconds:
            return False
    except ValueError:
        return False

    body = request_body.decode("utf-8") if isinstance(request_body, bytes) else request_body

    base_string = f"v0:{timestamp}:{body}"
    computed_signature = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to avoid timing attacks.
    return hmac.compare_digest(computed_signature, signature)
