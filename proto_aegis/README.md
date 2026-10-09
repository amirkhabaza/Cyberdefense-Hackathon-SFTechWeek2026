# proto_aegis: LLM-centered incident loop

Implements the architecture diagram: an LLM (OpenAI) is the central reasoning engine and four tools surround it.

```
ClickHouse ──#1 Alert──▶  LLM  ──#2 Query──▶ MongoDB Atlas (vector memory / RAG)
                          ▲ │  ◀──#3 Intel──
 Semgrep ◀──#4 Scan───────┘ │
         ──#5 Patch────────▶│  ──#6 Voice──▶ ElevenLabs ──#7 Approve──▶ LLM
```

| # | Flow | What happens |
|---|---|---|
| 1 | ClickHouse → LLM | Detects active SQL injection in `traffic_events` (the tables `proto_board/seed.py` fills) |
| 2 | LLM → MongoDB | LLM writes a memory query; Atlas Vector Search over `intel` (playbooks + past incidents) |
| 3 | MongoDB → LLM | Top matches come back as context |
| 4 | LLM → Semgrep | Scout Agent (`../proto_semgrep`) scans the vulnerable file |
| 5 | Semgrep → LLM | LLM writes a patch from findings + intel; Semgrep re-scans the patch to verify it |
| 6 | LLM → ElevenLabs | Spoken briefing that asks for approval |
| 7 | ElevenLabs → LLM | Operator approves (typed, or spoken with `--voice-approval`); only then is the patch "deployed" to `out/<run>/deployed/` |

The Python code drives the order of steps; the LLM does the reasoning inside each (query, patch, briefing, report). Nothing in the real
project is modified: patches are written under `out/`.

## Run it now (no keys needed)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run_incident.py --demo-alert              # type "approve" at the prompt
.venv/bin/python run_incident.py --demo-alert --auto-approve
```
Needs `../proto_semgrep/.venv` (Semgrep). Every step is labeled `[live]` or `[stub]`, and a trace goes to `out/<run>/trace.json`.

## Going live (all optional, any subset works)
Copy `.env.example` to `.env` (ClickHouse and Mongo settings are also read from `../proto_board/.env`).
- `OPENAI_API_KEY`: real reasoning and embeddings instead of the stub.
- `CLICKHOUSE_*`: live alert from `proto_board` traffic; drop `--demo-alert` (run `proto_board/seed.py --live` first).
- `MONGODB_URI` + `OPENAI_API_KEY`: then `.venv/bin/python seed_intel.py` embeds `intel/playbooks.json` and creates the `intel_vector` index; searches then use Atlas Vector Search.
- `ELEVENLABS_API_KEY`: speaks the briefing. For spoken approval: `pip install sounddevice` and use `--voice-approval` (uses ElevenLabs speech-to-text).

## Notes
- Semgrep scans a temp copy outside git, so `.gitignore` can't make it skip files. The rescan only counts as verification because the same method found 7 issues in the original.
- The stub patch is canned for the demo file. With `OPENAI_API_KEY` the LLM writes the patch for any file, and the Semgrep rescan decides whether it is accepted.
- Live LLM calls send the file contents and findings to OpenAI.
