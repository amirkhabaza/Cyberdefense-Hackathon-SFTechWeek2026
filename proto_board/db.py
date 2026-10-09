"""ClickHouse Cloud connection, schema, and the simulated attack/defense rules shared by seed.py and server.py."""
import os
import random
from pathlib import Path

import clickhouse_connect
from pymongo import ASCENDING, DESCENDING, MongoClient

HERE = Path(__file__).parent


def load_env():
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def connect(create_db=False):
    load_env()
    host, pw = os.environ.get("CLICKHOUSE_HOST"), os.environ.get("CLICKHOUSE_PASSWORD")
    if not host or not pw:
        raise RuntimeError("Set CLICKHOUSE_HOST and CLICKHOUSE_PASSWORD in .env (see .env.example).")
    kw = dict(
        host=host, port=int(os.environ.get("CLICKHOUSE_PORT", "8443")),
        username=os.environ.get("CLICKHOUSE_USER", "default"), password=pw, secure=True,
    )
    database = os.environ.get("CLICKHOUSE_DATABASE", "aegis")
    if create_db:
        clickhouse_connect.get_client(**kw).command(f"CREATE DATABASE IF NOT EXISTS {database}")
    return clickhouse_connect.get_client(database=database, **kw)


_mongo = None


def mongo():
    """MongoDB database holding raw log documents and incident timelines (client is thread-safe)."""
    global _mongo
    load_env()
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError("Set MONGODB_URI in .env (see .env.example).")
    if _mongo is None:
        _mongo = MongoClient(uri, serverSelectionTimeoutMS=8000)
    return _mongo[os.environ.get("MONGODB_DB", "aegis")]


def ensure_mongo_indexes(mdb):
    mdb.logs.create_index([("ts", DESCENDING)])
    mdb.logs.create_index([("is_malicious", ASCENDING), ("ts", DESCENDING)])
    mdb.logs.create_index("ts", expireAfterSeconds=86400, name="ts_ttl")  # keep one day of raw logs


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS traffic_events (
        ts DateTime('UTC'), src_ip String, method LowCardinality(String), path String,
        status UInt16, requests UInt32, is_malicious UInt8,
        attack_type LowCardinality(String), rule String, confidence Float32
    ) ENGINE = MergeTree ORDER BY ts TTL ts + INTERVAL 1 DAY""",
    """CREATE TABLE IF NOT EXISTS remediation_log (
        ts DateTime64(3, 'UTC'), step LowCardinality(String), status LowCardinality(String), detail String
    ) ENGINE = MergeTree ORDER BY ts""",
    """CREATE TABLE IF NOT EXISTS blocked_ips (
        ts DateTime('UTC'), ip String
    ) ENGINE = ReplacingMergeTree ORDER BY ip""",
]

COLUMNS = ["ts", "src_ip", "method", "path", "status", "requests", "is_malicious", "attack_type", "rule", "confidence"]

# ---- simulated world ----
ATTACKERS = [f"203.0.113.{n}" for n in (14, 27, 58, 91, 102, 133, 176, 201)]
BENIGN_PATHS = [
    ("GET", "/api/products", 5200), ("GET", "/api/users/me", 2600), ("POST", "/api/login", 1100),
    ("GET", "/api/cart", 2400), ("GET", "/health", 600), ("GET", "/static/app.js", 1500),
]
TARGET_PATH = "/api/search"
ASSET_FOR_PATH = {"/api/search": "Customer DB"}


def respond(blocked: bool, patched: bool) -> int:
    """HTTP status an exploit request receives given current defenses."""
    return 403 if blocked else 400 if patched else 200


def traffic_rows(ts, blocked_ips, patched, attack=True):
    """One second of aggregated traffic as rows for traffic_events."""
    rows = []
    for method, path, base in BENIGN_PATHS:
        rows.append([ts, f"198.51.100.{random.randint(1, 250)}", method, path, 200,
                     int(base * random.uniform(0.9, 1.1)), 0, "", "", 0.0])
    if attack:
        for ip in ATTACKERS:
            rows.append([ts, ip, "GET", TARGET_PATH, respond(ip in blocked_ips, patched),
                         int(random.uniform(450, 650)), 1, "sql_injection", "sqli-union-select",
                         round(random.uniform(0.95, 0.99), 3)])
    return rows


SQLI_PAYLOADS = [
    "q=' UNION SELECT username,password FROM customers--",
    "q=1' OR '1'='1",
    "q='; SELECT * FROM customers WHERE 'a'='a",
    "q=' UNION SELECT card_number,cvv FROM payments--",
]
USER_AGENTS = ["Mozilla/5.0 (X11; Linux x86_64)", "sqlmap/1.7.2#stable (https://sqlmap.org)", "python-requests/2.31.0"]
BENIGN_QUERIES = {"/api/products": "page=2", "/api/users/me": "", "/api/login": "", "/api/cart": "", "/health": "",
                  "/static/app.js": "v=41"}


def log_docs(ts, blocked_ips, patched, attack=True):
    """Sampled raw log documents for one second (what an access-log collector would store in MongoDB)."""
    docs = []

    def doc(ip, method, path, query, status, ua, malicious, rate, **extra):
        target = f"{path}?{query}" if query else path
        return {
            "ts": ts, "src_ip": ip, "method": method, "path": path, "query": query, "status": status,
            "user_agent": ua, "is_malicious": bool(malicious), "sample_rate": rate,
            "raw": f'{ip} - - [{ts:%d/%b/%Y:%H:%M:%S +0000}] "{method} {target} HTTP/1.1" {status} 512 "{ua}"',
            **extra,
        }

    for method, path, _ in random.sample(BENIGN_PATHS, 3):
        docs.append(doc(f"198.51.100.{random.randint(1, 250)}", method, path, BENIGN_QUERIES[path], 200,
                        USER_AGENTS[0], False, 500))
    if attack:
        for ip in ATTACKERS:
            docs.append(doc(ip, "GET", TARGET_PATH, random.choice(SQLI_PAYLOADS), respond(ip in blocked_ips, patched),
                            random.choice(USER_AGENTS[1:]), True, 500, attack_type="sql_injection",
                            rule="sqli-union-select", confidence=round(random.uniform(0.95, 0.99), 3)))
    return docs
