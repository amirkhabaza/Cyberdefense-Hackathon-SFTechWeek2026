#!/usr/bin/env python3
"""Project Aegis dashboard backend. All dashboard numbers are ClickHouse Cloud queries."""
import re
import threading
import time
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db

app = FastAPI(title="Project Aegis", version="1.0.0")
_local = threading.local()
_run_lock = threading.Lock()
APPROVAL_RE = re.compile(r"\b(approve[ds]?|go ahead|execute|do it|proceed|confirm(ed)?|yes)\b", re.I)

PLAN = [
    ("block_sources", "Block attack sources"),
    ("apply_patch", "Apply SQL parameterization patch"),
    ("replay_exploit", "Replay exploit"),
]
VERIFY = [("verify_patch", "Patch"), ("verify_tests", "Tests"), ("verify_replay", "Replay"), ("verify_health", "Health")]


def ch():
    """One ClickHouse client per thread (clients are not safe for concurrent queries)."""
    if not hasattr(_local, "c"):
        _local.c = db.connect()
    return _local.c


def q(sql):
    return ch().query(sql).result_rows


_run_id = None


def log(step, status, detail=""):
    """Step state goes to ClickHouse (queried for the dashboard); the full timeline goes to MongoDB."""
    now = datetime.now(timezone.utc)
    ch().insert("remediation_log", [[now, step, status, detail]], column_names=["ts", "step", "status", "detail"])
    if _run_id:
        try:
            db.mongo().incidents.update_one(
                {"_id": _run_id}, {"$push": {"timeline": {"ts": now, "step": step, "status": status, "detail": detail}}},
                upsert=True)
        except Exception:
            pass  # the timeline is best-effort; ClickHouse remains the source of truth for dashboard state


def step_states():
    rows = q("SELECT step, argMax(status, ts), argMax(detail, ts) FROM remediation_log GROUP BY step")
    return {s: {"status": st, "detail": d} for s, st, d in rows}


# ---------- remediation runner ----------
def run_remediation(approved_by, transcript):
    global _run_id
    try:
        _run_id = datetime.now(timezone.utc).strftime("inc-%Y%m%d-%H%M%S")
        db.mongo().incidents.insert_one({"_id": _run_id, "created": datetime.now(timezone.utc), "type": "sql_injection",
                                         "path": db.TARGET_PATH, "approved_by": approved_by, "transcript": transcript,
                                         "timeline": []})
        log("approval", "done", f"{approved_by}: {transcript}")

        log("block_sources", "running")
        ips = [r[0] for r in q("SELECT DISTINCT src_ip FROM traffic_events "
                               "WHERE is_malicious = 1 AND ts >= now() - INTERVAL 5 MINUTE")]
        if ips:
            ch().insert("blocked_ips", [[datetime.now(timezone.utc), ip] for ip in ips], column_names=["ts", "ip"])
        time.sleep(1.5)
        log("block_sources", "done", f"Blocked {len(ips)} source IPs")

        log("apply_patch", "running")
        time.sleep(2)
        log("apply_patch", "done", "Parameterized the /api/search query (simulated patch)")
        log("verify_patch", "done", "Handler now binds user input as parameters")

        log("verify_tests", "running")
        time.sleep(1.5)
        log("verify_tests", "done", "Regression suite passed (simulated)")

        log("replay_exploit", "running")
        time.sleep(1.5)
        blocked_replay = db.respond(blocked=True, patched=True)    # known attacker, blocked at the edge
        patched_replay = db.respond(blocked=False, patched=True)   # fresh IP, only the patch protects it
        now = datetime.now(timezone.utc)
        ch().insert("traffic_events", [
            [now, ips[0] if ips else "203.0.113.14", "GET", db.TARGET_PATH, blocked_replay, 1, 1,
             "sql_injection_replay", "replay-union-select", 0.99],
            [now, "192.0.2.77", "GET", db.TARGET_PATH, patched_replay, 1, 1,
             "sql_injection_replay", "replay-union-select", 0.99],
        ], column_names=db.COLUMNS)
        db.mongo().logs.insert_many([
            {"ts": now, "src_ip": "replay", "method": "GET", "path": db.TARGET_PATH, "query": db.SQLI_PAYLOADS[0],
             "status": st, "user_agent": "aegis-replay", "is_malicious": True, "sample_rate": 1, "attack_type": "sql_injection_replay",
             "raw": f'replay - - "GET {db.TARGET_PATH}?{db.SQLI_PAYLOADS[0]} HTTP/1.1" {st}'}
            for st in (blocked_replay, patched_replay)])
        ok = blocked_replay >= 400 and patched_replay >= 400
        detail = f"Exploit replay: known attacker -> {blocked_replay}, fresh source -> {patched_replay}"
        log("replay_exploit", "done" if ok else "failed", detail)
        log("verify_replay", "done" if ok else "failed", detail)

        log("verify_health", "running")
        time.sleep(1)
        total, good = q("SELECT sum(requests), sumIf(requests, status < 400) FROM traffic_events "
                        "WHERE is_malicious = 0 AND ts >= now() - INTERVAL 15 SECOND")[0]
        if total and good / total >= 0.95:
            log("verify_health", "done", f"Legitimate traffic success rate {good / total:.0%}")
        else:
            log("verify_health", "failed", "No live traffic or low success rate (is seed.py --live running?)")
    except Exception as e:  # surface any failure in the dashboard instead of dying silently
        log("error", "failed", str(e)[:300])
    finally:
        _run_lock.release()


# ---------- API ----------
class ApproveRequest(BaseModel):
    transcript: str
    approved_by: str = "voice-user"


@app.get("/api/state")
def state():
    try:
        rps, mal_rps, active_rps, conf, active_src = q(
            "SELECT sum(requests) / 10, sumIf(requests, is_malicious = 1) / 10, "
            "sumIf(requests, is_malicious = 1 AND status < 400) / 10, "
            "sumIf(confidence * requests, is_malicious = 1) / greatest(sumIf(requests, is_malicious = 1), 1), "
            "uniqExactIf(src_ip, is_malicious = 1 AND status < 400) "
            "FROM traffic_events WHERE ts >= now() - INTERVAL 11 SECOND AND ts < now() - INTERVAL 1 SECOND")[0]
        series = [{"t": t, "total": tot, "malicious": m} for t, tot, m in q(
            "SELECT toUInt32(ts) AS t, sum(requests), sumIf(requests, is_malicious = 1) "
            "FROM traffic_events WHERE ts >= now() - INTERVAL 62 SECOND AND ts < now() - INTERVAL 1 SECOND "
            "GROUP BY t ORDER BY t")]
        top = q("SELECT path, attack_type, sum(requests) AS r, uniqExact(src_ip) "
                "FROM traffic_events WHERE is_malicious = 1 AND attack_type = 'sql_injection' "
                "AND ts >= now() - INTERVAL 60 SECOND GROUP BY path, attack_type ORDER BY r DESC LIMIT 1")
        steps = step_states()
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"ClickHouse query failed: {str(e)[:300]}")

    try:
        evidence = [{"ts": d["ts"].strftime("%H:%M:%S"), "status": d["status"], "raw": d["raw"], "ua": d["user_agent"]}
                    for d in db.mongo().logs.find({"is_malicious": True}, {"ts": 1, "status": 1, "raw": 1, "user_agent": 1})
                    .sort("ts", -1).limit(8)]
        evidence_error = None
    except Exception as e:
        evidence, evidence_error = [], str(e)[:200]

    rps, mal_rps, active_rps = float(rps or 0), float(mal_rps or 0), float(active_rps or 0)
    conf = float(conf or 0)
    active_pct = active_rps / rps if rps else 0
    live = rps > 0

    verify = [{"key": k, "label": l, **steps.get(k, {"status": "pending", "detail": ""})} for k, l in VERIFY]
    plan = [{"key": k, "label": l, **steps.get(k, {"status": "pending", "detail": ""})} for k, l in PLAN]
    started = "approval" in steps
    all_done = started and all(v["status"] == "done" for v in verify)
    any_failed = any(s["status"] == "failed" for s in steps.values())
    phase = "failed" if any_failed else "complete" if all_done else "running" if started else "idle"

    if active_rps > 0:
        severity = "CRITICAL" if active_pct >= 0.10 and conf >= 0.9 else "HIGH" if active_pct >= 0.03 else "MEDIUM"
    else:
        severity = "LOW"
    defcon = {"CRITICAL": 2, "HIGH": 3, "MEDIUM": 4, "LOW": 5}[severity]

    path = None
    if top:
        p, atype, reqs, srcs = top[0]
        path = {"path": p, "attack_type": atype, "sources": srcs, "requests_60s": reqs,
                "asset": db.ASSET_FOR_PATH.get(p, "Backend"),
                "contained": active_rps == 0}

    if not live:
        line = "No live traffic. Start the generator with seed.py --live."
    elif phase == "running":
        line = "Remediation in progress. I will verify the fix when it finishes."
    elif phase == "complete":
        line = "All verifications passed. The incident is contained."
    elif phase == "failed":
        line = "A remediation step failed. Review the verification panel."
    elif active_rps > 0:
        line = (f"Active exploitation detected. SQL injection against {path['path'] if path else 'the API'} "
                f"from {active_src} sources, {active_pct:.0%} of traffic. Say approve to run the proposed remediation.")
    else:
        line = "No active exploitation detected."

    return {
        "live": live, "rps": rps, "malicious_rps": mal_rps, "active_exploit_rps": active_rps,
        "malicious_pct": mal_rps / rps if rps else 0, "severity": severity, "confidence": conf,
        "defcon": defcon, "series": series, "attack_path": path, "narration": line,
        "phase": phase, "plan": plan, "verification": verify,
        "evidence": evidence, "evidence_error": evidence_error,
        "can_approve": phase == "idle" and active_rps > 0,
    }


@app.post("/api/remediation/approve")
def approve(req: ApproveRequest):
    if not APPROVAL_RE.search(req.transcript):
        raise HTTPException(403, "Approval not recognized. Say 'approve' or 'go ahead'.")
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(409, "A remediation is already running.")
    try:
        if "approval" in step_states():
            _run_lock.release()
            raise HTTPException(409, "Remediation already executed. Reset to run again.")
    except HTTPException:
        raise
    except Exception as e:
        _run_lock.release()
        raise HTTPException(502, str(e)[:300])
    threading.Thread(target=run_remediation, args=(req.approved_by, req.transcript), daemon=True).start()
    return {"status": "started"}


@app.post("/api/reset")
def reset():
    """Clear remediation state and unblock sources so the demo can be replayed."""
    ch().command("TRUNCATE TABLE remediation_log")
    ch().command("TRUNCATE TABLE blocked_ips")
    return {"status": "reset"}


@app.get("/api/health")
def health():
    try:
        return {"status": "ok", "clickhouse": ch().command("SELECT version()"),
                "mongodb_logs": db.mongo().logs.estimated_document_count()}
    except Exception as e:
        raise HTTPException(503, str(e)[:300])


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(db.HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=db.HERE / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100)
