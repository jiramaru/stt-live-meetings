from app.exporters import to_markdown, to_srt, to_txt
from app.transcriber import clean_text

SESSION = {
    "id": "a" * 32,
    "title": "Comité technique",
    "language": None,
    "started_at": "2026-10-06T09:00:00+00:00",
    "ended_at": "2026-10-06T10:00:00+00:00",
    "segments": [
        {"id": 0, "start": 1.2, "end": 4.5, "text": "Bonjour à tous.", "language": "fr"},
        {"id": 1, "start": 3725.0, "end": 3728.25, "text": "Let's start.", "language": "en"},
    ],
}


def test_txt():
    out = to_txt(SESSION)
    assert out.startswith("Comité technique\n")
    assert "[00:00:01] Bonjour à tous." in out
    assert "[01:02:05] Let's start." in out


def test_srt():
    assert to_srt(SESSION) == (
        "1\n00:00:01,200 --> 00:00:04,500\nBonjour à tous.\n\n"
        "2\n01:02:05,000 --> 01:02:08,250\nLet's start.\n"
    )


def test_markdown():
    assert to_markdown(SESSION).startswith("# Comité technique\n")


def test_hallucinations_are_removed():
    assert clean_text(" Sous-titres réalisés par la communauté d'Amara.org ") == ""
    assert clean_text("Merci d'avoir regardé !") == ""
    assert clean_text("...") == ""
    assert clean_text("  Merci   pour  ce point. ") == "Merci pour ce point."
