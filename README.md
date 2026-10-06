# STT Live Meetings

Live transcription of in-room meetings. A browser captures the room microphone,
streams the audio to a Python server, and the transcript appears on screen as
people speak. Speech recognition runs **locally** with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper): no audio leaves
the machine and no internet connection is needed once the model is downloaded.

- French and English, detected automatically per segment (or forced)
- Live partial text while someone speaks, final text after each pause
- Speaker labels ("Intervenant 1, 2...") from a single microphone; click a label to rename that person everywhere
- Meeting title editable at any time, even while recording
- Sessions saved automatically, with a history sidebar
- Export to Word (.docx), TXT, SRT subtitles and Markdown, with speaker names

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
                                              │  SpeakerTracker: voice embedding per segment
                                              │   ├ live: join closest speaker or create one
                                              │   └ on stop: re-cluster with hindsight
                                              └  SessionStore -> data/sessions/*.json
```

| File | Role |
| --- | --- |
| `app/main.py` | FastAPI app, WebSocket protocol, REST API for sessions and exports |
| `app/streaming.py` | Turns the audio stream into partial and final segments |
| `app/transcriber.py` | faster-whisper wrapper, language detection, hallucination filter |
| `app/speakers.py` | Speaker labels: voice embeddings (sherpa-onnx) and clustering |
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

Download the speaker model (26 MB) for speaker labels:

```bash
mkdir -p models/speaker
curl -L -o models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx
```

Without it the app still works, just without speaker labels.

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
| `STT_DIARIZATION` | `true` | Speaker labels on / off |
| `STT_SPEAKER_THRESHOLD` | `0.5` | Voice similarity for a segment to join a known speaker. Lower if one person gets several labels, raise if two people share one |
| `STT_SPEAKER_MERGE_THRESHOLD` | `0.7` | Similarity for two speakers to be merged when the meeting ends |
| `STT_DATA_DIR` | `data` | Where sessions are stored |

Rough guide on CPU: `small` + `int8` keeps up with live speech on a recent
4-8 core machine. If the transcript falls behind, use `base` or raise `STT_STEP`.

## API

| Method | Path | |
| --- | --- | --- |
| `GET` | `/api/health` | Engine status (`loading`, `ready`, `error`) |
| `GET` | `/api/sessions` | List meetings |
| `GET` | `/api/sessions/{id}` | Meeting with all segments |
| `PATCH` | `/api/sessions/{id}` | Rename: `{"title": "...", "speakers": {"S1": "Mme la Directrice"}}` (works during recording) |
| `GET` | `/api/sessions/{id}/export?format=docx\|txt\|srt\|md` | Download transcript |
| `DELETE` | `/api/sessions/{id}` | Delete a meeting |
| `WS` | `/ws/transcribe` | Live transcription, protocol documented in `app/main.py` |

## Tests

```bash
pytest
```

The tests use a fake transcriber and an energy-based VAD, so they run without
downloading a model.

## Speaker labels: how well it works

Each final segment is turned into a 256-number voice fingerprint. Live, a
segment joins the most similar known speaker, or starts a new one. When the
meeting stops, all segments are re-clustered with hindsight and the labels
are corrected (ids stay stable, so names given during the meeting are kept).

Measured on real recordings (two different voices, 20-23 segments): 98-100% of
speech time attributed correctly, both live and after the final pass. These were
clean recordings; a single room microphone with distant speakers will do worse.
Known weak spots: segments under 1 s ("oui", "d'accord") inherit the previous
speaker, overlapping speech goes to one person, and a reply without any pause
stays in the previous speaker's segment.

## Roadmap

- Split a segment when the voice changes without a pause
- Meeting summary and action items generated from the transcript
- Several microphones / room audio interface input
- Authentication and per-user meeting history
