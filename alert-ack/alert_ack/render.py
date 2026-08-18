"""An Alertmanager webhook payload -> the Slack message it used to post itself.

This is a PORT, not a redesign. Until now Alertmanager rendered these posts from the Go templates
in ``alertmanager/config.yml`` and sent them down an incoming webhook. Those posts are authored by
the webhook, and Slack will not let a bot token edit them (``cant_update_message`` — see
``slack.py``), which is exactly why they can never be auto-resolved. Moving the rendering here and
posting with ``chat.postMessage`` makes the post ours, and therefore editable.

Because it is a port, the output has to LOOK the same. Anything that drifts shows up as a visible
change in #alerts for alerts that have nothing to do with this feature. The title especially:
``sweep.py`` finds the old webhook-authored posts by matching this exact string, so a cosmetic
tweak here silently stops the backlog sweep from recognising anything.

Deliberate deviation, one: the Go template is a YAML folded scalar (``>-``), which collapses its
newlines into spaces and produces a single run-on paragraph. Rendering here is line-oriented, so
multi-alert groups read as a list instead of a wall. The words are identical; only the wrapping
differs, and it differs in the direction of being readable.
"""

from __future__ import annotations

from typing import Any

# Slack's own palette. `danger` and `good` as hex so the border is the same colour regardless of
# whether Slack is resolving a keyword or a literal for us.
DANGER = "#a30200"

FIRING_EMOJI = ":rotating_light:"
RESOLVED_EMOJI = ":white_check_mark:"

# Mention on FIRING CRITICAL only. Pinging on warnings and on every resolve is how a mention gets
# tuned out — the reasoning, and the uid, come straight from the config.yml template.
MENTION = "<@U0BGX8MV8EA>"

MRKDWN_FIELDS = ["fallback", "pretext", "text"]


def title_for(status: str, service: str, alertname: str) -> str:
    """The post title. Also the key ``sweep.py`` matches legacy posts on — keep it deterministic."""
    emoji = FIRING_EMOJI if status == "firing" else RESOLVED_EMOJI
    return f"{emoji} NightHawk22 · {service} — {alertname}"


def _alert_block(alert: dict[str, Any]) -> str:
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}

    severity = str(labels.get("severity") or "").upper()
    source = str(labels.get("source") or "")
    summary = str(annotations.get("summary") or "")
    description = str(annotations.get("description") or "")
    runbook = str(annotations.get("runbook") or "")

    head = f"*{severity}*" if severity else ""
    if source:
        head = f"{head} [{source}]".strip()
    line = f"{head} {summary}".strip() if summary else head

    parts = [p for p in (line, description) if p]
    if runbook:
        parts.append(f"*Runbook:* {runbook}")
    return "\n".join(parts)


def firing_text(payload: dict[str, Any]) -> str:
    common = payload.get("commonLabels") or {}
    status = str(payload.get("status") or "")

    blocks = [_alert_block(a) for a in (payload.get("alerts") or [])]
    body = "\n\n".join(b for b in blocks if b)

    if status == "firing" and str(common.get("severity") or "") == "critical":
        return f"{MENTION} {body}" if body else MENTION
    return body


def firing_message(payload: dict[str, Any]) -> dict[str, Any]:
    """The ``chat.postMessage`` body for a firing group.

    Shaped as an attachment rather than Block Kit for the same reason the Go template was: an
    attachment ``color`` is the only thing in Slack that draws a coloured border, and the border
    is the whole point. Keeping the legacy attachment action also means the existing Resolve
    button — and the whole gateway path that services it — keeps working untouched.
    """
    common = payload.get("commonLabels") or {}
    service = str(common.get("service") or "")
    alertname = str(common.get("alertname") or "")
    status = str(payload.get("status") or "firing")

    title = title_for(status, service, alertname)
    text = firing_text(payload)

    attachment: dict[str, Any] = {
        "color": DANGER,
        "title": title,
        "text": text,
        "fallback": f"{title} — {text}"[:300],
        "mrkdwn_in": MRKDWN_FIELDS,
        # The routing key the gateway unicasts on, unchanged from config.yml.
        "callback_id": f"alert:{service}",
        "actions": [
            {
                "type": "button",
                "name": "alert:resolve",
                "text": "Mark resolved",
                "value": f"{status}:{alertname}",
                "style": "primary",
            }
        ],
    }
    # `text` at the top level is what Slack shows in notifications and the sidebar; without it a
    # push notification reads "sent an attachment".
    return {"text": title, "attachments": [attachment]}
