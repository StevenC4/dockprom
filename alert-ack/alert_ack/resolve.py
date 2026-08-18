"""Turning a firing alert post into a resolved one. Pure, so it is testable without Slack.

The ask is visual: a green border and an unambiguous "this is handled" mark. In Slack the only
thing that draws a coloured border is an **attachment** `color` — Block Kit blocks have no
border — which is why the alert stays an attachment-shaped message instead of being modernised
into blocks on the way through.

This is an *acknowledgement*, not a state change in Alertmanager. If the underlying alert is
still firing, Alertmanager will notify again on its `repeat_interval` (24h) as a NEW post, which
will be red and carry its own button. That is deliberate: a green post here means "a human saw
this one", never "the problem is gone".
"""

from __future__ import annotations

import copy
from typing import Any

# Slack's own "good" green. Hard-coded rather than the `good` keyword so the resolved border is
# the same colour whether Slack is rendering a named or a hex attachment colour.
GREEN = "#2eb886"

# Alertmanager's title template opens with one of these (see dockprom/alertmanager/config.yml).
# Swapping it keeps the title from reading 🚨 against a green border. A title that does not start
# with the firing emoji is left alone — this is a narrow cosmetic rule, not a parser.
FIRING_EMOJI = ":rotating_light:"
RESOLVED_EMOJI = ":white_check_mark:"

# Slack renders attachment text as plain text unless the field is named here.
MRKDWN_FIELDS = ["fallback", "pretext", "text"]


def resolved_message(
    original: dict[str, Any], *, user_id: str | None, clicked_at: int
) -> dict[str, Any]:
    """The `chat.update` body that marks ``original`` resolved.

    Returns only the fields being rewritten (`text` + `attachments`). `chat.update` replaces the
    attachments wholesale, so every attachment is carried through — recoloured, disarmed, and
    with the acknowledgement appended to the first one.
    """
    attachments = copy.deepcopy(original.get("attachments") or [])

    if not attachments:
        # A message with no attachment cannot show a border at all. Rather than lose the ask,
        # wrap whatever text it had in one.
        attachments = [{"fallback": str(original.get("text") or ""),
                        "text": str(original.get("text") or "")}]

    for attachment in attachments:
        attachment["color"] = GREEN
        # Disarm it. The click has been honoured; leaving the button would invite a second one
        # that does nothing visible, which reads as a broken button.
        attachment.pop("actions", None)
        attachment.pop("callback_id", None)

        title = attachment.get("title")
        if isinstance(title, str) and title.startswith(FIRING_EMOJI):
            attachment["title"] = RESOLVED_EMOJI + title[len(FIRING_EMOJI):]

    first = attachments[0]
    first["text"] = _append_ack(str(first.get("text") or ""), user_id)
    first["mrkdwn_in"] = MRKDWN_FIELDS
    # Slack renders the attachment `ts` as a timestamp in the footer, so the acknowledgement
    # carries *when* without us formatting a date in the wrong timezone.
    first["ts"] = clicked_at
    first["footer"] = "Resolved in Slack — Alertmanager state unchanged"

    return {"text": str(original.get("text") or ""), "attachments": attachments}


def _append_ack(text: str, user_id: str | None) -> str:
    who = f" by <@{user_id}>" if user_id else ""
    ack = f"{RESOLVED_EMOJI} *Resolved{who}*"
    return f"{text}\n\n{ack}" if text else ack
