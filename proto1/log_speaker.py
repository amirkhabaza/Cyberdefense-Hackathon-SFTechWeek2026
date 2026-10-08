#!/usr/bin/env python3
"""Read a bunch of logs, summarize them with Claude, and speak the summary with ElevenLabs."""
import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # ElevenLabs "George"
SEVERE = re.compile(r"\b(ERROR|CRITICAL|FATAL|EXCEPTION|TRACEBACK|WARN(ING)?)\b", re.I)
MAX_CHARS_PER_FILE = 12_000


def load_env(path=".env"):
    p = Path(__file__).with_name(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def expand(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += [f for f in glob.glob(os.path.join(p, "**", "*"), recursive=True) if os.path.isfile(f)]
        else:
            files += [f for f in glob.glob(p, recursive=True) if os.path.isfile(f)]
    return sorted(set(files))


def condense(text):
    """Keep logs within budget: all warn/error lines (deduped by count) plus the tail."""
    lines = text.splitlines()
    if len(text) <= MAX_CHARS_PER_FILE:
        return text
    severe = [l for l in lines if SEVERE.search(l)]
    tail = lines[-40:]
    out = [f"[{len(lines)} lines total, condensed]", "-- warnings/errors --", *severe[:150], "-- last lines --", *tail]
    return "\n".join(out)[:MAX_CHARS_PER_FILE]


def summarize(logs, model):
    prompt = (
        "You are an on-call assistant. Below are log files. Write a spoken briefing of about 100-150 words: "
        "what happened overall, the most important errors or warnings (group repeats and give counts), "
        "likely causes, and what to look at first. Plain prose only, no markdown, bullets, or special "
        "characters, because it will be read aloud. Say times naturally.\n\n" + logs
    )
    r = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 600, "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    r.raise_for_status()
    return "".join(b["text"] for b in r.json()["content"] if b["type"] == "text").strip()


def speak(text, voice_id, out_path):
    r = requests.post(
        ELEVENLABS_URL.format(voice_id=voice_id),
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"},
        json={"text": text, "model_id": "eleven_multilingual_v2"},
        timeout=120,
    )
    r.raise_for_status()
    Path(out_path).write_bytes(r.content)


def play(path):
    player = {"darwin": ["afplay"], "linux": ["mpg123", "-q"]}.get(sys.platform)
    if not player:
        print(f"Saved audio to {path}; play it manually.")
        return
    subprocess.run([*player, path], check=False)


def main():
    load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="log files, directories, or globs")
    ap.add_argument("--out", help="save the mp3 here (default: temp file)")
    ap.add_argument("--no-speak", action="store_true", help="print the summary only")
    ap.add_argument("--voice", default=os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE))
    ap.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL))
    args = ap.parse_args()

    files = expand(args.paths)
    if not files:
        sys.exit("No log files found.")
    logs = "\n\n".join(
        f"=== {f} ===\n{condense(Path(f).read_text(errors='replace'))}" for f in files
    )
    print(f"Read {len(files)} file(s). Summarizing...")
    summary = summarize(logs, args.model)
    print(f"\n{summary}\n")
    if args.no_speak:
        return
    out = args.out or os.path.join(tempfile.gettempdir(), "log_summary.mp3")
    speak(summary, args.voice, out)
    print(f"Audio saved to {out}")
    play(out)


if __name__ == "__main__":
    main()
