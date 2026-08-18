"""Marking the posts we are not allowed to edit.

Every alert post made before this feature existed was written by Alertmanager's incoming webhook,
and Slack refuses a bot token an edit to those — ``cant_update_message``, verified 2026-07-23 and
pinned in ``tests/test_slack.py``. That verdict is permanent and retroactive: the red posts
already sitting in #alerts can never be turned green, by this service or any other.

What a bot *can* do to a post it did not author is react to it and reply in its thread. So the
backlog gets a ``:white_check_mark:`` and a one-line reply saying when the alert cleared. It is
visibly weaker than the green border new posts get, and it is the whole of what Slack allows.

Two properties this has to hold, because it runs against posts that accumulate:

**Idempotent.** A daily alert clearing every day would otherwise drop a fresh reply onto the same
month-old post 30 times. ``reactions.add`` is naturally safe (``already_reacted``); the thread
reply is not, so ``store.swept`` remembers what has been handled and this skips it.

**Bounded, and loudly so.** One page of history, one lookback window, one cap on matches. A sweep
that silently stopped at 200 messages would read as "the backlog is clean" when it is not, so
hitting a limit logs at WARNING and says what was left.

THE THROTTLE, which is the reason this file has a cache in it at all. Slack rate-limits
``conversations.history`` for non-Marketplace apps to roughly one call a minute, and it does NOT
answer 429 — it answers ``ok: true`` with an EMPTY ``messages`` list. Measured against this
workspace on 2026-08-18: the same query returned 166 messages, then 0, 0, 0, 0 at 15s intervals,
then 166 again at t+90s. An empty page is therefore almost always the throttle rather than an
empty channel, and a sweep that believed it would report "nothing to mark" and mean "I was not
allowed to look" — the precise failure mode this whole change exists to remove. So: results are
cached and shared across resolves, an empty page is retried rather than trusted, and giving up is
logged at WARNING.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import slack
from .render import RESOLVED_EMOJI
from .store import Store

log = logging.getLogger(__name__)

REACTION = "white_check_mark"

# How long a fetched page of history stays usable. A burst of resolves (a scheduler outage
# clearing six alerts at once) then costs ONE call instead of six, which is the difference between
# fitting inside the throttle and being locked out by it.
HISTORY_TTL_S = 300

# Retry budget for an empty page. Spaced past the ~60s throttle window, and only affordable
# because this runs on its own worker — see backend.run_sweep_worker.
HISTORY_ATTEMPTS = 4
HISTORY_BACKOFF_S = 45

# How far back a resolve looks for its own older posts. 30 days comfortably covers "these have
# built up over time" without asking Slack for a year of channel history on every resolve.
LOOKBACK_S = 30 * 24 * 3600

# Most posts to mark in one resolve. A cap this high is only reached by something that has been
# firing for weeks; if it IS reached, the log says so rather than pretending it finished.
MAX_MARKS = 50


class HistoryCache:
    """One page of #alerts, refetched rarely and shared by every sweep.

    ``fetch`` returns (messages, trustworthy). ``trustworthy`` is False only when every attempt
    came back empty — the caller must not read that as "no legacy posts", because it far more
    likely means the throttle never let us look.
    """

    def __init__(self, *, ttl_s: int = HISTORY_TTL_S) -> None:
        self._ttl = ttl_s
        self._cached: list[dict[str, Any]] = []
        self._fetched_at = 0.0

    def fetch(
        self,
        *,
        token: str,
        channel: str,
        lookback_s: int,
        attempts: int | None = None,
        backoff_s: float | None = None,
        sleeper=time.sleep,
    ) -> tuple[list[dict[str, Any]], bool]:
        # Read from the module at CALL time, not as default arguments — defaults are bound when
        # the function is defined, which would make these impossible to override in a test and
        # burn 135 real seconds per empty page.
        attempts = HISTORY_ATTEMPTS if attempts is None else attempts
        backoff_s = HISTORY_BACKOFF_S if backoff_s is None else backoff_s
        if self._cached and (time.time() - self._fetched_at) < self._ttl:
            return self._cached, True

        for attempt in range(attempts):
            messages = slack.history(
                token=token, channel=channel, oldest=time.time() - lookback_s
            )
            if messages:
                self._cached = messages
                self._fetched_at = time.time()
                return messages, True
            if attempt < attempts - 1:
                log.info(
                    "conversations.history came back empty (attempt %d/%d) — almost certainly the "
                    "~1/min throttle; retrying in %ss",
                    attempt + 1,
                    attempts,
                    backoff_s,
                )
                sleeper(backoff_s)

        log.warning(
            "conversations.history returned an empty page %d times for %s — treating the backlog "
            "as UNKNOWN rather than clean. Legacy posts were left unmarked.",
            attempts,
            channel,
        )
        return [], False


def matches(message: dict[str, Any], firing_title: str) -> bool:
    """True if this message is a firing post for the alert whose title is ``firing_title``.

    Matched on the attachment title, which ``render.title_for`` builds deterministically from
    service + alertname. Matching on text would drag in the resolve replies and any human comment
    that quoted the alert.
    """
    for attachment in message.get("attachments") or []:
        if isinstance(attachment, dict) and attachment.get("title") == firing_title:
            return True
    return False


def reply_text(resolved_at: int) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(resolved_at))
    return (
        f"{RESOLVED_EMOJI} *Resolved* — Alertmanager cleared this alert at {stamp}.\n"
        "_This post could not be recoloured: it was written by the old incoming webhook, "
        "which Slack will not let a bot edit._"
    )


def sweep(
    *,
    token: str,
    channel: str,
    store: Store,
    firing_title: str,
    skip_ts: set[str],
    resolved_at: int,
    cache: HistoryCache | None = None,
    lookback_s: int = LOOKBACK_S,
    max_marks: int = MAX_MARKS,
) -> int:
    """React + reply on every legacy post for this alert. Returns how many were marked."""
    cache = HistoryCache() if cache is None else cache
    messages, trustworthy = cache.fetch(token=token, channel=channel, lookback_s=lookback_s)
    if not messages:
        if not trustworthy:
            # Already logged at WARNING by the cache. Returning 0 here is a count, not a verdict.
            return 0
        return 0

    candidates = [
        m
        for m in messages
        if m.get("ts")
        and str(m["ts"]) not in skip_ts
        and matches(m, firing_title)
        and not store.already_swept(channel, str(m["ts"]))
    ]

    if len(candidates) > max_marks:
        log.warning(
            "sweep for %r found %d legacy posts, marking the %d newest — %d left unmarked",
            firing_title,
            len(candidates),
            max_marks,
            len(candidates) - max_marks,
        )
        candidates = candidates[:max_marks]

    marked = 0
    for message in candidates:
        ts = str(message["ts"])
        reacted = slack.add_reaction(token=token, channel=channel, ts=ts, name=REACTION)
        replied = slack.reply_in_thread(
            token=token, channel=channel, thread_ts=ts, text=reply_text(resolved_at)
        )
        if reacted or replied:
            # Recorded even on a partial success: a retry would re-reply, and a duplicate reply is
            # worse than a missing reaction.
            store.mark_swept(channel, ts)
            marked += 1

    if marked:
        log.info("swept %d legacy post(s) for %r", marked, firing_title)
    return marked
