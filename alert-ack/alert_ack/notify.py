"""The Alertmanager webhook path: post firing alerts, and green them when they clear.

This is the half of the service that IS in the alerting path, and it is worth being explicit that
this is a reversal. ``alertmanager/config.yml`` rejected putting a container between the alert and
Slack, because one did take alerts down for nine days in July 2026, and ``backend.py`` still says
"nothing in the alerting path depends on it". That was true of the Resolve button and is no longer
true of this module.

It was reversed knowingly, for a reason Slack forces: a post made by an incoming webhook can never
be edited by a bot, so auto-resolve is impossible unless the post is ours. The mitigation is not
that this service became reliable — it is that Slack is no longer the only delivery. Every route
that reaches here also carries ``email_configs``, so a dead alert-ack costs the Slack rendering
and nothing else, and Alertmanager's own
``alertmanager_notifications_failed_total{integration="webhook"}`` feeds the ``service="alerting"``
route that pages by email when a notifier stops working.

Resolved notifications are not subject to ``repeat_interval`` — Alertmanager sends them once, as
soon as the group clears — so the daily cap on repeats does not delay any of this.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, NamedTuple

from . import slack, sweep
from .render import firing_message, title_for
from .resolve import resolved_message
from .store import Store

log = logging.getLogger(__name__)

# `resolve.py`'s footer is written for a human pressing the button, where Alertmanager genuinely
# does not know anything happened. Here Alertmanager is the one telling us, so saying its state is
# unchanged would be exactly backwards.
AUTO_FOOTER = "Resolved by Alertmanager — the alert stopped firing"


class SweepTask(NamedTuple):
    """The legacy-backlog half of a resolve, split out so it can be done off this thread.

    Greening our own posts is fast and bounded — a handful of chat.update calls. Marking the
    legacy backlog is neither: it needs conversations.history, which Slack throttles to about one
    call a minute and answers with an empty page rather than a 429 (see sweep.py). Retrying that
    inline would stall the notify worker for minutes, and that worker is what posts NEW alerts.

    So the fast half runs inline and the slow half is handed to a separate worker. A resolve is
    delivered immediately; the backlog catches up a minute later.
    """

    firing_title: str
    skip_ts: frozenset[str]
    resolved_at: int


def _common(payload: dict[str, Any]) -> tuple[str, str]:
    common = payload.get("commonLabels") or {}
    return str(common.get("service") or ""), str(common.get("alertname") or "")


def handle_firing(
    payload: dict[str, Any], *, token: str, channel: str, store: Store
) -> str | None:
    group_key = str(payload.get("groupKey") or "")
    ts = slack.post_message(
        token=token, channel=channel, message=firing_message(payload)
    )
    if ts is None:
        return None
    if group_key:
        store.record(group_key, channel, ts)
    return ts


def handle_resolved(
    payload: dict[str, Any],
    *,
    token: str,
    channel: str,
    store: Store,
    now: int | None = None,
    enqueue_sweep: Callable[[SweepTask], None] | None = None,
) -> tuple[int, int | None]:
    """Green everything we authored, then mark what we could not.

    Returns (greened, swept). ``swept`` is None when the backlog half was handed to the sweep
    worker instead of being done here — deferred, not zero.
    """
    now = int(time.time()) if now is None else now
    group_key = str(payload.get("groupKey") or "")
    service, alertname = _common(payload)

    # Rebuild the post as it looked while firing, then run it through the same transform the
    # Resolve button uses. Reusing that keeps one definition of "what resolved looks like".
    firing = firing_message({**payload, "status": "firing"})
    message = resolved_message(firing, user_id=None, clicked_at=now)
    for attachment in message.get("attachments") or []:
        attachment["footer"] = AUTO_FOOTER

    ours = store.posts_for(group_key) if group_key else []
    greened = 0
    for post_channel, ts in ours:
        if slack.update_message(
            token=token, channel=post_channel, ts=ts, message=message, response_url=None
        ):
            greened += 1
        else:
            log.warning("could not green our own post %s/%s", post_channel, ts)

    task = SweepTask(
        firing_title=title_for("firing", service, alertname),
        skip_ts=frozenset(ts for _, ts in ours),
        resolved_at=now,
    )

    if enqueue_sweep is not None:
        enqueue_sweep(task)
        swept: int | None = None
    else:
        swept = sweep.sweep(
            token=token,
            channel=channel,
            store=store,
            firing_title=task.firing_title,
            skip_ts=set(task.skip_ts),
            resolved_at=task.resolved_at,
        )

    if group_key:
        store.forget(group_key)
    log.info(
        "resolved %s: greened %d, legacy sweep %s",
        group_key or alertname,
        greened,
        "queued" if swept is None else f"marked {swept}",
    )
    return greened, swept


def handle(
    payload: dict[str, Any],
    *,
    token: str,
    channel: str,
    store: Store,
    enqueue_sweep: Callable[[SweepTask], None] | None = None,
) -> None:
    status = str(payload.get("status") or "")
    if status == "firing":
        handle_firing(payload, token=token, channel=channel, store=store)
    elif status == "resolved":
        handle_resolved(
            payload, token=token, channel=channel, store=store, enqueue_sweep=enqueue_sweep
        )
    else:
        log.warning("ignoring alertmanager webhook with status %r", status)
