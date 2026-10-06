import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .fakes import FakeTranscriber, energy_vad, silence, tone


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, step=0.25)
    app = create_app(settings, loader=lambda: (FakeTranscriber(), energy_vad))
    with TestClient(app) as client:
        for _ in range(50):
            if client.get("/api/health").json()["status"] == "ready":
                break
            time.sleep(0.02)
        yield client


def pcm16(audio: np.ndarray) -> bytes:
    return (audio * 32767).astype("<i2").tobytes()


def record(client, audio, title="Cotech", language="auto"):
    messages = []
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "title": title, "language": language})
        started = ws.receive_json()
        assert started["type"] == "started"
        chunk = 1600
        for i in range(0, len(audio), chunk):
            ws.send_bytes(pcm16(audio[i : i + chunk]))
        ws.send_json({"type": "stop"})
        while True:
            msg = ws.receive_json()
            messages.append(msg)
            if msg["type"] == "stopped":
                break
    return started["session"]["id"], messages


def test_live_session_is_transcribed_saved_and_exported(client):
    audio = np.concatenate([tone(1.5), silence(1.0), tone(1.0)])
    session_id, messages = record(client, audio)

    finals = [m["segment"]["text"] for m in messages if m["type"] == "final"]
    assert finals == ["speech 1.5s", "speech 1.0s"]

    session = client.get(f"/api/sessions/{session_id}").json()
    assert session["title"] == "Cotech"
    assert session["ended_at"]
    assert [s["text"] for s in session["segments"]] == finals

    [listed] = client.get("/api/sessions").json()
    assert listed["segment_count"] == 2

    txt = client.get(f"/api/sessions/{session_id}/export?format=txt")
    assert txt.status_code == 200
    assert "speech 1.5s" in txt.text
    assert "attachment" in txt.headers["content-disposition"]

    docx = client.get(f"/api/sessions/{session_id}/export?format=docx")
    assert docx.content[:2] == b"PK"  # zip container


def test_unknown_export_format_and_session(client):
    session_id, _ = record(client, silence(0.5))
    assert client.get(f"/api/sessions/{session_id}/export?format=pdf").status_code == 400
    assert client.get("/api/sessions/" + "0" * 32).status_code == 404
    assert client.get("/api/sessions/../etc").status_code == 404


def test_delete_session(client):
    session_id, _ = record(client, silence(0.5))
    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get("/api/sessions").json() == []


def test_socket_refused_while_model_loads(tmp_path):
    def slow_loader():
        time.sleep(5)
        return FakeTranscriber(), energy_vad

    app = create_app(Settings(data_dir=tmp_path), loader=slow_loader)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/transcribe") as ws:
            msg = ws.receive_json()
    assert msg["type"] == "error"
