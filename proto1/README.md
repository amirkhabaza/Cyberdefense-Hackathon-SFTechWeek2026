# proto1: spoken log summaries

Reads a bunch of log files, asks Claude for a short briefing written to be listened to, and reads it aloud with ElevenLabs text-to-speech.

```
log files -> condense (errors/warnings + tail) -> Claude summary -> ElevenLabs TTS -> mp3 + playback
```

## Contents
| Path | What it is |
|---|---|
| `log_speaker.py` | The CLI: load logs, summarize, speak |
| `sample_logs/` | Small synthetic logs (a repeated `KeyError`, a Redis outage) for a quick demo |
| `error_logs/`, `marco_logs/` | Real-world example logs to try it on |
| `.env.example` | Keys and optional settings |

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env    # add ANTHROPIC_API_KEY and ELEVENLABS_API_KEY
```

## Usage
```bash
python log_speaker.py sample_logs                  # summarize and speak
python log_speaker.py "/var/log/*.log" --no-speak  # print the summary only
python log_speaker.py app.log --out summary.mp3    # keep the audio
python log_speaker.py logs/ --voice <voice_id> --model <anthropic_model>
```
Paths can be files, folders (searched recursively) or globs.

## Configuration (`.env`)
| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Summarization |
| `ELEVENLABS_API_KEY` | required (not with `--no-speak`) | Speech |
| `ELEVENLABS_VOICE_ID` | `JBFqnCBsd6RMkjVDRZzb` | Voice |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | Summarizer model |

## Notes
- Logs over about 12k characters per file are condensed to all warning/error lines plus the last 40 lines.
- Playback uses `afplay` on macOS and `mpg123` on Linux; elsewhere the mp3 path is printed.
- The log text is sent to Anthropic, so check logs for secrets before summarizing them.
- A `401` from Anthropic means the API key is invalid or revoked.
