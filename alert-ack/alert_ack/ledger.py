"""Read the deliveries ledger: ``docker exec alert-ack python -m alert_ack deliveries``.

    deliveries [--alertname SUBSTRING] [--since-hours N]

Prints one line per notification Alertmanager sent to a receiver that includes the alert-ack
webhook, with whether the Slack post succeeded. Email is NOT recorded here -- see the README.
"""

from __future__ import annotations

import argparse
import os
import time

from .store import Store


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="alert_ack deliveries")
    ap.add_argument("cmd", choices=["deliveries"])
    ap.add_argument("--alertname", default="")
    ap.add_argument("--since-hours", type=float, default=0)
    args = ap.parse_args(argv)
    since = int(time.time() - args.since_hours * 3600) if args.since_hours else 0
    store = Store(os.environ.get("ALERT_ACK_DB", "/data/alert-ack.db"))
    for at, status, receiver, service, names, ok, ts in store.deliveries(since, args.alertname):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
        print(f"{stamp} {status:<8} receiver={receiver} service={service} "
              f"alerts={names} slack={'ok ts=' + ts if ok and ts else 'ok' if ok else 'FAILED'}")
    return 0
