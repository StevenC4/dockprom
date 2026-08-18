"""The Slack backend — ``POST /slack/dispatch``, behind homelab-slack-gateway.

Contract: homelab-slack-gateway/docs/slack-gateway-spec.md.

  * verify the HMAC over the RAW bytes, enforce a 300s skew window, then parse
  * return 200 as soon as the click is queued, and do **no network I/O before it** — the gateway
    is holding Slack's 3s ack budget open while it waits
  * rewrite the post promptly after that 200. The edit rides the click's `response_url` (see
    slack.py — chat.update cannot edit a webhook-authored post), and that URL is good for 30
    minutes from the click, so the work must not be deferred beyond it
  * handle ONLY interactivity whose namespace is ``alert``; the gateway unicasts by that prefix

**Deviation from the spec, deliberately.** Other backends persist the click durably before
answering 200; this one queues it in memory. The "work" is a cosmetic edit to a Slack message,
interactivity is never replayed, and the button is only removed by the edit itself — so the cost
of losing a click to a crash is that the post stays red and the human clicks again. A SQLite file
and a volume to protect that is not a trade worth making.

This service subscribes to no Slack *events*, and nothing in the alerting path depends on it:
Alertmanager posts to #alerts through its own incoming webhook. If this container is down, alerts
still arrive in full — the Resolve button just does nothing until it is back. That is the whole
reason the button hangs off the existing webhook post instead of this service posting the alert.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import signal
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, NamedTuple

from .notify import SweepTask
from .notify import handle as handle_notification
from .resolve import resolved_message
from .sign import SIGNATURE_HEADER, TIMESTAMP_HEADER, verify
from .slack import update_message
from .store import Store
from .sweep import HistoryCache
from .sweep import sweep as run_sweep

log = logging.getLogger(__name__)

DISPATCH_PATH = "/slack/dispatch"

# The Alertmanager webhook. A DIFFERENT trust model from the dispatch path above and deliberately
# so: that one is signed by homelab-slack-gateway with a shared HMAC, this one is Alertmanager on
# the monitor-net bridge, which speaks only bearer auth (`http_config.authorization`). Neither
# secret is accepted on the other's path.
ALERTMANAGER_PATH = "/alertmanager"

MAX_BODY_BYTES = 1024 * 1024

NAMESPACE = "alert"
RESOLVE_ACTION = "alert:resolve"

# Bounded, so a Slack outage that wedges the worker cannot grow the heap without limit. A full
# queue means something is badly wrong; dropping loudly beats dying quietly.
QUEUE_DEPTH = 100


class NotifyJob(NamedTuple):
    payload: dict[str, Any]


class Job(NamedTuple):
    channel: str
    ts: str
    original: dict[str, Any]
    user_id: str | None
    response_url: str | None
    label: str


def accept(work: queue.Queue[Job], envelope: dict[str, Any]) -> None:
    """Queue a Resolve click. Raises if it cannot be queued, so the caller can answer 500."""
    if envelope.get("kind") != "interactivity":
        log.debug("ignoring dispatch of kind %r", envelope.get("kind"))
        return

    payload = envelope.get("slack") or {}

    # Legacy attachment actions, not Block Kit. Alertmanager's Slack integration can only emit
    # attachments (it has no `blocks` field), and an attachment is also the only thing in Slack
    # that has a coloured border — so the button it renders is an `interactive_message`.
    if payload.get("type") != "interactive_message":
        log.info("ignoring interactivity type %r", payload.get("type"))
        return

    callback_id = str(payload.get("callback_id") or "")
    if not callback_id.startswith(NAMESPACE + ":"):
        log.warning("ignoring interactivity %r — not ours", callback_id)
        return

    actions = payload.get("actions") or []
    name = str(actions[0].get("name") or "") if actions and isinstance(actions[0], dict) else ""
    if name != RESOLVE_ACTION:
        log.warning("ignoring unknown action %r on %r", name, callback_id)
        return

    original = payload.get("original_message")
    channel = (payload.get("channel") or {}).get("id")
    ts = payload.get("message_ts")
    if not isinstance(original, dict) or not channel or not ts:
        # Nothing to rewrite. Ephemeral messages arrive without original_message.
        log.warning("resolve click on %r carried no editable message", callback_id)
        return

    work.put_nowait(
        Job(
            channel=str(channel),
            ts=str(ts),
            original=original,
            user_id=(payload.get("user") or {}).get("id"),
            response_url=payload.get("response_url"),
            label=callback_id,
        )
    )


def accept_notification(work: queue.Queue[NotifyJob], payload: dict[str, Any]) -> None:
    """Queue an Alertmanager group notification. Raises if it cannot be queued."""
    status = str(payload.get("status") or "")
    if status not in ("firing", "resolved"):
        log.warning("ignoring alertmanager webhook with status %r", status)
        return
    work.put_nowait(NotifyJob(payload=payload))


def run_sweep_worker(
    work: queue.Queue[SweepTask],
    token: str,
    channel: str,
    store: Store,
    stop: threading.Event,
) -> None:
    """The legacy-backlog half of a resolve, kept off the notify worker on purpose.

    It blocks for minutes at a time: conversations.history is throttled to roughly one call a
    minute and answers an empty page rather than a 429, so finding the backlog means retrying with
    backoff. Doing that on the notify worker would stall the posting of NEW alerts behind it,
    which is the one thing this service must never do.

    The HistoryCache is owned here, and being a single worker is what makes it worth having: six
    alerts clearing together share one fetched page instead of racing for six throttled ones.
    """
    cache = HistoryCache()
    while not stop.is_set():
        try:
            task = work.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            run_sweep(
                token=token,
                channel=channel,
                store=store,
                firing_title=task.firing_title,
                skip_ts=set(task.skip_ts),
                resolved_at=task.resolved_at,
                cache=cache,
            )
        except Exception:  # noqa: BLE001
            log.exception("legacy sweep failed for %r", task.firing_title)
        finally:
            work.task_done()


def run_notify_worker(
    work: queue.Queue[NotifyJob],
    token: str,
    channel: str,
    store: Store,
    stop: threading.Event,
    sweep_work: queue.Queue[SweepTask] | None = None,
) -> None:
    """SINGLE thread, on purpose: a resolve must not overtake the firing post it has to edit.

    One queue processed in order is the whole ordering guarantee. Widening this to a pool would
    let `resolved` for a group run before `firing` had recorded its ts, and the post would stay
    red with nothing left to say it should not be.
    """
    while not stop.is_set():
        try:
            job = work.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            enqueue = None
            if sweep_work is not None:
                def enqueue(task: SweepTask, _q=sweep_work) -> None:
                    try:
                        _q.put_nowait(task)
                    except queue.Full:
                        # The greening already happened; only the legacy marking is lost, and it
                        # is better to say so than to block the notify worker behind it.
                        log.error("sweep queue full — legacy backlog for %r left unmarked",
                                  task.firing_title)

            handle_notification(
                job.payload, token=token, channel=channel, store=store, enqueue_sweep=enqueue
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to handle alertmanager notification")
        finally:
            work.task_done()


def run_worker(work: queue.Queue[Job], token: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            job = work.get(timeout=1.0)
        except queue.Empty:
            continue

        try:
            message = resolved_message(
                job.original, user_id=job.user_id, clicked_at=int(time.time())
            )
            ok = update_message(
                token=token,
                channel=job.channel,
                ts=job.ts,
                message=message,
                response_url=job.response_url,
            )
            log.info(
                "resolve %s (%s/%s) -> %s", job.label, job.channel, job.ts, "ok" if ok else "FAILED"
            )
        except Exception:  # noqa: BLE001
            # One poisoned click must not take the worker down with it — the next Resolve press
            # in an hour's time still has to work.
            log.exception("failed to mark %s resolved", job.label)
        finally:
            work.task_done()


class _Handler(BaseHTTPRequestHandler):
    work: queue.Queue[Job]
    secret: str
    notify_work: queue.Queue[NotifyJob] | None = None
    webhook_token: str = ""
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        if self.path == ALERTMANAGER_PATH:
            self._do_alertmanager()
            return
        if self.path != DISPATCH_PATH:
            self._respond(404, b"not found")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._respond(400, b"bad content-length")
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._respond(400, b"bad content-length")
            return

        raw = self.rfile.read(length)

        # Verify over the RAW bytes, before parsing — re-serializing a parse changes key order and
        # whitespace, and the HMAC would never match.
        if not verify(
            self.secret,
            self.headers.get(TIMESTAMP_HEADER),
            self.headers.get(SIGNATURE_HEADER),
            raw,
        ):
            log.warning("rejected unsigned/stale dispatch from %s", self.client_address[0])
            self._respond(401, b"bad signature")
            return

        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            self._respond(400, b"bad json")
            return

        try:
            accept(self.work, envelope)
        except queue.Full:
            log.error("work queue full — dropping a resolve click")
            self._respond(500, b"queue full")
            return
        except Exception:  # noqa: BLE001
            log.exception("failed to queue dispatch")
            self._respond(500, b"queue failed")
            return

        # Empty ack. There is no modal and no response_action to relay; the visible answer is the
        # message edit the worker makes a moment later.
        self._respond(200, b"")

    def _do_alertmanager(self) -> None:
        """Alertmanager's webhook. Bearer-authenticated, queued, acked immediately.

        Alertmanager retries a non-2xx, so anything we can still recover from must NOT 200 —
        but anything malformed must, or it retries the same bad body forever.
        """
        if self.notify_work is None:
            # Deployed without ALERT_CHANNEL / ALERTMANAGER_WEBHOOK_TOKEN. 503 rather than 404 so
            # Alertmanager retries and the failure shows up in its notify metrics.
            self._respond(503, b"alertmanager path not configured")
            return

        expected = f"Bearer {self.webhook_token}"
        presented = self.headers.get("Authorization") or ""
        if not hmac.compare_digest(presented, expected):
            log.warning("rejected unauthenticated alertmanager post from %s", self.client_address[0])
            self._respond(401, b"bad token")
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._respond(400, b"bad content-length")
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._respond(400, b"bad content-length")
            return

        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            # 200: a body we cannot parse will not parse on retry either.
            self._respond(200, b"")
            return
        if not isinstance(payload, dict):
            self._respond(200, b"")
            return

        try:
            accept_notification(self.notify_work, payload)
        except queue.Full:
            log.error("notify queue full — telling Alertmanager to retry")
            self._respond(503, b"queue full")
            return
        except Exception:  # noqa: BLE001
            log.exception("failed to queue alertmanager notification")
            self._respond(500, b"queue failed")
            return

        self._respond(200, b"")

    def _respond(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        log.debug("http %s", fmt % args)


def build_server(
    work: queue.Queue[Job],
    secret: str,
    host: str,
    port: int,
    notify_work: queue.Queue[NotifyJob] | None = None,
    webhook_token: str = "",
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler",
        (_Handler,),
        {
            "work": work,
            "secret": secret,
            "notify_work": notify_work,
            "webhook_token": webhook_token,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    secret = os.environ["HOMELAB_GATEWAY_SECRET"]
    token = os.environ["SLACK_BOT_TOKEN"]

    # A placeholder left in env/alert-ack.env produces a button that looks fine and silently
    # fails on click, hours later, in a log nobody is reading. Say so at boot instead.
    if not token.startswith("xoxb-"):
        log.warning(
            "SLACK_BOT_TOKEN does not look like a bot token — Resolve clicks will fail. "
            "Fill in env/alert-ack.env from homelab-slack-gateway/.env."
        )
    host = os.environ.get("ALERT_ACK_HOST", "0.0.0.0")  # noqa: S104 — private Docker network only
    port = int(os.environ.get("ALERT_ACK_PORT", "8084"))

    work: queue.Queue[Job] = queue.Queue(maxsize=QUEUE_DEPTH)
    stop = threading.Event()

    threading.Thread(
        target=run_worker, args=(work, token, stop), daemon=True, name="alert-ack-worker"
    ).start()

    # The Alertmanager path is OPTIONAL, and stays off unless both halves are configured. A
    # half-configured deployment that posted alerts it could never resolve — or silently accepted
    # webhooks and dropped them — is worse than one that plainly answers 503 and lets Alertmanager
    # count the failure.
    channel = os.environ.get("ALERT_CHANNEL", "")
    webhook_token = os.environ.get("ALERTMANAGER_WEBHOOK_TOKEN", "")
    notify_work: queue.Queue[NotifyJob] | None = None
    store: Store | None = None

    if channel and webhook_token:
        db_path = os.environ.get("ALERT_ACK_DB", "/data/alert-ack.db")
        try:
            store = Store(db_path)
        except sqlite3.Error as exc:
            # Fail at BOOT, loudly. The one production outage this pipeline has had was a file the
            # `nobody` uid could not read, discovered days later. Do not repeat it quietly.
            log.error(
                "cannot open %s (%s) — the alertmanager path stays DISABLED. "
                "The volume must be writable by uid 65534.",
                db_path,
                exc,
            )
        else:
            notify_work = queue.Queue(maxsize=QUEUE_DEPTH)
            sweep_work: queue.Queue[SweepTask] = queue.Queue(maxsize=QUEUE_DEPTH)
            threading.Thread(
                target=run_sweep_worker,
                args=(sweep_work, token, channel, store, stop),
                daemon=True,
                name="alert-ack-sweep",
            ).start()
            threading.Thread(
                target=run_notify_worker,
                args=(notify_work, token, channel, store, stop, sweep_work),
                daemon=True,
                name="alert-ack-notify",
            ).start()
            log.info("alertmanager path enabled: posting to %s, state in %s", channel, db_path)
    else:
        log.warning(
            "ALERT_CHANNEL/ALERTMANAGER_WEBHOOK_TOKEN unset — %s will answer 503 and alerts will "
            "not be posted or auto-resolved by this service.",
            ALERTMANAGER_PATH,
        )

    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    server = build_server(work, secret, host, port, notify_work, webhook_token)
    log.info("alert-ack listening on %s:%d%s", host, port, DISPATCH_PATH)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
        if store is not None:
            store.close()
    return 0
