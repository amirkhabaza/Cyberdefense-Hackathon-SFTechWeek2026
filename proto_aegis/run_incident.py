#!/usr/bin/env python3
"""Project Aegis CLI: runs the LLM-centered incident loop in the terminal.

For the dashboard version (flows shown live, Approve button and ElevenLabs voice approving the SAME run), start
../proto_board/server.py instead. Both use engine.py.
  #1 ClickHouse alert -> LLM   #2 LLM queries MongoDB memory   #3 intel returns
  #4 LLM asks Semgrep to scan  #5 Semgrep results -> LLM patch (verified by rescan)
  #6 LLM briefs the operator by voice (ElevenLabs)   #7 operator approves -> LLM deploys
Each component runs live when its credentials are in .env, otherwise as a labeled [stub].
"""
import argparse

import config
import engine
import llm
import voice


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=str(engine.TARGET), help="vulnerable Python file to scan and patch")
    ap.add_argument("--voice-approval", action="store_true", help="listen on the microphone for the approval")
    ap.add_argument("--auto-approve", action="store_true", help="skip the approval prompt (testing only)")
    ap.add_argument("--demo-alert", action="store_true", help="use a canned alert instead of querying ClickHouse")
    args = ap.parse_args()

    print(f"Project Aegis (LLM: {'OpenAI' if llm.live() else 'stub'})")
    run = engine.Run()
    run.execute(approval_provider=lambda: voice.get_approval(args.voice_approval, args.auto_approve)[:2] + ("terminal",),
                demo_alert=args.demo_alert, target=args.target)
    snap = run.snapshot()
    if snap["error"]:
        raise SystemExit(f"\n{snap['error']}")
    print(f"\n{snap['closing']}")


if __name__ == "__main__":
    main()
