"""The Aegis incident loop as a reusable engine. One Run holds the state of one incident response.

The CLI (run_incident.py) and the dashboard server (../proto_board/server.py) both drive this same engine, so there is exactly one
pending approval: the dashboard's Approve button, the ElevenLabs voice reply, and the terminal prompt all resolve it.
"""
import difflib
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import clickhouse_src
import config
import llm
import memory
import scanner
import voice
from flows import FLOWS, Trace

TARGET = config.ROOT / "proto_semgrep" / "target" / "vulnerable_app.py"
APPROVAL_TIMEOUT = 600


def _findings(fs):
    return [f for f in fs if f.category in ("sql-injection", "hardcoded-secret")]


class Run:
    def __init__(self):
        self._lock = threading.Lock()
        self.run_id = 0
        self.reset()

    # ---- state ----
    def reset(self):
        old = getattr(self, "_event", None)
        if old is not None:  # wake a thread still waiting on the previous run so it can exit
            self._decision = (False, "(reset)", "reset")
            old.set()
        with self._lock:
            self.run_id += 1
            self.status = "idle"  # idle | running | awaiting_approval | deploying | complete | rejected | failed
            self.flows = {n: {"n": n, "name": v[0], "from": v[1], "to": v[2], "state": "pending", "mode": "", "detail": ""}
                          for n, v in FLOWS.items()}
            self.briefing = self.diff = self.verification = self.closing = self.error = ""
            self.audio_path = None
            self.approval = None
            self.deployed = None
            self._event = threading.Event()
            self._decision = None

    def snapshot(self):
        with self._lock:
            return {"run_id": self.run_id, "status": self.status, "flows": [self.flows[n] for n in sorted(self.flows)],
                    "briefing": self.briefing, "diff": self.diff, "verification": self.verification,
                    "closing": self.closing, "error": self.error, "approval": self.approval,
                    "audio_ready": bool(self.audio_path), "deployed": str(self.deployed) if self.deployed else None,
                    "awaiting_approval": self.status == "awaiting_approval"}

    def _on_flow(self, ev):
        with self._lock:
            self.flows[ev["flow"]].update(state="done", mode=ev["mode"], detail=ev["detail"])

    def _set(self, **kw):
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    # ---- approval (button, voice, or terminal all land here) ----
    def approve(self, transcript, source):
        """Returns {'accepted': bool, 'approved': bool, 'unclear': bool}."""
        with self._lock:
            if self.status != "awaiting_approval":
                return {"accepted": False, "approved": False, "unclear": False, "reason": f"not awaiting approval ({self.status})"}
        if source == "button":
            approved = True
        elif voice.is_approval(transcript):
            approved = True
        elif voice.NEGATE.search(transcript):
            approved = False
        else:
            return {"accepted": False, "approved": False, "unclear": True}
        with self._lock:
            self._decision = (approved, transcript, source)
            self.approval = {"approved": approved, "transcript": transcript, "source": source}
        self._event.set()
        return {"accepted": True, "approved": approved, "unclear": False}

    def wait_for_approval(self):
        """approval_provider for the server: block until approve() is called."""
        if not self._event.wait(APPROVAL_TIMEOUT):
            return False, "(timed out)", "timeout"
        approved, transcript, source = self._decision
        return approved, transcript, source

    # ---- the loop ----
    def execute(self, approval_provider=None, demo_alert=False, target=TARGET, on_approved=None, play_audio=True):
        provider = approval_provider or self.wait_for_approval
        gen = self.run_id

        def S(**kw):  # ignore updates from a run that was reset
            if self.run_id == gen:
                self._set(**kw)

        trace = Trace(listener=lambda ev: self._on_flow(ev) if self.run_id == gen else None)
        out = config.HERE / "out" / datetime.now().strftime("%Y%m%d-%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        S(status="running")
        try:
            alert, mode = clickhouse_src.detect(force_stub=demo_alert)
            if not alert:
                S(status="failed", error="No active exploitation found in ClickHouse.")
                return
            trace.flow(1, mode, f"{alert['attack_type']} on {alert['path']}: {alert['sources']} sources, "
                                f"{alert['active_exploit_rps']:.0f} exploit req/s ({alert['malicious_pct']:.0%} of traffic), "
                                f"confidence {alert['confidence']:.0%}")

            query = llm.formulate_query(alert)
            trace.flow(2, "live" if llm.live() else "stub", f'memory query: "{query}"')
            intel, mode = memory.search(query, k=3)
            trace.flow(3, mode, "\n".join(f"{d['score']:.2f}  {d['title']}  ({d['kind']})" for d in intel))

            work = Path(tempfile.mkdtemp(prefix="aegis_"))  # outside git so Semgrep can't skip files via .gitignore
            (work / "before").mkdir()
            (work / "after").mkdir()
            shutil.copy(target, work / "before" / Path(target).name)
            findings = _findings(scanner.scan(work / "before"))
            if not findings:
                S(status="failed", error="Semgrep found nothing in the target; refusing to continue.")
                return
            trace.flow(4, "live", f"{len(findings)} findings in {Path(target).name}\n" + "\n".join(
                f"line {f.line}: {f.category} ({f.rule_id})" for f in findings))

            original = Path(target).read_text()
            patch = llm.propose_patch(original, Path(target).name, findings, intel)
            (work / "after" / Path(target).name).write_text(patch["patched_code"])
            remaining = _findings(scanner.scan(work / "after"))
            verification = ("Semgrep re-scan of the patch is clean." if not remaining
                            else f"Semgrep still finds {len(remaining)} issue(s) in the patch.")
            diff = "".join(difflib.unified_diff(original.splitlines(True), patch["patched_code"].splitlines(True),
                                                "before/" + Path(target).name, "after/" + Path(target).name))
            (out / "fix.patch").write_text(diff)
            S(diff=diff, verification=verification)
            trace.flow(5, "live" if llm.live() else "stub",
                       f"{patch['explanation']}\nverification: {verification}\ndiff saved to proto_aegis/out/{out.name}/fix.patch")
            if remaining:
                S(status="failed", error="Patch did not verify; not asking for approval.")
                return

            text = llm.briefing(alert, findings, patch, verification)
            vmode, audio = voice.speak(text, play=play_audio)
            S(briefing=text, audio_path=audio)
            trace.flow(6, vmode, text)

            S(status="awaiting_approval")
            approved, transcript, source = provider()
            trace.flow(7, "live" if source == "voice" else "stub",
                       f'“{transcript}” via {source} -> {"APPROVED" if approved else "NOT approved"}')
            if not approved:
                S(status="rejected", closing="Not approved. Nothing was deployed.")
                trace.save(out / "trace.json")
                return

            S(status="deploying", approval={"approved": True, "transcript": transcript, "source": source})
            deployed = out / "deployed"
            deployed.mkdir(exist_ok=True)
            (deployed / Path(target).name).write_text(patch["patched_code"])
            S(deployed=deployed)
            if on_approved:
                on_approved(transcript, source)
            closing = llm.final_report(alert, patch, deployed)
            S(status="complete", closing=closing)
            trace.save(out / "trace.json")
        except Exception as e:  # show the failure on the dashboard instead of dying silently
            S(status="failed", error=f"{type(e).__name__}: {str(e)[:300]}")

    def start_async(self, **kw):
        """Start in a background thread. Returns False if a run is already in progress."""
        with self._lock:
            if self.status not in ("idle",):
                return False
            self.status = "running"
        threading.Thread(target=self.execute, kwargs=kw, daemon=True).start()
        return True
