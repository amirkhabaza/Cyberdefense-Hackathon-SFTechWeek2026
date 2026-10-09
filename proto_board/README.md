# proto_board: Project Aegis dashboard (ClickHouse Cloud)

A live security dashboard: traffic, attack path, a voice-approved remediation "autopilot", and verification.
Every number on screen is a query against ClickHouse Cloud. The attack and the remediation are simulated.

```
seed.py --live --> ClickHouse Cloud <-- server.py (FastAPI) <-- index.html (polls /api/state every 2s)
 (synthetic traffic,   traffic_events                 |             voice: "approve" -> POST /api/remediation/approve
  honors blocks/patch) remediation_log, blocked_ips   +-- runs remediation steps, logging each to ClickHouse
```

## Setup
1. In ClickHouse Cloud, open your service -> **Connect** -> HTTPS, and note host, user, password.
2. Run:
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                  # fill in CLICKHOUSE_HOST and CLICKHOUSE_PASSWORD
.venv/bin/python seed.py --live       # terminal 1: schema, 30 min backfill, then live traffic
.venv/bin/python server.py            # terminal 2: http://127.0.0.1:8100
```
Open http://127.0.0.1:8100. An SQL-injection attack on `/api/search` is already in progress (DEFCON 2).
Click **Approve**, or click **Enable voice** (Chrome) and say "approve". Use **Reset demo** to replay.

## What the demo does
- **Live traffic / severity / confidence / DEFCON:** aggregates over the last 10 s of `traffic_events`.
  Severity counts only exploit requests that *succeeded* (status < 400), so it drops once defenses work.
- **Attack path:** top `sql_injection` path in the last minute; Internet -> API Gateway -> path -> attack -> asset.
- **Autopilot:** on approval (server re-checks the approval words) it (1) inserts attacker IPs into `blocked_ips`, which the
  generator honors with 403s, (2) logs a simulated parameterization patch, after which exploits get 400s, (3) replays the
  exploit from a known and a fresh IP. Each step is a row in `remediation_log`.
- **Verification:** Patch, Tests (simulated), Replay (real status codes from the simulation), Health (legit-traffic success
  rate from ClickHouse).

## Tables
`traffic_events` (aggregated requests per second/source/path, MergeTree, 1-day TTL), `remediation_log` (step events),
`blocked_ips` (ReplacingMergeTree).

## Notes
- The browser narrates with the built-in speech synthesis and listens with the Web Speech API; swap in ElevenLabs for production-quality voice.
- Needs `seed.py --live` running, or the dashboard shows a "no live traffic" banner.
