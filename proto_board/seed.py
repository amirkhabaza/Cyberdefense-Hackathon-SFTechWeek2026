#!/usr/bin/env python3
"""Create the ClickHouse schema, backfill traffic, and optionally keep generating live traffic.

    python seed.py            # schema + 30 min backfill (attack ramps in during the last 5 min)
    python seed.py --live     # same, then keep inserting one second of traffic every second
    python seed.py --reset    # clear remediation state / blocked IPs so the demo can be replayed
"""
import argparse
import time
from datetime import datetime, timedelta, timezone

import db


def now_s():
    return datetime.now(timezone.utc).replace(microsecond=0)


def state(client):
    blocked = {r[0] for r in client.query("SELECT ip FROM blocked_ips").result_rows}
    patched = client.query(
        "SELECT argMax(status, ts) = 'done' FROM remediation_log WHERE step = 'apply_patch'"
    ).result_rows[0][0]
    return blocked, bool(patched)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--minutes", type=int, default=30)
    args = ap.parse_args()

    client = db.connect(create_db=True)
    for ddl in db.SCHEMA:
        client.command(ddl)

    if args.reset:
        client.command("TRUNCATE TABLE remediation_log")
        client.command("TRUNCATE TABLE blocked_ips")
        print("Reset remediation state and blocked IPs.")
        if not args.live:
            return

    if client.query("SELECT count() FROM traffic_events").result_rows[0][0] == 0:
        end, start = now_s(), now_s() - timedelta(minutes=args.minutes)
        attack_from = end - timedelta(minutes=5)
        rows, t = [], start
        while t < end:
            rows += db.traffic_rows(t, set(), False, attack=t >= attack_from)
            t += timedelta(seconds=1)
        for i in range(0, len(rows), 20000):
            client.insert("traffic_events", rows[i:i + 20000], column_names=db.COLUMNS)
        print(f"Backfilled {len(rows)} rows ({args.minutes} min).")
    else:
        print("traffic_events already has data; skipping backfill.")

    if args.live:
        print("Generating live traffic. Ctrl+C to stop.")
        last = now_s()
        while True:
            time.sleep(1)
            cur = now_s()
            blocked, patched = state(client)
            rows = []
            t = last + timedelta(seconds=1)
            while t <= cur:
                rows += db.traffic_rows(t, blocked, patched)
                t += timedelta(seconds=1)
            if rows:
                client.insert("traffic_events", rows, column_names=db.COLUMNS)
            last = cur


if __name__ == "__main__":
    main()
