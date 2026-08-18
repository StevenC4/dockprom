"""Shared fixtures.

The backoff override is here rather than in one module because forgetting it costs 135 real
seconds a test and the failure is a slow suite, not a red one — the kind of thing that gets
noticed a month later.
"""

from __future__ import annotations

import pytest

from alert_ack import sweep


@pytest.fixture(autouse=True)
def no_throttle_backoff(monkeypatch):
    """Keep the retry LOGIC, drop the wait.

    sweep.HistoryCache retries an empty page because Slack answers a throttled
    conversations.history with `ok: true` and an empty list rather than a 429. Only the sleep is
    patched out — the attempt count stays real, so tests still exercise the retry path.
    """
    monkeypatch.setattr(sweep, "HISTORY_BACKOFF_S", 0)
