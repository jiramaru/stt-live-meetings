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

    def create(self, title: str, language: str | None) -> dict:
        session = {
            "id": uuid.uuid4().hex,
            "title": title.strip() or "Réunion sans titre",
            "language": language,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "speakers": {},  # speaker id -> name given by the user
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
