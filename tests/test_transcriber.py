import numpy as np

from app.transcriber import Transcription, WhisperTranscriber

AUDIO = np.zeros(4 * 16000, dtype=np.float32)
SHORT = np.zeros(2 * 16000, dtype=np.float32)


def stub(scores: dict[str, float], detected: str, probability: float = 0.95):
    """WhisperTranscriber without a model: `scores` = logprob per language."""
    t = WhisperTranscriber.__new__(WhisperTranscriber)
    t.calls = []

    def run(audio, language, fast):
        t.calls.append(language)
        return Transcription(text=f"text-{language}", language=language, logprob=scores[language])

    def detect(audio):
        t.calls.append("detect")
        return detected, probability

    t._run, t._detect = run, detect
    return t


def test_confident_hint_needs_a_single_pass():
    t = stub({"fr": -0.2}, detected="fr")
    assert t.transcribe(AUDIO, hint="fr").language == "fr"
    assert t.calls == ["fr"]


def test_unsure_hint_confirmed_by_detection_is_kept():
    t = stub({"fr": -0.5}, detected="fr")
    assert t.transcribe(AUDIO, hint="fr").text == "text-fr"
    assert t.calls == ["fr", "detect"]


def test_language_switch_is_caught():
    # French guessed, but the speaker switched to English: Whisper "translated"
    # with low confidence, detection says English, so it is redone.
    t = stub({"fr": -0.9, "en": -0.2}, detected="en")
    assert t.transcribe(AUDIO, hint="fr").text == "text-en"
    assert t.calls == ["fr", "detect", "en"]


def test_forced_language_skips_detection():
    t = stub({"en": -1.5}, detected="fr")
    assert t.transcribe(AUDIO, language="en", hint="fr").language == "en"
    assert t.calls == ["en"]


def test_empty_result_with_hint_triggers_detection():
    t = stub({"en": float("-inf"), "fr": -0.3}, detected="fr")
    assert t.transcribe(AUDIO, hint="en").text == "text-fr"
    assert t.calls == ["en", "detect", "fr"]


def test_short_segment_keeps_the_previous_language():
    # A few words are not enough to trust a detection: no switch.
    t = stub({"fr": -0.9, "en": -0.2}, detected="en")
    assert t.transcribe(SHORT, hint="fr").language == "fr"
    assert t.calls == ["fr"]


def test_unconfident_detection_does_not_switch():
    t = stub({"fr": -0.9, "en": -0.2}, detected="en", probability=0.55)
    assert t.transcribe(AUDIO, hint="fr").language == "fr"
    assert t.calls == ["fr", "detect"]
