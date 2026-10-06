"""Speech-to-text engine wrapper around faster-whisper.

The rest of the app only depends on the `Transcriber` protocol, so another
engine (cloud API, GPU server, ...) can be plugged in later.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .config import SAMPLE_RATE, Settings

log = logging.getLogger(__name__)

# Phrases Whisper tends to invent on silence or noise (learned from subtitle data).
_HALLUCINATIONS = [
    r"sous-titr(es|age).*",
    r".*amara\.org.*",
    r"merci d'avoir regardé.*",
    r"abonnez-vous.*",
    r"thanks? (you )?for watching.*",
    r"please subscribe.*",
    r"\.+",
]
_HALLUCINATION_RE = re.compile(r"^\s*(" + "|".join(_HALLUCINATIONS) + r")\s*$", re.IGNORECASE)


@dataclass
class Transcription:
    text: str
    language: str | None


class Transcriber(Protocol):
    def transcribe(
        self, audio: np.ndarray, language: str | None = None, prompt: str | None = None
    ) -> Transcription: ...

    def detect_language(self, audio: np.ndarray) -> str | None: ...


def clean_text(text: str) -> str:
    text = " ".join(text.split())
    if _HALLUCINATION_RE.match(text):
        return ""
    return text


class WhisperTranscriber:
    def __init__(self, settings: Settings):
        from faster_whisper import WhisperModel

        self.settings = settings
        self.languages = settings.language_list
        log.info(
            "Loading Whisper model '%s' on %s (%s)",
            settings.model_size,
            settings.device,
            settings.compute_type,
        )
        self.model = WhisperModel(
            settings.model_size,
            device=settings.device,
            compute_type=settings.compute_type,
            cpu_threads=settings.cpu_threads,
        )
        # One inference at a time: parallel runs on CPU only fight for cores.
        self._lock = threading.Lock()

    def detect_language(self, audio: np.ndarray) -> str | None:
        """Most likely language among the allowed ones."""
        if len(audio) < SAMPLE_RATE:
            return None
        with self._lock:
            _, _, probs = self.model.detect_language(audio)
        allowed = [(lang, p) for lang, p in probs if lang in self.languages]
        if not allowed:
            return self.languages[0] if self.languages else None
        return max(allowed, key=lambda item: item[1])[0]

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, prompt: str | None = None
    ) -> Transcription:
        if language is None:
            language = self.detect_language(audio) or (self.languages[0] if self.languages else None)
        with self._lock:
            segments, info = self.model.transcribe(
                audio,
                language=language,
                beam_size=self.settings.beam_size,
                initial_prompt=prompt or None,
                condition_on_previous_text=False,
                vad_filter=False,  # segmentation already did VAD
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
            )
            parts = [
                s.text
                for s in segments
                if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)
            ]
        return Transcription(text=clean_text(" ".join(parts)), language=info.language)
