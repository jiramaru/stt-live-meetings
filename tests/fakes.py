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


def tone(seconds: float, freq: float = 220) -> np.ndarray:
    """A stand-in for a voice: the pitch plays the role of the speaker."""
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class FakeTranscriber:
    """Reports how many seconds of speech it was given, e.g. 'speech 2.0s'."""

    def __init__(self):
        self.calls: list[tuple[int, str | None, str | None]] = []

    def detect_language(self, audio):
        return "fr"

    def transcribe(self, audio, language=None, fast=False, hint=None):
        self.calls.append((len(audio), language))
        voiced = sum(e - s for s, e in energy_vad(audio)) / SAMPLE_RATE
        text = f"speech {voiced:.1f}s" if voiced else ""
        return Transcription(text=text, language=language or "fr")


class FakeEmbedder:
    """Embedding = pitch histogram: same tone frequency -> same 'voice'."""

    BINS = np.array([110, 220, 330, 440, 550, 660, 770, 880])

    def embed(self, audio):
        spectrum = np.abs(np.fft.rfft(audio))
        freqs = np.fft.rfftfreq(len(audio), 1 / SAMPLE_RATE)
        peak = freqs[np.argmax(spectrum)]
        v = np.exp(-(((self.BINS - peak) / 40.0) ** 2)) + 0.01
        return v.astype(np.float32)
