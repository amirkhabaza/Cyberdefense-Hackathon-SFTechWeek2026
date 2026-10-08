# proto_semgrep: Scout Agent

The Scout Agent runs Semgrep with deterministic rules and prints findings. Detection only; fixing is a later agent's job.

```
target code -> Semgrep (rules/) -> Scout Agent -> findings (category, location, code, issue, suggested fix)
```

| Vulnerability | Semgrep rule | Suggested remediation |
|---|---|---|
| SQL injection (CWE-89) | `rules/sql-injection.yml`: `execute()` called with an f-string, `+`, `%` or `.format()` query | **Parameterized query** |
| Hard-coded secret (CWE-798) | `rules/hardcoded-secret.yml`: password/key/token-named variable assigned a string literal; AWS `AKIA...` key ids | **Secret/env migration** |

## Setup and run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scout_agent.py                      # scans target/
.venv/bin/python scout_agent.py path/to/code --json  # machine-readable findings
.venv/bin/python scout_agent.py --fail-on-findings   # exit 1 if anything found (CI)
```

## Layout
- `scout_agent.py`: the agent (`ScoutAgent.scan()` returns a list of `Finding`s; also a CLI)
- `rules/`: Semgrep rules; each carries `category`, `cwe`, `remediation` and `fix` metadata that the agent reports
- `target/vulnerable_app.py`: demo code with 4 SQL injections and 3 hard-coded (fake) secrets
- `target/safe_app.py`: control file, no findings expected

## Notes
- Secret values are masked in output (`"Sup…***"`); they are never printed in full.
- Rules are Python-only for the prototype and use Semgrep's pattern matching, not taint tracking, so a query built into a variable first and executed later is not caught.
- Runs with `--metrics=off`, entirely local.
