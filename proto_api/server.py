#!/usr/bin/env python3
"""Local remediation API. Remediations are SIMULATED: nothing here touches real infrastructure."""
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

HERE = Path(__file__).parent
AUDIT_LOG = HERE / "audit.jsonl"


def load_env():
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
API_KEY = os.environ.get("REMEDIATE_API_KEY", "dev-local-key")
APPROVAL_RE = re.compile(r"\b(approve[ds]?|confirm(ed)?|go ahead|do it|proceed|yes)\b", re.I)
LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- domain ----------
class Action(str, Enum):
    restart_service = "restart_service"
    rollback_deployment = "rollback_deployment"
    clear_cache = "clear_cache"
    scale_up = "scale_up"
    reset_connections = "reset_connections"


ACTIONS = {
    Action.restart_service: {
        "description": "Restart the affected service.",
        "risk": "medium",
        "steps": ["Drain traffic from instances", "Restart service processes", "Wait for health checks to pass"],
    },
    Action.rollback_deployment: {
        "description": "Roll back to the previous known-good release.",
        "risk": "high",
        "steps": ["Locate previous release", "Redeploy previous release", "Run smoke tests"],
    },
    Action.clear_cache: {
        "description": "Flush the application cache.",
        "risk": "low",
        "steps": ["Flush cache keys", "Warm critical keys"],
    },
    Action.scale_up: {
        "description": "Add capacity to the affected service.",
        "risk": "low",
        "steps": ["Request additional instances", "Register instances with load balancer"],
    },
    Action.reset_connections: {
        "description": "Reset and re-establish backend connections.",
        "risk": "low",
        "steps": ["Close stale connections", "Reconnect to backend", "Verify connectivity"],
    },
}

INCIDENTS = {
    "inc-1001": {
        "id": "inc-1001",
        "title": "Checkout failing with KeyError 'cart_id'",
        "service": "api",
        "severity": "high",
        "status": "open",
        "detected_at": "2026-10-07T09:07:02Z",
        "evidence": "3 unhandled KeyError exceptions in /checkout within 30 seconds after the latest deploy.",
        "recommended_action": Action.rollback_deployment,
        "allowed_actions": [Action.rollback_deployment, Action.restart_service],
        "outcomes": {
            Action.rollback_deployment: "Checkout errors stopped and the previous release is serving traffic.",
            Action.restart_service: "Service restarted, but the faulty release is still deployed so errors may return.",
        },
    },
    "inc-1002": {
        "id": "inc-1002",
        "title": "Worker cannot reach Redis",
        "service": "worker",
        "severity": "critical",
        "status": "open",
        "detected_at": "2026-10-07T09:03:21Z",
        "evidence": "Job 4411 abandoned after 3 failed retries with connection refused on redis:6379.",
        "recommended_action": Action.reset_connections,
        "allowed_actions": [Action.reset_connections, Action.restart_service, Action.scale_up],
        "outcomes": {
            Action.reset_connections: "Worker reconnected to Redis and queued jobs are draining.",
            Action.restart_service: "Worker restarted and reconnected to Redis.",
            Action.scale_up: "Extra workers added, but they share the same Redis connection problem.",
        },
    },
    "inc-1003": {
        "id": "inc-1003",
        "title": "Slow queries on orders table",
        "service": "db",
        "severity": "medium",
        "status": "open",
        "detected_at": "2026-10-07T09:05:44Z",
        "evidence": "Two queries on open orders took 1.8 and 2.4 seconds.",
        "recommended_action": Action.clear_cache,
        "allowed_actions": [Action.clear_cache, Action.scale_up],
        "outcomes": {
            Action.clear_cache: "Cache flushed and warmed; order lookups are fast again.",
            Action.scale_up: "Read capacity increased and query latency dropped.",
        },
    },
}

REMEDIATIONS: dict[str, dict] = {}
IDEMPOTENCY: dict[str, str] = {}


def audit(event: str, **data):
    with LOCK, AUDIT_LOG.open("a") as f:
        f.write(json.dumps({"ts": now(), "event": event, **data}, default=str) + "\n")


# ---------- schemas ----------
class Approval(BaseModel):
    approved_by: str = Field("voice-user", description="Who approved the action.")
    channel: str = Field("voice", description="How approval was given.")
    transcript: str = Field(..., min_length=2, description="The user's words approving the action.")
    conversation_id: Optional[str] = Field(None, description="ElevenLabs conversation id, if known.")


class RemediateRequest(BaseModel):
    incident_id: str = Field(..., examples=["inc-1001"])
    action: Optional[Action] = Field(None, description="Defaults to the incident's recommended action.")
    approved: bool = Field(..., description="Must be true unless dry_run is set.")
    approval: Optional[Approval] = None
    dry_run: bool = Field(False, description="Plan only; change nothing and require no approval.")
    reason: Optional[str] = Field(None, max_length=500)


class Step(BaseModel):
    order: int
    description: str
    status: str


class RemediationResult(BaseModel):
    id: str
    incident_id: str
    action: Action
    status: str  # planned | succeeded
    dry_run: bool
    risk: str
    approval: Optional[Approval]
    steps: list[Step]
    summary: str
    spoken_summary: str
    created_at: str
    completed_at: str


class ErrorBody(BaseModel):
    error: str
    message: str


# ---------- app ----------
app = FastAPI(
    title="Remediation API",
    version="1.0.0",
    description="Voice-approved incident remediation (simulated). Called by an ElevenLabs agent.",
)


def require_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(401, detail={"error": "unauthorized", "message": "Missing or invalid X-API-Key."})


def err(status: int, code: str, message: str):
    return HTTPException(status, detail={"error": code, "message": message})


def public_incident(i: dict) -> dict:
    return {k: v for k, v in i.items() if k != "outcomes"}


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok", "time": now(), "open_incidents": sum(i["status"] == "open" for i in INCIDENTS.values())}


@app.get("/api/actions", tags=["meta"], dependencies=[Depends(require_key)])
def list_actions():
    return [{"action": a, **{k: v for k, v in m.items() if k != "steps"}} for a, m in ACTIONS.items()]


@app.get("/api/incidents", tags=["incidents"], dependencies=[Depends(require_key)])
def list_incidents(status: Optional[str] = Query(None, pattern="^(open|resolved)$")):
    items = [public_incident(i) for i in INCIDENTS.values() if not status or i["status"] == status]
    return {"count": len(items), "incidents": items}


@app.get("/api/incidents/{incident_id}", tags=["incidents"], dependencies=[Depends(require_key)])
def get_incident(incident_id: str):
    inc = INCIDENTS.get(incident_id)
    if not inc:
        raise err(404, "incident_not_found", f"No incident '{incident_id}'.")
    return public_incident(inc)


@app.post(
    "/api/remediate",
    tags=["remediation"],
    response_model=RemediationResult,
    dependencies=[Depends(require_key)],
    responses={
        401: {"model": ErrorBody}, 403: {"model": ErrorBody}, 404: {"model": ErrorBody},
        409: {"model": ErrorBody}, 422: {"model": ErrorBody},
    },
)
def remediate(req: RemediateRequest, idempotency_key: Optional[str] = Header(None)):
    with LOCK:
        if idempotency_key and idempotency_key in IDEMPOTENCY:
            return REMEDIATIONS[IDEMPOTENCY[idempotency_key]]

    inc = INCIDENTS.get(req.incident_id)
    if not inc:
        raise err(404, "incident_not_found", f"No incident '{req.incident_id}'.")
    action = req.action or inc["recommended_action"]
    if action not in inc["allowed_actions"]:
        allowed = ", ".join(a.value for a in inc["allowed_actions"])
        raise err(422, "action_not_allowed", f"'{action.value}' is not allowed for {inc['id']}. Allowed: {allowed}.")

    if not req.dry_run:
        if not req.approved or req.approval is None:
            audit("rejected_unapproved", incident=inc["id"], action=action)
            raise err(403, "approval_required", "Explicit approval is required before remediating.")
        if not APPROVAL_RE.search(req.approval.transcript):
            audit("rejected_unclear_approval", incident=inc["id"], transcript=req.approval.transcript)
            raise err(403, "approval_unclear", "Approval transcript does not contain a clear approval.")
        if inc["status"] != "open":
            raise err(409, "already_resolved", f"{inc['id']} is already {inc['status']}.")

    meta = ACTIONS[action]
    started = now()
    done = "planned" if req.dry_run else "completed"
    steps = [Step(order=n, description=s, status=done) for n, s in enumerate(meta["steps"], 1)]
    outcome = inc["outcomes"][action]
    if req.dry_run:
        summary = f"Dry run for {inc['id']}: would {meta['description'].lower()} Steps: " + "; ".join(meta["steps"]) + "."
        spoken = f"Here is the plan for {inc['title']}. I would {meta['description'].lower()} Nothing has been changed yet."
    else:
        summary = f"{inc['id']} ({inc['title']}): ran {action.value}. {outcome} Steps: " + "; ".join(meta["steps"]) + "."
        spoken = f"Done. I ran {action.value.replace('_', ' ')} for {inc['title']}. {outcome}"

    result = {
        "id": f"rem-{uuid.uuid4().hex[:8]}",
        "incident_id": inc["id"],
        "action": action,
        "status": "planned" if req.dry_run else "succeeded",
        "dry_run": req.dry_run,
        "risk": meta["risk"],
        "approval": req.approval,
        "steps": steps,
        "summary": summary,
        "spoken_summary": spoken,
        "created_at": started,
        "completed_at": now(),
    }
    with LOCK:
        REMEDIATIONS[result["id"]] = result
        if idempotency_key:
            IDEMPOTENCY[idempotency_key] = result["id"]
        if not req.dry_run:
            inc["status"] = "resolved"
    audit("remediation", **json.loads(RemediationResult(**result).model_dump_json()))
    return result


@app.get("/api/remediations", tags=["remediation"], dependencies=[Depends(require_key)])
def list_remediations(incident_id: Optional[str] = None, limit: int = Query(50, ge=1, le=200)):
    items = [r for r in REMEDIATIONS.values() if not incident_id or r["incident_id"] == incident_id]
    items = sorted(items, key=lambda r: r["created_at"], reverse=True)[:limit]
    return {"count": len(items), "remediations": items}


@app.get("/api/remediations/{rem_id}", tags=["remediation"], response_model=RemediationResult,
         dependencies=[Depends(require_key)])
def get_remediation(rem_id: str):
    if rem_id not in REMEDIATIONS:
        raise err(404, "remediation_not_found", f"No remediation '{rem_id}'.")
    return REMEDIATIONS[rem_id]


@app.post("/api/reset", tags=["meta"], dependencies=[Depends(require_key)])
def reset():
    """Re-open all incidents and clear history (demo helper)."""
    with LOCK:
        for i in INCIDENTS.values():
            i["status"] = "open"
        REMEDIATIONS.clear()
        IDEMPOTENCY.clear()
    return {"status": "reset"}


@app.get("/api/session", tags=["agent"])
def session():
    """Config for the browser page. Uses a signed URL so the ElevenLabs key never reaches the browser."""
    agent_id, xi = os.environ.get("ELEVENLABS_AGENT_ID"), os.environ.get("ELEVENLABS_API_KEY")
    if not agent_id or not xi:
        raise err(503, "agent_not_configured", "Run create_agent.py and set ELEVENLABS_AGENT_ID / ELEVENLABS_API_KEY.")
    r = requests.get(
        "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
        params={"agent_id": agent_id}, headers={"xi-api-key": xi}, timeout=15,
    )
    if not r.ok:
        raise err(502, "signed_url_failed", f"ElevenLabs returned {r.status_code}: {r.text[:200]}")
    return {"signed_url": r.json()["signed_url"], "api_key": API_KEY}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
