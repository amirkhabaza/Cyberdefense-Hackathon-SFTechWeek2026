#!/usr/bin/env python3
"""Project Aegis: the LLM-centered incident loop from the architecture diagram.

  #1 ClickHouse alert -> LLM   #2 LLM queries MongoDB memory   #3 intel returns
  #4 LLM asks Semgrep to scan  #5 Semgrep results -> LLM patch (verified by rescan)
  #6 LLM briefs the operator by voice (ElevenLabs)   #7 operator approves -> LLM deploys
Each component runs live when its credentials are in .env, otherwise as a labeled [stub].
"""
import argparse
import difflib
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import clickhouse_src
import config
import llm
import memory
import scanner
import voice
from flows import Trace

TARGET = config.ROOT / "proto_semgrep" / "target" / "vulnerable_app.py"


def sql_and_secret(findings):
    return [f for f in findings if f.category in ("sql-injection", "hardcoded-secret")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=str(TARGET), help="vulnerable Python file to scan and patch")
    ap.add_argument("--voice-approval", action="store_true", help="listen on the microphone for the approval")
    ap.add_argument("--auto-approve", action="store_true", help="skip the approval prompt (testing only)")
    ap.add_argument("--demo-alert", action="store_true", help="use a canned alert instead of querying ClickHouse")
    args = ap.parse_args()

    trace = Trace()
    run = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = config.HERE / "out" / run
    out.mkdir(parents=True)
    target = Path(args.target)
    print(f"Project Aegis run {run}  (LLM: {'OpenAI' if llm.live() else 'stub'})")

    # #1 ClickHouse -> LLM
    alert, mode = clickhouse_src.detect(force_stub=args.demo_alert)
    if not alert:
        print("\nNo active exploitation in ClickHouse. Nothing to do.")
        return
    trace.flow(1, mode, f"{alert['attack_type']} on {alert['path']}: {alert['sources']} sources, "
                        f"{alert['active_exploit_rps']:.0f} exploit req/s ({alert['malicious_pct']:.0%} of traffic), "
                        f"confidence {alert['confidence']:.0%}")

    # #2 LLM -> MongoDB, #3 MongoDB -> LLM
    query = llm.formulate_query(alert)
    trace.flow(2, "live" if llm.live() else "stub", f'memory query: "{query}"')
    intel, mode = memory.search(query, k=3)
    trace.flow(3, mode, "\n".join(f"{d['score']:.2f}  {d['title']}  ({d['kind']})" for d in intel))

    # #4 LLM -> Semgrep. Scan in a temp dir outside git so Semgrep's .gitignore handling cannot skip files.
    work = Path(tempfile.mkdtemp(prefix="aegis_"))
    (work / "before").mkdir()
    (work / "after").mkdir()
    shutil.copy(target, work / "before" / target.name)
    findings = sql_and_secret(scanner.scan(work / "before"))
    if not findings:
        sys.exit("Semgrep found nothing in the target; refusing to continue without findings.")
    trace.flow(4, "live", f"{len(findings)} findings in {target.name}\n" + "\n".join(
        f"line {f.line}: {f.category} ({f.rule_id})" for f in findings))

    # #5 Semgrep -> LLM: the LLM writes a patch, Semgrep re-scans it as verification.
    original = target.read_text()
    patch = llm.propose_patch(original, target.name, findings, intel)
    (work / "after" / target.name).write_text(patch["patched_code"])
    remaining = sql_and_secret(scanner.scan(work / "after"))
    verified = not remaining
    verification = ("Semgrep re-scan of the patch is clean." if verified
                    else f"Semgrep still finds {len(remaining)} issue(s) in the patch.")
    diff = "".join(difflib.unified_diff(original.splitlines(True), patch["patched_code"].splitlines(True),
                                        "before/" + target.name, "after/" + target.name))
    (out / "fix.patch").write_text(diff)
    trace.flow(5, "live" if llm.live() else "stub",
               f"{patch['explanation']}\nverification: {verification}\ndiff saved to {out / 'fix.patch'}")
    if not verified:
        print("\nPatch did not verify; not asking for approval.")
        trace.save(out / "trace.json")
        sys.exit(2)

    # #6 LLM -> ElevenLabs
    text = llm.briefing(alert, findings, patch, verification)
    vmode = voice.speak(text)
    trace.flow(6, vmode, text)

    # #7 ElevenLabs -> LLM
    approved, transcript, amode = voice.get_approval(args.voice_approval, args.auto_approve)
    trace.flow(7, amode, f'“{transcript}” -> {"APPROVED" if approved else "NOT approved"}')
    if not approved:
        print("\nNo approval. Nothing deployed.")
        trace.save(out / "trace.json")
        return

    deployed = out / "deployed"
    deployed.mkdir()
    (deployed / target.name).write_text(patch["patched_code"])
    closing = llm.final_report(alert, patch, deployed)
    print(f"\n{closing}")
    voice.speak(closing)
    trace.save(out / "trace.json")
    print(f"\nTrace: {out / 'trace.json'}")


if __name__ == "__main__":
    main()
