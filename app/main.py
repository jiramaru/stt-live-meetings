"""FastAPI app: live transcription over WebSocket plus a small session API.

WebSocket protocol (/ws/transcribe):
  client -> {"type": "start", "title": str, "language": "auto" | "fr" | "en"}
  client -> binary frames: 16 kHz mono PCM, signed 16-bit little endian
  client -> {"type": "stop"}
  server -> {"type": "started", "session": {...}}
  server -> {"type": "partial", "text": str}
  server -> {"type": "final", "segment": {...}}
  server -> {"type": "stopped", "session_id": str}
  server -> {"type": "error", "message": str}
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .exporters import EXPORTERS
from .storage import SessionStore
from .streaming import StreamingSegmenter, VadFn, silero_vad
from .transcriber import Transcriber, WhisperTranscriber

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class Engine:
    """Holds the model, loaded in the background so the server starts at once."""

    def __init__(self, load: Callable[[], tuple[Transcriber, VadFn]]):
        self._load = load
        self.transcriber: Transcriber | None = None
        self.vad: VadFn | None = None
        self.status = "loading"
        self.error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.transcriber, self.vad = self._load()
            self.status = "ready"
            log.info("Speech engine ready")
        except Exception as exc:  # surfaced through /api/health
            log.exception("Failed to load speech engine")
            self.status, self.error = "error", str(exc)


def default_loader(settings: Settings) -> Callable[[], tuple[Transcriber, VadFn]]:
    return lambda: (WhisperTranscriber(settings), silero_vad(settings.vad_threshold))


def create_app(
    settings: Settings | None = None,
    loader: Callable[[], tuple[Transcriber, VadFn]] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = Engine(loader or default_loader(settings))
    store = SessionStore(settings.data_dir)

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
        }

    @app.get("/api/sessions")
    def list_sessions():
        return store.list()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        try:
            return store.get(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")

    @app.delete("/api/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str):
        try:
            store.delete(session_id)
        except KeyError:
            raise HTTPException(404, "Session introuvable")

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
            await run_session(ws, engine, store, settings)
        except (WebSocketDisconnect, RuntimeError):
            pass  # client left; the session was saved by run_session

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


async def run_session(ws: WebSocket, engine: Engine, store: SessionStore, settings: Settings):
    start = await ws.receive_json()
    if start.get("type") != "start":
        await ws.send_json({"type": "error", "message": "Message 'start' attendu"})
        return
    language = start.get("language")
    language = language if language in settings.language_list else None
    session = store.create(start.get("title", ""), language)
    segmenter = StreamingSegmenter(engine.transcriber, engine.vad, settings, language=language)
    await ws.send_json({"type": "started", "session": session})

    # Audio is queued here by the receiver and handed to the segmenter by the
    # worker, so the segmenter is never touched while a step runs in a thread.
    inbox: list[np.ndarray] = []
    wake = asyncio.Event()
    stopped = asyncio.Event()

    async def receive():
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes"):
                    pcm = np.frombuffer(message["bytes"], dtype="<i2")
                    inbox.append(pcm.astype(np.float32) / 32768.0)
                    wake.set()
                elif message.get("text") and '"stop"' in message["text"]:
                    break
        finally:
            stopped.set()
            wake.set()

    async def emit(events):
        for event in events:
            if event.type == "final":
                session["segments"].append(event.segment.to_dict())
                store.save(session)
                await ws.send_json({"type": "final", "segment": event.segment.to_dict()})
            else:
                await ws.send_json({"type": "partial", "text": event.text})

    receiver = asyncio.create_task(receive())
    try:
        while not stopped.is_set():
            await wake.wait()
            wake.clear()
            while inbox:
                segmenter.add_audio(inbox.pop(0))
            if segmenter.ready() and not stopped.is_set():
                await emit(await asyncio.to_thread(segmenter.step))
        while inbox:
            segmenter.add_audio(inbox.pop(0))
        await emit(await asyncio.to_thread(segmenter.flush))
    finally:
        receiver.cancel()
        session["ended_at"] = datetime.now(timezone.utc).isoformat()
        store.save(session)

    await ws.send_json({"type": "stopped", "session_id": session["id"]})
    await ws.close()


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
