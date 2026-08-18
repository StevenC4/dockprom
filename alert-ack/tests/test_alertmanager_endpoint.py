"""The Alertmanager webhook endpoint, over real HTTP.

Two things are worth pinning here and neither is about Slack.

**The two paths do not share a secret.** ``/slack/dispatch`` is HMAC-signed by the gateway;
``/alertmanager`` is bearer-authenticated by Alertmanager. Presenting one path's credential to the
other must fail, or adding this endpoint would have widened the gateway's trust boundary.

**Which failures Alertmanager should retry.** It retries any non-2xx, so a body we can never parse
has to be answered 200 (retrying it forever is pointless) while a full queue has to be answered
non-2xx (that one genuinely should come back). Getting this backwards produces either an infinite
retry loop or silently dropped alerts.
"""

from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request

import pytest

from alert_ack.backend import ALERTMANAGER_PATH, DISPATCH_PATH, build_server

SECRET = "gateway-secret"
WEBHOOK_TOKEN = "alertmanager-token"

GROUP = {
    "status": "firing",
    "groupKey": '{}:{alertname="X"}',
    "commonLabels": {"service": "nh22-extractor", "alertname": "X", "severity": "warning"},
    "alerts": [{"labels": {"severity": "warning"}, "annotations": {"summary": "s"}}],
}


@pytest.fixture
def notify_work():
    return queue.Queue(maxsize=2)


def _serve(notify_work, token=WEBHOOK_TOKEN):
    server = build_server(
        queue.Queue(maxsize=10), SECRET, "127.0.0.1", 0, notify_work, token
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def url(notify_work):
    server = _serve(notify_work)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def post(url, body, *, token=WEBHOOK_TOKEN, path=ALERTMANAGER_PATH, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_a_signed_group_is_accepted_and_queued(url, notify_work):
    assert post(url, GROUP) == 200
    assert notify_work.get_nowait().payload["groupKey"] == GROUP["groupKey"]


def test_resolved_is_accepted_too(url, notify_work):
    assert post(url, {**GROUP, "status": "resolved"}) == 200
    assert notify_work.get_nowait().payload["status"] == "resolved"


def test_no_token_is_rejected(url, notify_work):
    assert post(url, GROUP, token=None) == 401
    assert notify_work.empty()


def test_wrong_token_is_rejected(url, notify_work):
    assert post(url, GROUP, token="not-it") == 401
    assert notify_work.empty()


def test_the_gateway_secret_is_not_accepted_here(url, notify_work):
    # The two endpoints must not become interchangeable just because both hold a secret.
    assert post(url, GROUP, token=SECRET) == 401
    assert notify_work.empty()


def test_the_webhook_token_is_not_accepted_on_the_dispatch_path(url, notify_work):
    # And the reverse: bearer auth buys nothing on the HMAC-signed path.
    assert post(url, GROUP, path=DISPATCH_PATH) == 401


def test_unparseable_body_is_not_retried(url, notify_work):
    # 200 on purpose: this body will not parse on the tenth attempt either.
    assert post(url, None, raw=b"{not json") == 200
    assert notify_work.empty()


def test_unknown_status_is_dropped_not_queued(url, notify_work):
    assert post(url, {**GROUP, "status": "weird"}) == 200
    assert notify_work.empty()


def test_a_full_queue_asks_alertmanager_to_come_back(url, notify_work):
    for _ in range(2):
        assert post(url, GROUP) == 200
    # 503, not 200 — this one really is worth retrying.
    assert post(url, GROUP) == 503


def test_the_path_is_503_when_unconfigured():
    # No ALERT_CHANNEL / token: answer 503 so the failure lands in Alertmanager's notify metrics
    # rather than looking like a delivered alert.
    server = build_server(queue.Queue(maxsize=10), SECRET, "127.0.0.1", 0, None, "")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert post(f"http://127.0.0.1:{server.server_address[1]}", GROUP) == 503
    finally:
        server.shutdown()
