# proto_api: voice-approved remediation

An ElevenLabs conversational agent talks to you about open incidents. When you clearly approve a fix by voice,
it calls the local `POST /api/remediate` endpoint, speaks a summary of the fix, and the page prints the same summary.
Remediation is simulated; nothing touches real infrastructure.

```
mic -> ElevenLabs agent --(client tool, in your browser)--> http://127.0.0.1:8000/api/remediate
                    ^                                              |
                    +---------- spoken_summary / summary ----------+  (also printed on the page)
```

The tools are *client tools*: they run in your browser, so the agent can reach `localhost` with no tunnel or ngrok.

## Files
| Path | What it is |
|---|---|
| `server.py` | FastAPI app: the `/api/*` endpoints, simulated incidents, audit log |
| `create_agent.py` | Creates the ElevenLabs agent and its two client tools, saves `ELEVENLABS_AGENT_ID` to `.env` |
| `static/index.html` | Browser page: starts the voice conversation, runs the client tools, prints summaries |
| `audit.jsonl` | Created at runtime; one line per remediation or rejection (gitignored) |

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env            # add ELEVENLABS_API_KEY (and optionally change REMEDIATE_API_KEY)
python create_agent.py          # creates the agent + tools, saves ELEVENLABS_AGENT_ID to .env
python server.py                # http://127.0.0.1:8000  (Swagger UI at /docs)
```
Your ElevenLabs API key needs the **ElevenAgents (convai) Write** permission for `create_agent.py` to work.
Open http://127.0.0.1:8000, click Start, and try: "What's going on?" then "Fix the checkout incident." then "Approve."

## API (all `/api/*` except health and session need header `X-API-Key`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness, open incident count |
| GET | `/api/actions` | Action catalog with risk levels |
| GET | `/api/incidents[?status=open\|resolved]` | List incidents |
| GET | `/api/incidents/{id}` | One incident |
| **POST** | **`/api/remediate`** | Run (or dry-run) a remediation |
| GET | `/api/remediations[?incident_id=&limit=]` | History |
| GET | `/api/remediations/{id}` | One remediation |
| POST | `/api/reset` | Re-open all incidents (demo) |
| GET | `/api/session` | Signed ElevenLabs URL for the page |

### POST /api/remediate
```bash
curl -s -X POST localhost:8000/api/remediate \
  -H 'X-API-Key: dev-local-key' -H 'Idempotency-Key: abc123' -H 'Content-Type: application/json' \
  -d '{"incident_id":"inc-1001","action":"rollback_deployment","approved":true,
       "approval":{"transcript":"Yes, go ahead and approve it","channel":"voice"}}'
```
Body: `incident_id`, `action` (defaults to the recommended one), `approved`, `approval{transcript, approved_by, channel, conversation_id}`, `dry_run`, `reason`.
Returns `id, status, steps[], summary, spoken_summary, risk, ...`.

Errors are `{"detail": {"error": code, "message": ...}}`: 401 `unauthorized`, 403 `approval_required` / `approval_unclear`,
404 `incident_not_found`, 409 `already_resolved`, 422 `action_not_allowed` or schema errors.
Safeguards: approval is enforced server-side (not just by the agent prompt), `dry_run` needs no approval,
`Idempotency-Key` makes retries safe, and every decision is appended to `audit.jsonl`.

Note: the server hands the page its API key via `/api/session`. That is fine for a localhost demo, not for production.

## Agent behavior
The agent's prompt (in `create_agent.py`) tells it to list incidents on request, state the incident, action and risk, ask
"Do you approve?", and call `remediate_incident` only after clear spoken approval, passing the user's exact words as
`approval_transcript`. It can also call it with `dry_run` to explain a fix first. The server re-checks approval regardless.

## Simulated data
Three incidents are seeded in `server.py`: `inc-1001` (checkout KeyError, recommends rollback), `inc-1002` (worker cannot reach Redis,
recommends reset connections) and `inc-1003` (slow orders queries, recommends clear cache). `POST /api/reset` re-opens them.
State is in memory and resets when the server restarts.
