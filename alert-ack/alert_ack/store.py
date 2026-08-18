"""Where the ts of every alert post lives, so a resolve can find its posts again.

**This one is durable, and the click path deliberately is not.** ``backend.py`` explains why a
Resolve *click* is queued in memory: the work is a cosmetic edit, Slack never replays
interactivity, and a lost click costs one more press. None of that reasoning survives here. The
whole feature is "when an alert resolves, go back and green every post it made" — and a post's
``ts`` is the only handle Slack gives us on it. Lose the ts and the post is unreachable forever;
there is no click coming to mint a fresh ``response_url``, and no way to re-derive it. So this
is SQLite on a volume, and the deviation is in the other direction from the rest of the service.

Two tables, because they answer two different questions:

``posts``  - what WE authored via chat.postMessage, keyed by Alertmanager's ``groupKey``. These
             are editable, so a resolve rewrites them green in place.
``swept``  - which webhook-authored posts we have already reacted to and thread-replied on.
             Slack's ``reactions.add`` is naturally idempotent (it answers ``already_reacted``),
             but a thread reply is NOT: without this table a recurring alert would drop a fresh
             "Resolved" reply onto the same ancient post every time it cleared.

The uid matters. The Dockerfile runs this service as ``nobody`` (65534), and the alerting
pipeline's one production outage was a file that uid could not read. So ``open()`` below fails
LOUDLY at boot rather than at the first alert, hours later, in a log nobody is reading.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    group_key TEXT    NOT NULL,
    channel   TEXT    NOT NULL,
    ts        TEXT    NOT NULL,
    posted_at INTEGER NOT NULL,
    PRIMARY KEY (channel, ts)
);
CREATE INDEX IF NOT EXISTS posts_by_group ON posts (group_key);

CREATE TABLE IF NOT EXISTS swept (
    channel   TEXT    NOT NULL,
    ts        TEXT    NOT NULL,
    swept_at  INTEGER NOT NULL,
    PRIMARY KEY (channel, ts)
);
"""

# Posts older than this are dropped on prune. Long enough that an alert firing daily for a month
# still has every post reachable when it finally clears; short enough that the file stays small.
RETENTION_S = 90 * 24 * 3600


class Store:
    """Thread-safe by a plain lock — the write volume here is a handful of rows per alert."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        # check_same_thread=False because the HTTP thread records and the worker reads; the lock
        # is what actually serialises them.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def record(self, group_key: str, channel: str, ts: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO posts (group_key, channel, ts, posted_at) "
                "VALUES (?, ?, ?, ?)",
                (group_key, channel, ts, int(time.time())),
            )
            self._db.commit()

    def posts_for(self, group_key: str) -> list[tuple[str, str]]:
        """Every (channel, ts) we authored for this group, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT channel, ts FROM posts WHERE group_key = ? ORDER BY posted_at",
                (group_key,),
            ).fetchall()
        return [(str(c), str(t)) for c, t in rows]

    def forget(self, group_key: str) -> None:
        """Drop a group's posts once they have been marked resolved."""
        with self._lock:
            self._db.execute("DELETE FROM posts WHERE group_key = ?", (group_key,))
            self._db.commit()

    def already_swept(self, channel: str, ts: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM swept WHERE channel = ? AND ts = ?", (channel, ts)
            ).fetchone()
        return row is not None

    def mark_swept(self, channel: str, ts: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO swept (channel, ts, swept_at) VALUES (?, ?, ?)",
                (channel, ts, int(time.time())),
            )
            self._db.commit()

    def prune(self, retention_s: int = RETENTION_S) -> int:
        cutoff = int(time.time()) - retention_s
        with self._lock:
            n = self._db.execute("DELETE FROM posts WHERE posted_at < ?", (cutoff,)).rowcount
            self._db.execute("DELETE FROM swept WHERE swept_at < ?", (cutoff,))
            self._db.commit()
        return max(0, n)

    def close(self) -> None:
        with self._lock:
            self._db.close()
