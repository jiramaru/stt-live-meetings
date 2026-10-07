"""FastAPI app: live transcription over WebSocket plus a small session API.

WebSocket protocol (/ws/transcribe):
  client -> {"type": "start", "title": str, "language": "auto" | "fr" | "en"}
  client -> binary frames: 16 kHz mono PCM, signed 16-bit little endian
  client -> {"type": "pause"} / {"type": "resume"}   (same meeting, same speakers)
  client -> {"type": "stop"}
  server -> {"type": "started", "session": {...}}
  server -> {"type": "partial", "text": str}
  server -> {"type": "final", "segment": {...}}
  server -> {"type": "paused", "session": {...}}    (speaker labels refined)
  server -> {"type": "resumed"}
  server -> {"type": "stopped", "session": {...}}   (speaker labels refined)
  server -> {"type": "error", "message": str}
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import wave
from urllib.parse import unquote
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import SAMPLE_RATE, Settings, get_settings
from .exporters import EXPORTERS
from .speakers import Embedder, SherpaEmbedder, SpeakerTracker, enrol
from .storage import ProfileStore, SessionStore
from .streaming import StreamingSegmenter, VadFn, silero_vad
from .transcriber import Transcriber, WhisperTranscriber

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
SPEAKER_ID_RE = re.compile(r"^(S\d{1,4}|P[0-9a-f]{8})$")  # unknown voice | voice profile
ENROL_MAX_BYTES = 60 * SAMPLE_RATE * 2  # one minute of 16-bit audio


@dataclass
class Components:
    transcriber: Transcriber
    vad: VadFn
    embedder: Embedder | None = None  # None = no speaker labels


class Engine:
    """Holds the models, loaded in the background so the server starts at once."""

    def __init__(self, load: Callable[[], Components]):
        self._load = load
        self.parts: Components | None = None
        self.status = "loading"
        self.error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.parts = self._load()
            self.status = "ready"
            log.info("Speech engine ready")
        except Exception as exc:  # surfaced through /api/health
            log.exception("Failed to load speech engine")
            self.status, self.error = "error", str(exc)


def default_loader(settings: Settings) -> Callable[[], Components]:
    def load() -> Components:
        embedder = None
        if settings.diarization:
            if settings.speaker_model.exists():
                embedder = SherpaEmbedder(str(settings.speaker_model))
            else:
                log.warning(
                    "Speaker model %s not found: speaker labels disabled", settings.speaker_model
                )
        return Components(
            WhisperTranscriber(settings), silero_vad(settings.vad_threshold), embedder
        )

    return load


class ProfileUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


class SegmentUpdate(BaseModel):
    text: str = Field(..., max_length=5000)


class SessionUpdate(BaseModel):
    title: str | None = Field(None, max_length=120)
    speakers: dict[str, str] | None = None  # speaker id -> display name ("" = default)


def create_app(
    settings: Settings | None = None,
    loader: Callable[[], Components] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = Engine(loader or default_loader(settings))
    store = SessionStore(settings.data_dir)
    profiles = ProfileStore(settings.data_dir)
    # Sessions being recorded, by id. Edits go through these dicts so the
    # recorder's next save does not overwrite them. Every access happens on
    # the event loop (async handlers), so no lock is needed.
    live: dict[str, dict] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        engine.start()
        yield

    app = FastAPI(title="STT Live Meetings", lifespan=lifespan)
    app.state.engine = engine
    app.state.store = store

    @app.get("/api/health")
    def health():
        return {
            "status": engine.status,
            "error": engine.error,
            "model": settings.model_size,
            "device": settings.device,
            "languages": settings.language_list,
            "diarization": bool(engine.parts and engine.parts.embedder),
        }

    # -------------------------------------------------------------- voice profiles

    @app.get("/api/profiles")
    def list_profiles():
        return profiles.list()

    @app.post("/api/profiles", status_code=201)
    async def create_profile(request: Request):
        """Body: 16 kHz mono 16-bit PCM of the person reading.
        Headers: X-Profile-Name (URL-encoded), X-Consent: yes."""
        if request.headers.get("x-consent") != "yes":
            raise HTTPException(400, "Le consentement de la personne est requis.")
        name = unquote(request.headers.get("x-profile-name", "")).strip()
        if not name:
            raise HTTPException(400, "Le nom est requis.")
        if not (engine.parts and engine.parts.embedder):
            raise HTTPException(503, "Le modèle de reconnaissance des voix n'est pas chargé.")
        body = await request.body()
        if len(body) > ENROL_MAX_BYTES:
            raise HTTPException(413, "Enregistrement trop long (une minute au plus).")
        audio = np.frombuffer(body[: len(body) // 2 * 2], dtype="<i2").astype(np.float32) / 32768
        try:
            print_ = await asyncio.to_thread(
                enrol, engine.parts.embedder, engine.parts.vad, audio
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return profiles.create(name, print_)

    @app.patch("/api/profiles/{profile_id}")
    def rename_profile(profile_id: str, update: ProfileUpdate):
        try:
            return profiles.rename(profile_id, update.name)
        except KeyError:
            raise HTTPException(404, "Profil introuvable")

    @app.delete("/api/profiles/{profile_id}", status_code=204)
    def delete_profile(profile_id: str):
        try:
            profiles.delete(profile_id)
        except KeyError:
            raise HTTPException(404, "Profil introuvable")

    # -------------------------------------------------------------- sessions

    @app.get("/api/sessions")
    def list_sessions():
        return store.list()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        try:
            return store.get(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")

    @app.patch("/api/sessions/{session_id}")
    async def update_session(session_id: str, update: SessionUpdate):
        try:
            session = live.get(session_id) or store.get(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")
        if update.title is not None:
            session["title"] = update.title.strip() or session["title"]
        for speaker, name in (update.speakers or {}).items():
            if not SPEAKER_ID_RE.match(speaker):
                raise HTTPException(400, f"Intervenant inconnu : {speaker}")
            name = name.strip()[:80]
            if name:
                session["speakers"][speaker] = name
            else:
                session["speakers"].pop(speaker, None)
        store.save(session)
        return session

    @app.patch("/api/sessions/{session_id}/segments/{segment_id}")
    async def update_segment(session_id: str, segment_id: int, update: SegmentUpdate):
        """Correct a segment's text by hand (also while recording)."""
        try:
            session = live.get(session_id) or store.get(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")
        segment = next((s for s in session["segments"] if s["id"] == segment_id), None)
        if segment is None:
            raise HTTPException(404, "Segment introuvable")
        text = " ".join(update.text.split())
        if not text:
            raise HTTPException(400, "Le texte ne peut pas être vide.")
        if text != segment["text"]:
            segment.setdefault("original", segment["text"])  # what the recognizer heard
            segment["text"] = text
            segment["edited"] = True
            store.save(session)
        return segment

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str):
        try:
            store.delete(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")

    @app.get("/api/sessions/{session_id}/audio")
    def session_audio(session_id: str):
        try:
            path = store.audio_path(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")
        if not path.exists():
            raise HTTPException(404, "Pas d'audio pour cette réunion")
        return FileResponse(path, media_type="audio/wav", filename=f"reunion-{session_id[:8]}.wav")

    @app.get("/api/sessions/{session_id}/export")
    def export_session(session_id: str, format: str = "txt"):
        if format not in EXPORTERS:
            raise HTTPException(400, f"Format inconnu : {format}")
        try:
            session = store.get(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")
        render, media_type, ext = EXPORTERS[format]
        filename = f"transcription-{session['started_at'][:10]}-{session_id[:8]}.{ext}"
        return Response(
            render(session),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.websocket("/ws/transcribe")
    async def transcribe(ws: WebSocket):
        await ws.accept()
        if engine.status != "ready":
            message = "Le modèle est en cours de chargement, réessayez dans un instant."
            if engine.status == "error":
                message = f"Le moteur de transcription n'a pas pu démarrer : {engine.error}"
            await ws.send_json({"type": "error", "message": message})
            await ws.close()
            return
        try:
            await run_session(ws, engine.parts, store, profiles, live, settings)
        except (WebSocketDisconnect, RuntimeError):
            pass  # client left; the session was saved by run_session

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


async def run_session(
    ws: WebSocket,
    parts: Components,
    store: SessionStore,
    profiles: ProfileStore,
    live: dict,
    settings: Settings,
):
    start = await ws.receive_json()
    if start.get("type") != "start":
        await ws.send_json({"type": "error", "message": "Message 'start' attendu"})
        return
    language = start.get("language")
    language = language if language in settings.language_list else None
    session = store.create(start.get("title", ""), language)
    live[session["id"]] = session
    recording = None
    if settings.save_audio:
        recording = wave.open(str(store.audio_path(session["id"])), "wb")
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(SAMPLE_RATE)
        session["audio"] = True
    tracker = None
    if parts.embedder:
        tracker = SpeakerTracker(
            parts.embedder,
            threshold=settings.speaker_threshold,
            merge_threshold=settings.speaker_merge_threshold,
            profiles=profiles.voices(),
        )
    names = {p.id: p.name for p in (tracker.profiles if tracker else [])}
    segmenter = StreamingSegmenter(
        parts.transcriber, parts.vad, settings, language=language, speakers=tracker
    )
    await ws.send_json({"type": "started", "session": session})

    # Audio and pause/resume commands are queued here by the receiver and
    # handled in order by the worker, so the segmenter is never touched while
    # a step runs in a thread.
    inbox: list[np.ndarray | str] = []
    wake = asyncio.Event()
    stopped = asyncio.Event()
    paused = False

    async def receive():
        nonlocal paused
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    if paused:
                        continue  # in flight when the pause was sent
                    if recording:
                        recording.writeframes(message["bytes"])
                    pcm = np.frombuffer(message["bytes"], dtype="<i2")
                    inbox.append(pcm.astype(np.float32) / 32768.0)
                elif message.get("text"):
                    command = json.loads(message["text"]).get("type")
                    if command == "stop":
                        break
                    if command in ("pause", "resume"):
                        paused = command == "pause"
                        inbox.append(command)
                wake.set()
        finally:
            stopped.set()
            wake.set()

    def refine_speakers():
        if tracker:
            labels = tracker.refine()
            for segment in session["segments"]:
                segment["speaker"] = labels.get(segment["id"], segment["speaker"])

    async def emit(events):
        for event in events:
            if event.type == "final":
                speaker = event.segment.speaker
                # A recognised profile: the transcript shows the person's name
                # (unless renamed in this meeting).
                if speaker in names and speaker not in session["speakers"]:
                    session["speakers"][speaker] = names[speaker]
                session["segments"].append(event.segment.to_dict())
                store.save(session)
                await ws.send_json({
                    "type": "final",
                    "segment": event.segment.to_dict(),
                    "speakers": session["speakers"],
                })
            else:
                await ws.send_json({"type": "partial", "text": event.text})

    async def drain_inbox():
        while inbox:
            item = inbox.pop(0)
            if isinstance(item, np.ndarray):
                segmenter.add_audio(item)
            elif item == "pause":
                # Finish the sentence in progress, then relabel with hindsight.
                await emit(await asyncio.to_thread(segmenter.flush))
                refine_speakers()
                store.save(session)
                await ws.send_json({"type": "paused", "session": session})
            elif item == "resume":
                await ws.send_json({"type": "resumed"})

    receiver = asyncio.create_task(receive())
    try:
        while not stopped.is_set():
            await wake.wait()
            wake.clear()
            await drain_inbox()
            if segmenter.ready() and not stopped.is_set():
                await emit(await asyncio.to_thread(segmenter.step))
        inbox[:] = [item for item in inbox if isinstance(item, np.ndarray)]
        await drain_inbox()
        await emit(await asyncio.to_thread(segmenter.flush))
    finally:
        receiver.cancel()
        if recording:
            recording.close()
        refine_speakers()
        session["ended_at"] = datetime.now(timezone.utc).isoformat()
        store.save(session)
        live.pop(session["id"], None)

    await ws.send_json({"type": "stopped", "session": session})
    await ws.close()


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
