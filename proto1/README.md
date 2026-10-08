# proto1: log summary, spoken aloud

Reads log files, asks Claude for a short spoken-style briefing, and reads it out with ElevenLabs text-to-speech.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY and ELEVENLABS_API_KEY
```

## Usage
```bash
python log_speaker.py sample_logs             # summarize and speak
python log_speaker.py "/var/log/*.log" --no-speak
python log_speaker.py app.log --out summary.mp3
```
Large logs are condensed (all warnings/errors plus the last lines) before summarizing.
