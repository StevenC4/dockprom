"""Posting an alert, and greening every post it made when it clears.

The Slack calls are stubbed at the ``slack`` module boundary rather than over HTTP, because what
is under test here is the DECISION each path makes — which posts get edited, which only get
reacted to, and what happens the second time the same alert clears. The wire format of those calls
is already pinned by tests/test_slack.py.

The distinction the whole feature rests on is asserted directly: a post this service authored is
edited green, and a post the old incoming webhook authored is only reacted to and replied to,
because Slack will not let a bot edit it.
"""

from __future__ import annotations

import pytest

from alert_ack import notify, slack, sweep
from alert_ack.render import title_for
from alert_ack.store import Store

CHANNEL = "C0BG4A0V9LP"
TOKEN = "xoxb-test"
FIRING_TITLE = title_for("firing", "nh22-extractor", "NH22DataStaleCritical")


def payload(status="firing", group_key="{}:{alertname=\"NH22DataStaleCritical\"}"):
    return {
        "status": status,
        "groupKey": group_key,
        "commonLabels": {
            "service": "nh22-extractor",
            "alertname": "NH22DataStaleCritical",
            "severity": "critical",
        },
        "alerts": [
            {
                "labels": {"severity": "critical", "source": "cdbaby"},
                "annotations": {"summary": "cdbaby missing >3 days", "description": "broken"},
            }
        ],
    }


class FakeSlack:
    """Records every call and hands back plausible answers."""

    def __init__(self, legacy=()):
        self.posted, self.updated, self.reactions, self.replies = [], [], [], []
        self.legacy = list(legacy)
        self._ts = 1000.0

    def post_message(self, *, token, channel, message):
        self._ts += 1
        ts = f"{self._ts:.6f}"
        self.posted.append((channel, ts, message))
        return ts

    def update_message(self, *, token, channel, ts, message, response_url):
        self.updated.append((channel, ts, message))
        return True

    def add_reaction(self, *, token, channel, ts, name):
        self.reactions.append((channel, ts, name))
        return True

    def reply_in_thread(self, *, token, channel, thread_ts, text):
        self.replies.append((channel, thread_ts, text))
        return True

    def history(self, *, token, channel, oldest, limit=200):
        return self.legacy


@pytest.fixture
def fake(monkeypatch):
    stub = FakeSlack()
    for module in (notify, sweep):
        for name in ("post_message", "update_message", "add_reaction", "reply_in_thread", "history"):
            if hasattr(module, "slack"):
                monkeypatch.setattr(module.slack, name, getattr(stub, name), raising=False)
    return stub


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


def test_firing_posts_and_remembers_the_ts(fake, store):
    ts = notify.handle_firing(payload(), token=TOKEN, channel=CHANNEL, store=store)
    assert ts is not None
    assert len(fake.posted) == 1
    # Remembering the ts is the entire point — without it the resolve has no handle on the post.
    assert store.posts_for(payload()["groupKey"]) == [(CHANNEL, ts)]


def test_resolve_greens_every_post_the_alert_made(fake, store):
    key = payload()["groupKey"]
    for _ in range(3):  # three days of repeats, three red posts
        notify.handle_firing(payload(), token=TOKEN, channel=CHANNEL, store=store)

    greened, _ = notify.handle_resolved(payload("resolved"), token=TOKEN, channel=CHANNEL, store=store)

    assert greened == 3
    assert len(fake.updated) == 3
    colours = {a["color"] for _, _, m in fake.updated for a in m["attachments"]}
    assert colours == {"#2eb886"}
    # The button is gone; a green post with a live Resolve button reads as broken.
    assert all("actions" not in a for _, _, m in fake.updated for a in m["attachments"])
    # And the group is forgotten, so a later resolve does not re-edit them.
    assert store.posts_for(key) == []


def test_resolve_says_alertmanager_cleared_it_not_a_human(fake, store):
    notify.handle_firing(payload(), token=TOKEN, channel=CHANNEL, store=store)
    notify.handle_resolved(payload("resolved"), token=TOKEN, channel=CHANNEL, store=store)
    footers = {a.get("footer") for _, _, m in fake.updated for a in m["attachments"]}
    assert footers == {notify.AUTO_FOOTER}


def test_legacy_webhook_posts_are_reacted_to_not_edited(fake, store):
    # Two old posts the incoming webhook wrote, which Slack will never let us edit.
    fake.legacy = [
        {"ts": "900.1", "attachments": [{"title": FIRING_TITLE}]},
        {"ts": "900.2", "attachments": [{"title": FIRING_TITLE}]},
        {"ts": "900.3", "attachments": [{"title": "unrelated alert"}]},
    ]
    greened, swept = notify.handle_resolved(
        payload("resolved"), token=TOKEN, channel=CHANNEL, store=store
    )

    assert (greened, swept) == (0, 2)
    assert {ts for _, ts, _ in fake.reactions} == {"900.1", "900.2"}
    assert {ts for _, ts, _ in fake.replies} == {"900.1", "900.2"}
    assert fake.updated == []          # never attempted — it cannot work
    assert "900.3" not in {ts for _, ts, _ in fake.reactions}


def test_sweeping_twice_does_not_double_reply(fake, store):
    fake.legacy = [{"ts": "900.1", "attachments": [{"title": FIRING_TITLE}]}]
    notify.handle_resolved(payload("resolved"), token=TOKEN, channel=CHANNEL, store=store)
    notify.handle_resolved(payload("resolved"), token=TOKEN, channel=CHANNEL, store=store)
    # A daily alert clearing daily must not accumulate a reply per day on the same old post.
    assert len(fake.replies) == 1


def test_our_own_post_is_not_also_swept(fake, store):
    ts = notify.handle_firing(payload(), token=TOKEN, channel=CHANNEL, store=store)
    fake.legacy = [{"ts": ts, "attachments": [{"title": FIRING_TITLE}]}]
    greened, swept = notify.handle_resolved(
        payload("resolved"), token=TOKEN, channel=CHANNEL, store=store
    )
    # It was edited green; reacting to it as well would be belt-and-braces noise.
    assert (greened, swept) == (1, 0)
    assert fake.reactions == []


def test_a_failed_post_is_not_remembered(fake, store, monkeypatch):
    monkeypatch.setattr(notify.slack, "post_message", lambda **_: None)
    assert notify.handle_firing(payload(), token=TOKEN, channel=CHANNEL, store=store) is None
    assert store.posts_for(payload()["groupKey"]) == []
