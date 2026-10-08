#!/usr/bin/env python3
"""Scout Agent: runs Semgrep with deterministic rules and reports findings.

Detects two vulnerability classes:
  - SQL injection        -> recommended fix: parameterized query
  - Hard-coded secrets   -> recommended fix: secret/env migration
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).parent
SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}


@dataclass
class Finding:
    rule_id: str
    category: str
    severity: str
    file: str
    line: int
    end_line: int
    snippet: str
    message: str
    cwe: str
    remediation: str
    fix: str


def find_semgrep():
    for c in (shutil.which("semgrep"), HERE / ".venv" / "bin" / "semgrep"):
        if c and Path(c).exists():
            return str(c)
    sys.exit("semgrep not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")


def mask_secret(line: str) -> str:
    """Never print a full secret: keep the first 3 chars of any quoted value of 8+ chars."""
    return re.sub(r'(["\'])([^"\']{3})[^"\']{5,}(["\'])', r"\1\2…***\3", line)


class ScoutAgent:
    def __init__(self, rules_dir=HERE / "rules"):
        self.rules_dir = Path(rules_dir)
        self.semgrep = find_semgrep()

    def scan(self, target) -> list[Finding]:
        proc = subprocess.run(
            [self.semgrep, "--config", str(self.rules_dir), "--json", "--quiet", "--metrics=off",
             "--disable-version-check", str(target)],
            capture_output=True, text=True,
        )
        if proc.returncode not in (0, 1) or not proc.stdout.strip():
            sys.exit(f"semgrep failed (exit {proc.returncode}):\n{proc.stderr.strip()[-1500:]}")
        data = json.loads(proc.stdout)
        for e in data.get("errors", []):
            print(f"warning: {e.get('message', e)}", file=sys.stderr)
        unique = {}  # one finding per file/line/category, even if several rules match
        for r in data["results"]:
            f = self._to_finding(r)
            unique.setdefault((f.file, f.line, f.category), f)
        return sorted(unique.values(), key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.file, f.line))

    @staticmethod
    def _to_finding(r) -> Finding:
        meta = r["extra"].get("metadata", {})
        category = meta.get("category", "other")
        lines = Path(r["path"]).read_text(errors="replace").splitlines()
        snippet = lines[r["start"]["line"] - 1].strip() if lines else ""
        if category == "hardcoded-secret":
            snippet = mask_secret(snippet)
        return Finding(
            rule_id=r["check_id"].split(".", 1)[-1] if r["check_id"].startswith("scout") else r["check_id"],
            category=category,
            severity=r["extra"]["severity"],
            file=os.path.relpath(r["path"]),
            line=r["start"]["line"],
            end_line=r["end"]["line"],
            snippet=snippet,
            message=" ".join(r["extra"]["message"].split()),
            cwe=meta.get("cwe", ""),
            remediation=meta.get("remediation", ""),
            fix=" ".join(str(meta.get("fix", "")).split()),
        )


def print_report(findings, target):
    print(f"\nScout Agent report for {target}")
    print("=" * 60)
    if not findings:
        print("No findings.")
        return
    by_cat = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)
    print(f"{len(findings)} finding(s): " + ", ".join(f"{len(v)} {k}" for k, v in by_cat.items()))
    for cat, items in by_cat.items():
        print(f"\n## {cat}  ({items[0].cwe})  ->  suggested fix: {items[0].remediation}")
        for f in items:
            print(f"\n  [{f.severity}] {f.file}:{f.line}")
            print(f"    code:    {f.snippet}")
            print(f"    issue:   {f.message}")
            print(f"    fix:     {f.fix}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=str(HERE / "target"), help="file or directory to scan")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    ap.add_argument("--fail-on-findings", action="store_true", help="exit 1 if anything is found (for CI)")
    args = ap.parse_args()

    findings = ScoutAgent().scan(args.target)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        print_report(findings, args.target)
    if args.fail_on_findings and findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
