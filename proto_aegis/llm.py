"""The central reasoning engine. Uses OpenAI when OPENAI_API_KEY is set, otherwise deterministic [stub] logic."""
import json
import os
import re

import requests

import config

BASE = "https://api.openai.com/v1"


def pretty(attack_type):
    return {"sql_injection": "SQL injection"}.get(attack_type, attack_type.replace("_", " "))


def live():
    return config.have("OPENAI_API_KEY")


def _headers():
    return {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}


def chat(system, user, json_mode=False):
    body = {"model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(f"{BASE}/chat/completions", headers=_headers(), json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def embed(text):
    r = requests.post(f"{BASE}/embeddings", headers=_headers(), timeout=60,
                      json={"model": os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small"), "input": text})
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


# ---- reasoning steps ----
def formulate_query(alert):
    """#2: turn the alert into a memory query."""
    if live():
        return chat("You are a security incident analyst. Write one short search query (max 25 words) to retrieve "
                    "remediation playbooks and similar past incidents for this alert. Output only the query.",
                    json.dumps(alert)).strip()
    return (f"{alert['attack_type'].replace('_', ' ')} active exploitation on {alert['path']} endpoint: "
            "remediation playbook, parameterized queries, hard-coded secrets, past incidents")


def propose_patch(code, filename, findings, intel):
    """#5: produce a patched file from Semgrep findings plus retrieved intel. Returns dict with patched_code, explanation."""
    if live():
        out = chat(
            "You are a senior security engineer. Fix every listed vulnerability in the file with minimal changes. "
            "SQL injection -> parameterized queries (keep sqlite3 style '?' placeholders). Hard-coded secrets -> "
            "os.environ[...] reads. Keep function names and behavior. Respond as JSON: "
            '{"patched_code": "<full file>", "explanation": "<2 sentences>"}.',
            json.dumps({"file": filename, "code": code,
                        "findings": [{"rule": f.rule_id, "line": f.line, "category": f.category, "fix": f.fix} for f in findings],
                        "playbooks": [{"title": d["title"], "text": d["text"]} for d in intel]}),
            json_mode=True)
        data = json.loads(out)
        data["patched_code"] = re.sub(r"^```(?:python)?\n|\n```$", "", data["patched_code"].strip())
        return data
    return {"patched_code": (config.HERE / "fixtures" / "vulnerable_app.patched.py").read_text(),
            "explanation": "Replaced string-built SQL with parameterized queries and moved the three hard-coded "
                           "credentials to environment variables."}


def briefing(alert, findings, patch, verification):
    """#6: the spoken briefing that asks for approval."""
    facts = {"alert": alert, "findings": len(findings), "patch": patch["explanation"], "verification": verification}
    if live():
        return chat("You are Aegis, a voice security copilot. Write a spoken briefing of at most 80 words for the on-call "
                    "engineer: what is happening, what you found in the code, the verified fix, and end by asking them to "
                    "say 'approve' to deploy it. Plain prose, no markdown or lists.", json.dumps(facts, default=str)).strip()
    return (f"Active exploitation detected. {pretty(alert['attack_type']).capitalize()} against {alert['path']} "
            f"from {alert['sources']} sources, {alert['malicious_pct']:.0%} of traffic. Semgrep found {len(findings)} "
            f"issues in the code. {patch['explanation']} {verification} Say approve to deploy the fix.")


def final_report(alert, patch, deployed_to):
    """#7 follow-up: closing summary after approval."""
    if live():
        return chat("Write a two-sentence closing incident summary for the on-call engineer, plain prose.",
                    json.dumps({"alert": alert, "patch": patch["explanation"], "deployed_to": str(deployed_to)}, default=str)).strip()
    return (f"Approved and deployed. The {pretty(alert['attack_type'])} fix for {alert['path']} is in {deployed_to}. "
            "Rotate the exposed credentials and keep watching traffic for the next few minutes.")
