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


def test_decoding_loops_are_collapsed():
    assert clean_text("Hello " * 200) == "Hello"
    assert clean_text("Hi Hello Hello Hello Hello") == "Hi Hello"
    assert clean_text("c'est pas mal, c'est pas mal, c'est pas mal, c'est pas mal") == "c'est pas mal"
    # A phrase said twice is kept: people do repeat themselves.
    assert clean_text("oui oui, on continue") == "oui oui, on continue"


def test_speaker_names_in_exports():
    session = {
        **SESSION,
        "speakers": {"S1": "M. le Ministre"},
        "segments": [
            {**SESSION["segments"][0], "speaker": "S1"},
            {**SESSION["segments"][1], "speaker": "S2"},
        ],
    }
    txt = to_txt(session)
    assert "M. le Ministre :\n[00:00:01] Bonjour à tous." in txt
    assert "Intervenant 2 :\n[01:02:05] Let's start." in txt
    assert "M. le Ministre : Bonjour à tous." in to_srt(session)
    assert "### Intervenant 2" in to_markdown(session)


def test_notes_come_first_in_exports():
    session = {**SESSION, "notes": "Décision : lancer l'appel d'offres.\n[12:04] Budget validé"}
    txt = to_txt(session)
    assert txt.index("Notes :") < txt.index("Transcription :") < txt.index("Bonjour à tous.")
    assert "[12:04] Budget validé" in txt
    md = to_markdown(session)
    assert "## Notes" in md and md.index("## Notes") < md.index("## Transcription")
    assert "Notes" not in to_txt(SESSION)  # nothing added without notes
