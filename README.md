# STT Live Meetings

Live transcription of in-room meetings. A browser captures the room microphone,
streams the audio to a Python server, and the transcript appears on screen as
people speak. Speech recognition runs **locally** with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper): no audio leaves
the machine and no internet connection is needed once the model is downloaded.

- French and English, detected automatically per segment (or forced)
- Live partial text while someone speaks, final text after each pause
- Sessions saved automatically, with a history sidebar
- Export to Word (.docx), TXT, SRT subtitles and Markdown

## How it works

```
 Browser                                   Server (FastAPI)
 ───────                                   ────────────────
 Microphone                                 /ws/transcribe
   │ AudioWorklet: resample to 16 kHz,        │
   │ 16-bit PCM, 100 ms chunks                │  StreamingSegmenter
   └──────────── WebSocket (binary) ────────► │   ├ Silero VAD: find speech / pauses
                                              │   ├ pause >= 0.6 s  -> final segment
   transcript  ◄──── partial / final JSON ─── │   ├ segment > 15 s  -> cut at last pause
                                              │   └ otherwise       -> partial text
                                              │  WhisperTranscriber (faster-whisper)
                                              └  SessionStore -> data/sessions/*.json
```

| File | Role |
| --- | --- |
| `app/main.py` | FastAPI app, WebSocket protocol, REST API for sessions and exports |
| `app/streaming.py` | Turns the audio stream into partial and final segments |
| `app/transcriber.py` | faster-whisper wrapper, language detection, hallucination filter |
| `app/storage.py` | One JSON file per meeting |
| `app/exporters.py` | TXT / SRT / Markdown / DOCX |
| `app/static/` | Web UI (plain HTML/CSS/JS, no build step) |

## Getting started

Requires Python 3.10+ and about 2 GB of free disk space (dependencies plus the model).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # optional, to change the model, etc.

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. On first start the Whisper model is downloaded from
Hugging Face (about 500 MB for `small`); the status indicator turns green when it is ready.

> The browser only allows microphone access on `localhost` or over **HTTPS**.
> To use a laptop in the meeting room against a server elsewhere on the network,
> put the server behind HTTPS (for example a reverse proxy, or
> `uvicorn ... --ssl-keyfile key.pem --ssl-certfile cert.pem`).

## Configuration

Set in `.env` or as environment variables (prefix `STT_`):

| Variable | Default | Notes |
| --- | --- | --- |
| `STT_MODEL_SIZE` | `small` | `base` is faster, `medium` / `large-v3-turbo` more accurate (GPU recommended). Also accepts a local folder, e.g. `models/faster-whisper-small` downloaded from `Systran/faster-whisper-small` on Hugging Face |
| `STT_DEVICE` | `cpu` | `cuda` for an NVIDIA GPU |
| `STT_COMPUTE_TYPE` | `int8` | `float16` on GPU |
| `STT_LANGUAGES` | `fr,en` | Languages allowed for automatic detection |
| `STT_MIN_SILENCE` | `0.6` | Pause (s) that closes a segment |
| `STT_MAX_SEGMENT` | `15` | Longest segment (s) before a forced cut |
| `STT_STEP` | `1.0` | How often (s) partial text is refreshed; raise it if the CPU cannot keep up |
| `STT_DATA_DIR` | `data` | Where sessions are stored |

Rough guide on CPU: `small` + `int8` keeps up with live speech on a recent
4-8 core machine. If the transcript falls behind, use `base` or raise `STT_STEP`.

## API

| Method | Path | |
| --- | --- | --- |
| `GET` | `/api/health` | Engine status (`loading`, `ready`, `error`) |
| `GET` | `/api/sessions` | List meetings |
| `GET` | `/api/sessions/{id}` | Meeting with all segments |
| `GET` | `/api/sessions/{id}/export?format=docx\|txt\|srt\|md` | Download transcript |
| `DELETE` | `/api/sessions/{id}` | Delete a meeting |
| `WS` | `/ws/transcribe` | Live transcription, protocol documented in `app/main.py` |

## Tests

```bash
pytest
```

The tests use a fake transcriber and an energy-based VAD, so they run without
downloading a model.

## Roadmap

- Speaker labels (diarization)
- Meeting summary and action items generated from the transcript
- Several microphones / room audio interface input
- Authentication and per-user meeting history
