import time
from urllib.parse import quote

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import Components, create_app

from .fakes import FakeEmbedder, FakeTranscriber, energy_vad, silence, tone


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path, step=0.25)
    app = create_app(
        settings, loader=lambda: Components(FakeTranscriber(), energy_vad, FakeEmbedder())
    )
    with TestClient(app) as client:
        for _ in range(50):
            if client.get("/api/health").json()["status"] == "ready":
                break
            time.sleep(0.02)
        yield client


def pcm16(audio: np.ndarray) -> bytes:
    return (audio * 32767).astype("<i2").tobytes()


def record(client, audio, title="Cotech", language="auto", during=None):
    messages = []
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "title": title, "language": language})
        started = ws.receive_json()
        assert started["type"] == "started"
        if during:
            during(started["session"]["id"])
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
        return Components(FakeTranscriber(), energy_vad)

    app = create_app(Settings(data_dir=tmp_path), loader=slow_loader)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/transcribe") as ws:
            msg = ws.receive_json()
    assert msg["type"] == "error"


def test_speakers_are_labelled_and_can_be_renamed(client):
    a, b = 220, 660
    audio = np.concatenate(
        [tone(1.5, a), silence(1.0), tone(1.5, b), silence(1.0), tone(1.2, a), silence(1.0)]
    )
    session_id, messages = record(client, audio)

    live = [m["segment"]["speaker"] for m in messages if m["type"] == "final"]
    assert live == ["S1", "S2", "S1"]
    stopped = messages[-1]["session"]
    assert [s["speaker"] for s in stopped["segments"]] == ["S1", "S2", "S1"]

    renamed = client.patch(
        f"/api/sessions/{session_id}", json={"speakers": {"S1": "M. le Directeur"}}
    ).json()
    assert renamed["speakers"] == {"S1": "M. le Directeur"}

    txt = client.get(f"/api/sessions/{session_id}/export?format=txt").text
    assert "M. le Directeur :" in txt and "Intervenant 2 :" in txt

    # An empty name restores the default label.
    client.patch(f"/api/sessions/{session_id}", json={"speakers": {"S1": ""}})
    assert client.get(f"/api/sessions/{session_id}").json()["speakers"] == {}


def test_title_and_names_can_change_during_recording(client):
    def rename(session_id):
        r = client.patch(
            f"/api/sessions/{session_id}",
            json={"title": "Comité de pilotage", "speakers": {"S1": "Mme la Présidente"}},
        )
        assert r.status_code == 200

    session_id, _ = record(client, np.concatenate([tone(1.5), silence(1.0)]), during=rename)

    # The recorder kept saving segments afterwards without undoing the edits.
    session = client.get(f"/api/sessions/{session_id}").json()
    assert session["title"] == "Comité de pilotage"
    assert session["speakers"] == {"S1": "Mme la Présidente"}
    assert len(session["segments"]) == 1


def test_rename_rejects_unknown_speaker_ids(client):
    session_id, _ = record(client, silence(0.5))
    r = client.patch(f"/api/sessions/{session_id}", json={"speakers": {"../x": "Bob"}})
    assert r.status_code == 400


def test_audio_is_saved_and_deleted_with_the_session(client):
    audio = np.concatenate([tone(1.5), silence(1.2)])
    session_id, _ = record(client, audio)

    r = client.get(f"/api/sessions/{session_id}/audio")
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    assert len(r.content) == 44 + len(pcm16(audio))  # header + every sample sent

    client.delete(f"/api/sessions/{session_id}")
    assert client.get(f"/api/sessions/{session_id}/audio").status_code == 404


def test_pause_and_resume_stay_in_the_same_meeting(client):
    a, b = 220, 660
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "title": "Copil", "language": "auto"})
        session_id = ws.receive_json()["session"]["id"]

        ws.send_bytes(pcm16(tone(1.5, a)))  # still speaking when paused
        ws.send_json({"type": "pause"})
        msg = ws.receive_json()
        while msg["type"] != "paused":
            msg = ws.receive_json()
        # The open sentence was finalized at the pause.
        assert [s["text"] for s in msg["session"]["segments"]] == ["speech 1.5s"]

        ws.send_json({"type": "resume"})
        assert ws.receive_json()["type"] == "resumed"
        ws.send_bytes(pcm16(np.concatenate([tone(1.5, b), silence(1.2), tone(1.5, a)])))
        ws.send_json({"type": "stop"})
        msg = ws.receive_json()
        while msg["type"] != "stopped":
            msg = ws.receive_json()

    session = msg["session"]
    assert session["id"] == session_id
    assert [s["speaker"] for s in session["segments"]] == ["S1", "S2", "S1"]
    # Timestamps follow the recorded audio: no gap for the pause.
    assert session["segments"][1]["start"] < 2.0
    assert [s["id"] for s in session["segments"]] == [0, 1, 2]


def test_voice_profiles_are_enrolled_and_recognised_by_name(client):
    def enrol(name, freq):
        r = client.post(
            "/api/profiles",
            content=pcm16(tone(10.0, freq)),
            headers={"X-Profile-Name": quote(name), "X-Consent": "yes"},
        )
        assert r.status_code == 201, r.text
        return r.json()

    awa = enrol("Awa Mbemba", 220)
    assert "embedding" not in awa  # the voice print never leaves the server
    assert [p["name"] for p in client.get("/api/profiles").json()] == ["Awa Mbemba"]

    audio = np.concatenate([tone(1.5, 220), silence(1.2), tone(1.5, 660), silence(1.2)])
    _, messages = record(client, audio)
    finals = [m for m in messages if m["type"] == "final"]
    assert finals[0]["segment"]["speaker"] == awa["id"]
    assert finals[0]["speakers"] == {awa["id"]: "Awa Mbemba"}
    assert finals[1]["segment"]["speaker"] == "S1"  # unknown voice

    session = messages[-1]["session"]
    txt = client.get(f"/api/sessions/{session['id']}/export?format=txt").text
    assert "Awa Mbemba :" in txt and "Intervenant 1 :" in txt

    assert client.patch(f"/api/profiles/{awa['id']}", json={"name": "Awa M."}).json()["name"] == "Awa M."
    assert client.delete(f"/api/profiles/{awa['id']}").status_code == 204
    assert client.get("/api/profiles").json() == []


def test_enrolment_requires_consent_and_enough_speech(client):
    headers = {"X-Profile-Name": "Bob"}
    assert client.post("/api/profiles", content=pcm16(tone(10.0)), headers=headers).status_code == 400
    r = client.post(
        "/api/profiles", content=pcm16(tone(3.0)), headers={**headers, "X-Consent": "yes"}
    )
    assert r.status_code == 422 and "parole" in r.json()["detail"]


def test_segment_text_can_be_corrected(client):
    session_id, _ = record(client, np.concatenate([tone(1.5), silence(1.2)]))
    url = f"/api/sessions/{session_id}/segments/0"

    r = client.patch(url, json={"text": "  Bonjour   à tous. "})
    assert r.status_code == 200
    assert r.json()["text"] == "Bonjour à tous."
    assert r.json()["original"] == "speech 1.5s"  # the recognizer's version is kept

    client.patch(url, json={"text": "Bonjour à toutes et à tous."})
    segment = client.get(f"/api/sessions/{session_id}").json()["segments"][0]
    assert segment["text"] == "Bonjour à toutes et à tous."
    assert segment["original"] == "speech 1.5s" and segment["edited"]
    assert "Bonjour à toutes et à tous." in client.get(
        f"/api/sessions/{session_id}/export?format=txt").text

    assert client.patch(url, json={"text": "   "}).status_code == 400
    assert client.patch(f"/api/sessions/{session_id}/segments/99", json={"text": "x"}).status_code == 404


def test_segment_can_be_given_to_another_speaker(client):
    audio = np.concatenate([tone(1.5, 220), silence(1.2), tone(1.5, 660), silence(1.2)])
    session_id, _ = record(client, audio)
    url = f"/api/sessions/{session_id}/segments"

    # To another speaker of the meeting
    assert client.patch(f"{url}/1", json={"speaker": "S1"}).json()["speaker"] == "S1"
    # To a speaker not seen yet. S2 is free again, but it was given a name
    # in this meeting: a new speaker must not inherit it.
    client.patch(f"/api/sessions/{session_id}", json={"speakers": {"S2": "Paul"}})
    assert client.patch(f"{url}/0", json={"speaker": "new"}).json()["speaker"] == "S3"
    # To an enrolled voice: the meeting now knows that name
    profile = client.post(
        "/api/profiles", content=pcm16(tone(10.0, 440)),
        headers={"X-Profile-Name": "Awa", "X-Consent": "yes"},
    ).json()
    client.patch(f"{url}/1", json={"speaker": profile["id"]})
    session = client.get(f"/api/sessions/{session_id}").json()
    assert [s["speaker"] for s in session["segments"]] == ["S3", profile["id"]]
    assert session["speakers"][profile["id"]] == "Awa"
    assert all(s["speaker_manual"] for s in session["segments"])

    assert client.patch(f"{url}/0", json={"speaker": "../x"}).status_code == 400
    assert client.patch(f"{url}/0", json={"speaker": "P00000000"}).status_code == 404


def test_manual_speaker_survives_the_final_relabelling(client):
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "title": "Copil", "language": "auto"})
        session_id = ws.receive_json()["session"]["id"]
        ws.send_bytes(pcm16(np.concatenate([tone(1.5, 220), silence(1.2)])))
        ws.send_json({"type": "pause"})
        while ws.receive_json()["type"] != "paused":
            pass
        # Corrected while the meeting is paused (still live).
        r = client.patch(f"/api/sessions/{session_id}/segments/0", json={"speaker": "new"})
        assert r.json()["speaker"] == "S2"
        ws.send_json({"type": "resume"})
        ws.receive_json()
        # The same voice again: the tracker learned it is S2 now.
        ws.send_bytes(pcm16(np.concatenate([tone(1.5, 220), silence(1.2)])))
        ws.send_json({"type": "stop"})
        msg = ws.receive_json()
        while msg["type"] != "stopped":
            msg = ws.receive_json()
    assert [s["speaker"] for s in msg["session"]["segments"]] == ["S2", "S2"]


def enrol_voice(client, name, freq):
    return client.post(
        "/api/profiles", content=pcm16(tone(10.0, freq)),
        headers={"X-Profile-Name": quote(name), "X-Consent": "yes"},
    ).json()


def test_participants_limit_who_can_be_recognised(client):
    awa, paul = enrol_voice(client, "Awa", 220), enrol_voice(client, "Paul", 660)
    audio = np.concatenate([tone(1.5, 220), silence(1.2), tone(1.5, 660), silence(1.2)])

    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "title": "Copil", "participants": [awa["id"]]})
        started = ws.receive_json()["session"]
        assert started["participants"] == [awa["id"]]
        assert started["speakers"] == {awa["id"]: "Awa"}  # named before speaking
        ws.send_bytes(pcm16(audio))
        ws.send_json({"type": "stop"})
        msg = ws.receive_json()
        while msg["type"] != "stopped":
            msg = ws.receive_json()
    # Paul was not declared present: his voice is an unknown speaker.
    assert [s["speaker"] for s in msg["session"]["segments"]] == [awa["id"], "S1"]


def test_participant_joining_during_the_meeting(client):
    awa, paul = enrol_voice(client, "Awa", 220), enrol_voice(client, "Paul", 660)
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "title": "Copil", "participants": [awa["id"]]})
        session_id = ws.receive_json()["session"]["id"]
        r = client.patch(f"/api/sessions/{session_id}",
                         json={"participants": [awa["id"], paul["id"]]})
        assert r.json()["speakers"][paul["id"]] == "Paul"
        ws.send_bytes(pcm16(np.concatenate([tone(1.5, 660), silence(1.2)])))
        ws.send_json({"type": "stop"})
        msg = ws.receive_json()
        while msg["type"] != "stopped":
            msg = ws.receive_json()
    assert msg["session"]["segments"][0]["speaker"] == paul["id"]


def test_unknown_participant_is_refused(client):
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "participants": ["P00000000"]})
        assert ws.receive_json()["type"] == "error"
    session_id, _ = record(client, silence(0.5))
    r = client.patch(f"/api/sessions/{session_id}", json={"participants": ["P00000000"]})
    assert r.status_code == 400


def test_all_passages_of_a_speaker_go_to_a_profile(client):
    awa = enrol_voice(client, "Awa", 440)
    a, b = 220, 660
    audio = np.concatenate([
        tone(1.5, a), silence(1.2), tone(1.5, b), silence(1.2), tone(1.5, a), silence(1.2),
    ])
    session_id, _ = record(client, audio)
    url = f"/api/sessions/{session_id}/speakers"

    # "Intervenant 1" was Awa: both of S1's passages become hers.
    session = client.post(f"{url}/S1/reassign", json={"to": awa["id"]}).json()
    assert [s["speaker"] for s in session["segments"]] == [awa["id"], "S2", awa["id"]]
    assert session["speakers"][awa["id"]] == "Awa"

    # One person split in two: S2 merged into Awa as well.
    session = client.post(f"{url}/S2/reassign", json={"to": awa["id"]}).json()
    assert {s["speaker"] for s in session["segments"]} == {awa["id"]}
    assert all(s["speaker_manual"] for s in session["segments"])

    assert client.post(f"{url}/S9/reassign", json={"to": awa["id"]}).status_code == 404
    assert client.post(f"{url}/{awa['id']}/reassign", json={"to": "x"}).status_code == 400


def test_notes_before_during_and_after_the_meeting(client):
    with client.websocket_connect("/ws/transcribe") as ws:
        ws.send_json({"type": "start", "title": "Copil", "notes": "Ordre du jour : budget"})
        session = ws.receive_json()["session"]
        assert session["notes"] == "Ordre du jour : budget"
        # Typed while recording: the recorder's own saves must keep it.
        client.patch(f"/api/sessions/{session['id']}", json={"notes": "Budget validé"})
        ws.send_bytes(pcm16(np.concatenate([tone(1.5), silence(1.2)])))
        ws.send_json({"type": "stop"})
        while ws.receive_json()["type"] != "stopped":
            pass
    saved = client.get(f"/api/sessions/{session['id']}").json()
    assert saved["notes"] == "Budget validé"
    assert "Budget validé" in client.get(f"/api/sessions/{session['id']}/export?format=md").text
