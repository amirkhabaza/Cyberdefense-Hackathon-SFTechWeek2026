"""Prints and records the numbered flows from the architecture diagram."""
import json
import time

FLOWS = {
    1: ("Alert", "ClickHouse", "LLM"),
    2: ("Query", "LLM", "MongoDB"),
    3: ("Intel", "MongoDB", "LLM"),
    4: ("Scan", "LLM", "Semgrep"),
    5: ("Patch", "Semgrep", "LLM"),
    6: ("Voice", "LLM", "ElevenLabs"),
    7: ("Approve", "ElevenLabs", "LLM"),
}


class Trace:
    def __init__(self):
        self.events = []

    def flow(self, n, mode, detail):
        name, src, dst = FLOWS[n]
        self.events.append({"flow": n, "name": name, "from": src, "to": dst, "mode": mode, "detail": detail, "t": time.time()})
        print(f"\n#{n} {name:<8} {src} -> {dst}   [{mode}]")
        for line in str(detail).splitlines():
            print(f"     {line}")

    def save(self, path):
        path.write_text(json.dumps(self.events, indent=2))
