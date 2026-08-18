"""The two ways to rewrite a message that is already in the channel. stdlib only.

`response_url` is primary, because `chat.update` **cannot do this job**: the alert post is
written by an *incoming webhook*, and Slack refuses the bot token an edit to it —
`cant_update_message`, every time, verified against a real post on 2026-07-23. The response_url
is scoped to this one interaction and carries no such ownership question.

Its 30-minute expiry is not a constraint here, and the reason is worth stating because it is
easy to get backwards: **the window opens when the button is CLICKED, not when the alert was
posted.** A click on a three-day-old alert still yields a fresh response_url, and the worker
spends it about a second later. It would only matter if this service deferred the work.

`chat.update` stays as the fallback for the case the primary cannot cover: a post this button
is attached to that Slack *did* let us author (chat.postMessage rather than a webhook), where a
missing or spent response_url would otherwise leave the click with nowhere to go.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api/chat.update"
TIMEOUT_S = 10


def _post(url: str, body: dict[str, Any], token: str | None = None) -> tuple[bool, str]:
    raw = json.dumps(body).encode()
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            payload = response.read().decode()
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, f"transport: {exc}"

    # The Web API answers JSON; response_url answers the string "ok".
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return payload.strip() == "ok", payload.strip()

    if isinstance(parsed, dict) and not parsed.get("ok", False):
        return False, str(parsed.get("error") or payload)
    return True, "ok"


def update_message(
    *,
    token: str,
    channel: str,
    ts: str,
    message: dict[str, Any],
    response_url: str | None,
) -> bool:
    """Rewrite the message in place. True if either route landed it."""
    if response_url:
        ok, detail = _post(response_url, {"replace_original": True, **message})
        if ok:
            return True
        log.warning("response_url replace failed for %s/%s: %s", channel, ts, detail)

    ok, detail = _post(SLACK_API, {"channel": channel, "ts": ts, **message}, token=token)
    if not ok:
        # Expected, and harmless, for a webhook-authored post: `cant_update_message` here after
        # the primary already succeeded is not reachable — this line means BOTH routes failed.
        log.error("chat.update also failed for %s/%s: %s", channel, ts, detail)
    return ok


# --------------------------------------------------------------------------------------------
# Posting and marking. Everything below is for the auto-resolve path, not the Resolve button.
#
# `update_message` above stays exactly as it was, `SLACK_API` and all — its behaviour is pinned by
# tests/test_slack.py for a reason, and the button path has no need of any of this.
#
# The split in this file mirrors the split in the feature: posts WE author (chat.postMessage) can
# be edited green on resolve, and posts the old incoming webhook authored can only be reacted to
# and replied to. Slack draws that line, not us — see the module docstring.
# --------------------------------------------------------------------------------------------

API_BASE = "https://slack.com/api"


def _api(method: str, token: str, body: dict[str, Any]) -> tuple[bool, dict[str, Any], str]:
    """Call a Web API method. Returns (ok, parsed, error)."""
    raw = json.dumps(body).encode()
    request = urllib.request.Request(
        f"{API_BASE}/{method}",
        data=raw,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            parsed = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError) as exc:
        return False, {}, f"transport: {exc}"
    except json.JSONDecodeError as exc:
        return False, {}, f"bad json: {exc}"

    if not isinstance(parsed, dict) or not parsed.get("ok", False):
        error = str((parsed or {}).get("error") or "unknown")
        return False, parsed if isinstance(parsed, dict) else {}, error
    return True, parsed, ""


def post_message(*, token: str, channel: str, message: dict[str, Any]) -> str | None:
    """Post an alert. Returns the ``ts`` to remember it by, or None if it did not land."""
    ok, parsed, error = _api("chat.postMessage", token, {"channel": channel, **message})
    if not ok:
        log.error("chat.postMessage to %s failed: %s", channel, error)
        return None
    ts = parsed.get("ts")
    return str(ts) if ts else None


def reply_in_thread(*, token: str, channel: str, thread_ts: str, text: str) -> bool:
    """Reply under a post. Never broadcast — the channel already carried the alert once."""
    ok, _, error = _api(
        "chat.postMessage",
        token,
        {"channel": channel, "thread_ts": thread_ts, "text": text, "reply_broadcast": False},
    )
    if not ok:
        log.warning("thread reply on %s/%s failed: %s", channel, thread_ts, error)
    return ok


def add_reaction(*, token: str, channel: str, ts: str, name: str) -> bool:
    """React to a post. ``already_reacted`` counts as success — this runs more than once."""
    ok, _, error = _api("reactions.add", token, {"channel": channel, "timestamp": ts, "name": name})
    if ok or error == "already_reacted":
        return True
    log.warning("reactions.add on %s/%s failed: %s", channel, ts, error)
    return False


def history(*, token: str, channel: str, oldest: float, limit: int = 200) -> list[dict[str, Any]]:
    """Recent channel messages, newest first. One page — the sweep is bounded on purpose."""
    ok, parsed, error = _api(
        "conversations.history",
        token,
        {"channel": channel, "oldest": str(oldest), "limit": limit},
    )
    if not ok:
        # `not_in_channel` here means the bot was never invited to #alerts; that is a setup
        # problem, not a transient one, so it is worth an error rather than a debug line.
        log.error("conversations.history on %s failed: %s", channel, error)
        return []
    messages = parsed.get("messages")
    return [m for m in messages if isinstance(m, dict)] if isinstance(messages, list) else []
