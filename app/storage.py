"""Meeting sessions persisted as one JSON file each."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class SessionStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "sessions"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        if not _ID_RE.match(session_id):
            raise KeyError(session_id)
        return self.dir / f"{session_id}.json"

    def audio_path(self, session_id: str) -> Path:
        return self._path(session_id).with_suffix(".wav")

    def create(self, title: str, language: str | None) -> dict:
        session = {
            "id": uuid.uuid4().hex,
            "title": title.strip() or "Réunion sans titre",
            "language": language,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "speakers": {},  # speaker id -> name given by the user
            "participants": [],  # voice profile ids of the people present ([] = all)
            "segments": [],
        }
        self.save(session)
        return session

    def save(self, session: dict) -> None:
        path = self._path(session["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def get(self, session_id: str) -> dict:
        path = self._path(session_id)
        if not path.exists():
            raise KeyError(session_id)
        session = json.loads(path.read_text(encoding="utf-8"))
        session.setdefault("speakers", {})  # sessions recorded before speaker labels
        session.setdefault("participants", [])
        return session

    def list(self) -> list[dict]:
        sessions = []
        for path in self.dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            segments = data.pop("segments")
            data["segment_count"] = len(segments)
            data["duration"] = segments[-1]["end"] if segments else 0
            sessions.append(data)
        return sorted(sessions, key=lambda s: s["started_at"], reverse=True)

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        if not path.exists():
            raise KeyError(session_id)
        path.unlink()
        self.audio_path(session_id).unlink(missing_ok=True)


class ProfileStore:
    """Voice profiles, one JSON file each. Only the voice print is kept, never
    the enrolment audio."""

    _ID_RE = re.compile(r"^P[0-9a-f]{8}$")

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "profiles"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, profile_id: str) -> Path:
        if not self._ID_RE.match(profile_id):
            raise KeyError(profile_id)
        return self.dir / f"{profile_id}.json"

    def create(self, name: str, print_: dict) -> dict:
        profile = {
            "id": "P" + uuid.uuid4().hex[:8],
            "name": name.strip()[:80],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "seconds": print_["seconds"],
            "mu": print_["mu"],
            "sd": print_["sd"],
            "embedding": [round(float(x), 6) for x in print_["embedding"]],
        }
        self._write(profile)
        return self.public(profile)

    def _write(self, profile: dict) -> None:
        path = self._path(profile["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _read(self, profile_id: str) -> dict:
        path = self._path(profile_id)
        if not path.exists():
            raise KeyError(profile_id)
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def public(profile: dict) -> dict:
        """What the API shows: everything but the voice print itself."""
        return {k: profile[k] for k in ("id", "name", "created_at", "seconds")}

    def list(self) -> list[dict]:
        profiles = [json.loads(p.read_text(encoding="utf-8")) for p in self.dir.glob("P*.json")]
        return sorted((self.public(p) for p in profiles), key=lambda p: p["name"].lower())

    def rename(self, profile_id: str, name: str) -> dict:
        profile = self._read(profile_id)
        profile["name"] = name.strip()[:80] or profile["name"]
        self._write(profile)
        return self.public(profile)

    def delete(self, profile_id: str) -> None:
        path = self._path(profile_id)
        if not path.exists():
            raise KeyError(profile_id)
        path.unlink()

    def voices(self):
        """All profiles, ready for matching."""
        import numpy as np

        from .speakers import VoiceProfile

        return [
            VoiceProfile(
                id=p["id"],
                name=p["name"],
                embedding=np.asarray(p["embedding"], dtype=np.float32),
                mu=p["mu"],
                sd=p["sd"],
            )
            for p in (json.loads(f.read_text(encoding="utf-8")) for f in self.dir.glob("P*.json"))
        ]
