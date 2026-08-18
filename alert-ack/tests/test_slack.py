"""Which route rewrites the post, and in which order.

This is pinned rather than left to reading order because it was got WRONG first: `chat.update`
was primary on the theory that it never expires, and it turned out it can never work at all —
Slack answers `cant_update_message` for a post authored by an incoming webhook, which is every
post this button appears on. The response_url is the one that works, and its 30-minute clock
starts at the CLICK, not at the post, so age is irrelevant.

Both routes are served by a real loopback HTTP server, so the assertions are about bytes that
actually went somewhere rather than about a mock being called.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from alert_ack import slack

MESSAGE = {"text": "", "attachments": [{"color": "#2eb886"}]}


class _Recorder(BaseHTTPRequestHandler):
    hits: list[tuple[str, dict]]
    fail_paths: set[str]
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).hits.append((self.path, body))

        if self.path in type(self).fail_paths:
            payload = b'{"ok": false, "error": "cant_update_message"}'
        else:
            payload = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        pass


@pytest.fixture
def slack_server(monkeypatch):
    handler = type("Bound", (_Recorder,), {"hits": [], "fail_paths": set()})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(slack, "SLACK_API", base + "/api/chat.update")
    yield handler, base + "/actions/response-url"
    server.shutdown()


def update(response_url):
    return slack.update_message(
        token="xoxb-test", channel="C1", ts="1.2", message=MESSAGE, response_url=response_url
    )


def test_response_url_is_tried_first_and_alone(slack_server):
    handler, response_url = slack_server
    assert update(response_url) is True

    paths = [path for path, _ in handler.hits]
    assert paths == ["/actions/response-url"], "chat.update must not be called once this lands"
    assert handler.hits[0][1]["replace_original"] is True
    assert handler.hits[0][1]["attachments"] == MESSAGE["attachments"]


def test_chat_update_covers_a_missing_response_url(slack_server):
    handler, _ = slack_server
    assert update(None) is True

    paths = [path for path, _ in handler.hits]
    assert paths == ["/api/chat.update"]
    assert handler.hits[0][1]["channel"] == "C1"
    assert handler.hits[0][1]["ts"] == "1.2"


def test_a_spent_response_url_falls_back_to_chat_update(slack_server):
    handler, response_url = slack_server
    handler.fail_paths = {"/actions/response-url"}
    assert update(response_url) is True

    assert [path for path, _ in handler.hits] == ["/actions/response-url", "/api/chat.update"]


def test_both_failing_is_reported_as_failure(slack_server):
    handler, response_url = slack_server
    handler.fail_paths = {"/actions/response-url", "/api/chat.update"}
    assert update(response_url) is False


def test_a_transport_error_falls_back_too(slack_server):
    # Not just an `"ok": false` body — a refused connection must fall through as well, or a
    # blip on the response_url host would drop the click on the floor.
    handler, _ = slack_server
    assert update("http://127.0.0.1:1/never") is True
    assert [path for path, _ in handler.hits] == ["/api/chat.update"]


def test_an_unreachable_slack_is_a_failure_not_a_crash(monkeypatch):
    # Port 1 on loopback refuses instantly. Both routes gone: the worker must get a False and
    # log it, never an exception that takes the thread down — the next Resolve press in an
    # hour still has to work.
    monkeypatch.setattr(slack, "SLACK_API", "http://127.0.0.1:1/api")
    assert update("http://127.0.0.1:1/never") is False
