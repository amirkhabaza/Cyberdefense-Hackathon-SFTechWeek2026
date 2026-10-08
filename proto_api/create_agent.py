#!/usr/bin/env python3
"""Create the ElevenLabs agent (with client tools) and save its id to .env."""
import json
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).parent
BASE = "https://api.elevenlabs.io/v1/convai"


def load_env():
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


PROMPT = """You are an on-call incident assistant that can fix production incidents by voice.

Flow:
1. Greet the user briefly. If asked what is going on, call list_incidents and read out a short spoken overview.
2. To fix something, first state exactly which incident and which action you will run, and its risk, then ask: "Do you approve?"
3. Only if the user then clearly approves (e.g. "approve", "yes, go ahead", "do it") call remediate_incident with the incident_id, the action, and approval_transcript set to the user's exact approving words.
4. Never call remediate_incident without that explicit spoken approval, even if the user seems impatient. If the user is unsure or says no, do not call it. If asked what a fix would do first, call it with dry_run=true (no approval needed).
5. After the tool returns, speak the spoken_summary in your own words in one or two sentences. If it returns an error, explain it plainly and do not retry without asking.
Keep replies short; this is a voice conversation. Do not read ids letter by letter."""

PARAMS = {
    "remediate_incident": {
        "description": "Run an approved remediation for an incident, or a dry run plan. Requires the user's spoken approval unless dry_run is true.",
        "parameters": {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "Incident id such as inc-1001."},
                "action": {"type": "string", "description": "One of restart_service, rollback_deployment, clear_cache, scale_up, reset_connections. Omit to use the recommended action."},
                "approval_transcript": {"type": "string", "description": "The user's exact words approving the action. Omit for dry runs."},
                "dry_run": {"type": "boolean", "description": "True to only plan the fix without changing anything."},
            },
            "required": ["incident_id"],
        },
    },
    "list_incidents": {
        "description": "List currently open incidents with severity, evidence and recommended action.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def main():
    load_env()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("Set ELEVENLABS_API_KEY in .env first.")
    h = {"xi-api-key": key, "Content-Type": "application/json"}

    tools = [
        {"type": "client", "name": n, "description": d["description"], "parameters": d["parameters"],
         "expects_response": True, "response_timeout_secs": 30}
        for n, d in PARAMS.items()
    ]
    tool_ids = []
    for t in tools:
        r = requests.post(f"{BASE}/tools", headers=h, json={"tool_config": t}, timeout=30)
        if r.ok:
            tool_ids.append(r.json()["id"])
        else:
            print(f"Tool create failed ({r.status_code}): {r.text[:200]}; falling back to inline tools.")
            tool_ids = []
            break

    prompt = {"prompt": PROMPT, "llm": os.environ.get("AGENT_LLM", "claude-sonnet-4-5"), "temperature": 0}
    prompt.update({"tool_ids": tool_ids} if tool_ids else {"tools": tools})
    body = {
        "name": "Voice Remediation Agent",
        "conversation_config": {
            "agent": {"first_message": "Hi, I'm your incident assistant. Want a rundown of what's open?", "language": "en", "prompt": prompt},
            "tts": {"voice_id": os.environ.get("ELEVENLABS_VOICE_ID", "cjVigY5qzO86Huf0OWal")},
        },
    }
    r = requests.post(f"{BASE}/agents/create", headers=h, json=body, timeout=30)
    if not r.ok:
        sys.exit(f"Agent create failed ({r.status_code}): {r.text}")
    agent_id = r.json()["agent_id"]

    env = HERE / ".env"
    lines = [l for l in (env.read_text().splitlines() if env.exists() else []) if not l.startswith("ELEVENLABS_AGENT_ID=")]
    env.write_text("\n".join(lines + [f"ELEVENLABS_AGENT_ID={agent_id}"]) + "\n")
    print(f"Created agent {agent_id} and saved it to .env")


if __name__ == "__main__":
    main()
