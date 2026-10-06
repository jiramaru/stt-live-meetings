"""Transcript export formats."""

from __future__ import annotations

import io
from datetime import datetime


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    return f"{ms // 3_600_000:02d}:{ms % 3_600_000 // 60_000:02d}:{ms % 60_000 // 1000:02d},{ms % 1000:03d}"


def _started(session: dict) -> str:
    return datetime.fromisoformat(session["started_at"]).astimezone().strftime("%d/%m/%Y %H:%M")


def to_txt(session: dict) -> str:
    lines = [session["title"], f"Date : {_started(session)}", ""]
    lines += [f"[{_clock(s['start'])}] {s['text']}" for s in session["segments"]]
    return "\n".join(lines) + "\n"


def to_markdown(session: dict) -> str:
    lines = [f"# {session['title']}", "", f"*Date : {_started(session)}*", ""]
    lines += [f"**{_clock(s['start'])}** {s['text']}  " for s in session["segments"]]
    return "\n".join(lines) + "\n"


def to_srt(session: dict) -> str:
    blocks = [
        f"{i}\n{_srt_time(s['start'])} --> {_srt_time(s['end'])}\n{s['text']}\n"
        for i, s in enumerate(session["segments"], start=1)
    ]
    return "\n".join(blocks)


def to_docx(session: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    doc.add_heading(session["title"], level=1)
    doc.add_paragraph(f"Date : {_started(session)}")
    for s in session["segments"]:
        p = doc.add_paragraph()
        stamp = p.add_run(f"[{_clock(s['start'])}]  ")
        stamp.font.size = Pt(9)
        stamp.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        p.add_run(s["text"])
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# format -> (renderer, media type, extension)
EXPORTERS = {
    "txt": (to_txt, "text/plain; charset=utf-8", "txt"),
    "md": (to_markdown, "text/markdown; charset=utf-8", "md"),
    "srt": (to_srt, "application/x-subrip; charset=utf-8", "srt"),
    "docx": (
        to_docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
}
