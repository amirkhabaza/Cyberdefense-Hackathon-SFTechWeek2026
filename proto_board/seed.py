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
    mdb = db.mongo()
    db.ensure_mongo_indexes(mdb)

    if args.reset:
        client.command("TRUNCATE TABLE remediation_log")
        client.command("TRUNCATE TABLE blocked_ips")
        db.mongo().incidents.delete_many({})
        print("Reset remediation state, blocked IPs, and incident timelines.")
        if not args.live:
            return

    need_ch = client.query("SELECT count() FROM traffic_events").result_rows[0][0] == 0
    need_mongo = mdb.logs.estimated_document_count() == 0
    if need_ch or need_mongo:
        end, start = now_s(), now_s() - timedelta(minutes=args.minutes)
        attack_from = end - timedelta(minutes=5)
        rows, docs, t = [], [], start
        while t < end:
            attack = t >= attack_from
            rows += db.traffic_rows(t, set(), False, attack=attack)
            docs += db.log_docs(t, set(), False, attack=attack)
            t += timedelta(seconds=1)
        if need_ch:
            for i in range(0, len(rows), 20000):
                client.insert("traffic_events", rows[i:i + 20000], column_names=db.COLUMNS)
            print(f"ClickHouse: backfilled {len(rows)} aggregate rows ({args.minutes} min).")
        if need_mongo:
            for i in range(0, len(docs), 5000):
                mdb.logs.insert_many(docs[i:i + 5000], ordered=False)
            print(f"MongoDB: backfilled {len(docs)} raw log documents.")
    else:
        print("Both stores already have data; skipping backfill.")

    if args.live:
        print("Generating live traffic. Ctrl+C to stop.")
        last = now_s()
        while True:
            time.sleep(1)
            cur = now_s()
            blocked, patched = state(client)
            rows, docs = [], []
            t = last + timedelta(seconds=1)
            while t <= cur:
                rows += db.traffic_rows(t, blocked, patched)
                docs += db.log_docs(t, blocked, patched)
                t += timedelta(seconds=1)
            if rows:
                client.insert("traffic_events", rows, column_names=db.COLUMNS)
                mdb.logs.insert_many(docs, ordered=False)
            last = cur


if __name__ == "__main__":
    main()
