"""Test doubles: an energy VAD and a transcriber that needs no model."""

import numpy as np

from app.config import SAMPLE_RATE
from app.transcriber import Transcription

FRAME = SAMPLE_RATE // 100  # 10 ms


def energy_vad(audio: np.ndarray) -> list[tuple[int, int]]:
    """Speech = consecutive 10 ms frames whose mean amplitude exceeds 0.05."""
    regions: list[tuple[int, int]] = []
    start = None
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        loud = np.abs(audio[i : i + FRAME]).mean() > 0.05
        if loud and start is None:
            start = i
        elif not loud and start is not None:
            regions.append((start, i))
            start = None
    if start is not None:
        regions.append((start, len(audio) - len(audio) % FRAME))
    return regions


def tone(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class FakeTranscriber:
    """Reports how many seconds of speech it was given, e.g. 'speech 2.0s'."""

    def __init__(self):
        self.calls: list[tuple[int, str | None, str | None]] = []

    def detect_language(self, audio):
        return "fr"

    def transcribe(self, audio, language=None, prompt=None):
        self.calls.append((len(audio), language, prompt))
        voiced = sum(e - s for s, e in energy_vad(audio)) / SAMPLE_RATE
        text = f"speech {voiced:.1f}s" if voiced else ""
        return Transcription(text=text, language=language or "fr")
