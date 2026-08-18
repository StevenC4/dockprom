"""The dispatch endpoint, over real HTTP on loopback.

Same reasoning as the gateway's own suite: signing is a wire contract, so the test puts real
bytes on a real socket and signs them the way the gateway does. A mock would only prove we can
call our own function.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request

import pytest

from alert_ack.backend import DISPATCH_PATH, Job, accept, build_server
from alert_ack.sign import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign

SECRET = "test-secret"


def click(**overrides):
    """A legacy `interactive_message` payload, as Slack delivers an attachment-button click."""
    payload = {
        "type": "interactive_message",
        "callback_id": "alert:nh22",
        "actions": [{"name": "alert:resolve", "type": "button", "value": "nh22"}],
        "channel": {"id": "C0BG4A0V9LP", "name": "alerts"},
        "user": {"id": "U0BGX8MV8EA", "name": "steven"},
        "message_ts": "1784818437.512549",
        "response_url": "https://hooks.slack.com/actions/T1/2/3",
        "original_message": {"text": "", "attachments": [{"color": "danger", "text": "boom"}]},
    }
    payload.update(overrides)
    return {"kind": "interactivity", "slack": payload, "meta": {"namespace": "alert"}}


@pytest.fixture
def work():
    return queue.Queue(maxsize=10)


@pytest.fixture
def server(work):
    server = build_server(work, SECRET, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def post(url: str, envelope: dict, *, secret: str = SECRET, timestamp: int | None = None) -> int:
    raw = json.dumps(envelope).encode()
    timestamp = int(time.time()) if timestamp is None else timestamp
    request = urllib.request.Request(
        url + DISPATCH_PATH,
        data=raw,
        headers={
            "Content-Type": "application/json",
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_HEADER: sign(secret, timestamp, raw),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_a_signed_click_is_queued(server, work):
    assert post(server, click()) == 200
    job = work.get_nowait()
    assert isinstance(job, Job)
    assert (job.channel, job.ts, job.user_id) == (
        "C0BG4A0V9LP",
        "1784818437.512549",
        "U0BGX8MV8EA",
    )
    assert job.original["attachments"][0]["text"] == "boom"


def test_an_unsigned_click_is_rejected(server, work):
    assert post(server, click(), secret="wrong-secret") == 401
    assert work.empty()


def test_a_stale_click_is_rejected(server, work):
    assert post(server, click(), timestamp=int(time.time()) - 3600) == 401
    assert work.empty()


def test_another_path_is_404(server):
    raw = b"{}"
    request = urllib.request.Request(server + "/nope", data=raw, method="POST")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)
    assert caught.value.code == 404


# Everything below goes through accept() directly: these are routing decisions, and the HTTP
# answer is 200 either way — the gateway must not be told to retry a click it correctly delivered.


def test_someone_elses_namespace_is_ignored(work):
    accept(work, click(callback_id="wp:apply:123"))
    assert work.empty()


def test_an_unknown_action_is_ignored(work):
    accept(work, click(actions=[{"name": "alert:silence", "type": "button"}]))
    assert work.empty()


def test_block_actions_are_ignored(work):
    # The alert post is attachment-shaped; a block_actions payload under this namespace would
    # mean someone changed the button without changing this backend.
    accept(work, click(type="block_actions"))
    assert work.empty()


def test_an_event_is_ignored(work):
    accept(work, {"kind": "event", "slack": {"type": "message"}, "meta": {}})
    assert work.empty()


def test_a_click_with_nothing_to_edit_is_ignored(work):
    accept(work, click(original_message=None))
    assert work.empty()
