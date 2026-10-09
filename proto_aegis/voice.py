"""#6 Voice out (ElevenLabs TTS) and #7 Approve in (typed, or spoken via ElevenLabs speech-to-text)."""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

import config

APPROVE = re.compile(r"\b(approve[ds]?|go ahead|do it|proceed|confirm(ed)?|yes)\b", re.I)
NEGATE = re.compile(r"\b(no|not|don'?t|do not|stop|cancel|wait|reject)\b", re.I)


def is_approval(text):
    return bool(APPROVE.search(text)) and not NEGATE.search(text)


def speak(text):
    """Returns mode: 'live' if spoken through ElevenLabs, 'stub' if printed only."""
    if not config.have("ELEVENLABS_API_KEY"):
        return "stub"
    try:
        return _tts(text)
    except requests.HTTPError as e:
        print(f"     (ElevenLabs TTS failed: {e.response.status_code} {e.response.text[:140]}; continuing without audio)")
        return "stub"


def _tts(text):
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                      headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"},
                      json={"text": text, "model_id": "eleven_multilingual_v2"}, timeout=120)
    r.raise_for_status()
    out = Path(tempfile.gettempdir()) / "aegis_briefing.mp3"
    out.write_bytes(r.content)
    if sys.platform == "darwin":
        subprocess.run(["afplay", str(out)], check=False)
    else:
        print(f"     (audio saved to {out})")
    return "live"


def _listen(seconds=6):
    """Record the microphone and transcribe with ElevenLabs Scribe. Needs: pip install sounddevice."""
    import sounddevice as sd
    import wave
    rate = 16000
    print(f"     Listening for {seconds}s… say 'approve' or 'no'.")
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16")
    sd.wait()
    wav = Path(tempfile.gettempdir()) / "aegis_reply.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(audio.tobytes())
    with wav.open("rb") as f:
        r = requests.post("https://api.elevenlabs.io/v1/speech-to-text",
                          headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                          data={"model_id": "scribe_v1"}, files={"file": ("reply.wav", f, "audio/wav")}, timeout=120)
    r.raise_for_status()
    return r.json().get("text", "")


def get_approval(use_voice=False, auto=False):
    """Returns (approved, transcript, mode)."""
    if auto:
        return True, "approve (--auto-approve)", "stub"
    if use_voice and config.have("ELEVENLABS_API_KEY"):
        text = _listen()
        print(f"     Heard: “{text}”")
        return is_approval(text), text, "live"
    text = input("     Type your reply (e.g. 'approve'): ").strip()
    return is_approval(text), text, "stub"
