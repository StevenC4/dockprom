"""The throttle on conversations.history, and why an empty page is not an answer.

Slack rate-limits ``conversations.history`` for non-Marketplace apps to roughly one call a minute,
and it does not answer 429 — it answers ``ok: true`` with an EMPTY ``messages`` list. Measured
against the real workspace on 2026-08-18: the same query returned 166 messages, then 0, 0, 0, 0 at
15s intervals, then 166 again at t+90s.

Taken at face value that reads as "the backlog is clean", which is the exact false-negative this
whole feature exists to remove — the sweep would mark nothing and report success. So an empty page
is retried, a good page is cached and shared, and giving up is loud.
"""

from __future__ import annotations

import pytest

from alert_ack import slack, sweep
from alert_ack.store import Store

CHANNEL = "C0BG4A0V9LP"
TOKEN = "xoxb-test"
TITLE = ":rotating_light: NightHawk22 · nh22-extractor — X"
PAGE = [{"ts": "900.1", "attachments": [{"title": TITLE}]}]


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    yield s
    s.close()


@pytest.fixture
def calls(monkeypatch):
    """Replaces slack.history with a scripted sequence, counting how often it is called."""
    state = {"n": 0, "script": []}

    def fake_history(*, token, channel, oldest, limit=200):
        state["n"] += 1
        i = min(state["n"] - 1, len(state["script"]) - 1)
        return state["script"][i]

    monkeypatch.setattr(slack, "history", fake_history)
    return state


def test_an_empty_page_is_retried_not_believed(calls):
    calls["script"] = [[], [], PAGE]
    cache = sweep.HistoryCache()
    messages, trustworthy = cache.fetch(
        token=TOKEN, channel=CHANNEL, lookback_s=60, backoff_s=0
    )
    assert (messages, trustworthy) == (PAGE, True)
    assert calls["n"] == 3  # two throttled, one good


def test_giving_up_reports_untrustworthy_not_empty(calls):
    calls["script"] = [[]]
    cache = sweep.HistoryCache()
    messages, trustworthy = cache.fetch(
        token=TOKEN, channel=CHANNEL, lookback_s=60, attempts=3, backoff_s=0
    )
    # The distinction that matters: not "no legacy posts", but "I was never allowed to look".
    assert messages == []
    assert trustworthy is False
    assert calls["n"] == 3


def test_a_good_page_is_cached_so_a_burst_costs_one_call(calls):
    calls["script"] = [PAGE]
    cache = sweep.HistoryCache()
    for _ in range(6):  # six alerts clearing together, e.g. one scheduler outage
        messages, trustworthy = cache.fetch(token=TOKEN, channel=CHANNEL, lookback_s=60)
        assert (messages, trustworthy) == (PAGE, True)
    # Six fetches, one API call — otherwise five of them would hit the throttle and see nothing.
    assert calls["n"] == 1


def test_an_expired_cache_refetches(calls):
    calls["script"] = [PAGE, PAGE]
    cache = sweep.HistoryCache(ttl_s=0)
    cache.fetch(token=TOKEN, channel=CHANNEL, lookback_s=60)
    cache.fetch(token=TOKEN, channel=CHANNEL, lookback_s=60)
    assert calls["n"] == 2


def test_a_throttled_sweep_marks_nothing_rather_than_guessing(calls, store, monkeypatch):
    calls["script"] = [[]]
    reacted = []
    monkeypatch.setattr(slack, "add_reaction", lambda **k: reacted.append(k) or True)
    monkeypatch.setattr(slack, "reply_in_thread", lambda **k: True)

    marked = sweep.sweep(
        token=TOKEN,
        channel=CHANNEL,
        store=store,
        firing_title=TITLE,
        skip_ts=set(),
        resolved_at=0,
        cache=sweep.HistoryCache(),
    )
    assert marked == 0
    assert reacted == []          # nothing invented from a page we never got
