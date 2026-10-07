"""Speech-to-text engine wrapper around faster-whisper.

The rest of the app only depends on the `Transcriber` protocol, so another
engine (cloud API, GPU server, ...) can be plugged in later.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
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
# The same 1-6 words repeated 3+ times in a row: Whisper's decoding loop.
_LOOP_RE = re.compile(r"\b((?:\S+\s+){0,5}?\S+?)(?:[\s,.!?;]+\1\b){2,}", re.IGNORECASE)

# Average token log-probability below which a transcription made with a guessed
# language is double-checked. Given the wrong language, Whisper tends to
# translate rather than fail; on real recordings (FR and EN) that scored -0.46
# or lower, while the right language scored between -0.11 and -0.56.
LANGUAGE_CHECK_LOGPROB = -0.4
# Language detection on a few words is unreliable: below this length the
# previous segment's language is kept, and a switch needs a confident detection.
MIN_DETECT_SECONDS = 3.0
MIN_SWITCH_PROBABILITY = 0.7


@dataclass
class Transcription:
    text: str
    language: str | None
    logprob: float = 0.0  # average token log-probability (confidence), -inf if empty
    # (start, end, text) of each word, in seconds from the start of the audio,
    # when requested with words=True
    words: list[tuple[float, float, str]] = field(default_factory=list)


class Transcriber(Protocol):
    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        fast: bool = False,
        hint: str | None = None,
        words: bool = False,
    ) -> Transcription: ...

    def detect_language(self, audio: np.ndarray) -> str | None: ...


def clean_text(text: str) -> str:
    text = " ".join(text.split())
    text = _LOOP_RE.sub(r"\1", text)
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
        lang, _ = self._detect(audio)
        return lang

    def _detect(self, audio: np.ndarray) -> tuple[str | None, float]:
        """(language, probability among the allowed languages)."""
        if len(audio) < SAMPLE_RATE:
            return None, 0.0
        with self._lock:
            _, _, probs = self.model.detect_language(audio)
        allowed = [(lang, p) for lang, p in probs if lang in self.languages]
        if not allowed:
            return (self.languages[0] if self.languages else None), 0.0
        total = sum(p for _, p in allowed) or 1.0
        lang, p = max(allowed, key=lambda item: item[1])
        return lang, p / total

    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        fast: bool = False,
        hint: str | None = None,
        words: bool = False,
    ) -> Transcription:
        """Transcribe in `language`, or detect it when None.

        `hint` (auto mode) is the likely language, usually that of the previous
        segment. It is tried first and the costly detection pass (a whole extra
        run of the encoder) only happens when the result looks unreliable.

        `fast` (used for partials) disables Whisper's temperature fallback,
        which re-decodes when the output looks unreliable; on CPU it can
        multiply the cost of a short, unclear chunk several times over.

        `words` also returns each word's timing (used to split a segment
        where the speaker changes).
        """
        if language is None and hint:
            result = self._run(audio, hint, fast, words)
            if result.logprob >= LANGUAGE_CHECK_LOGPROB or len(audio) < MIN_DETECT_SECONDS * SAMPLE_RATE:
                return result
            detected, probability = self._detect(audio)
            if detected in (None, hint) or probability < MIN_SWITCH_PROBABILITY:
                return result
            return self._run(audio, detected, fast, words)
        if language is None:
            language = self.detect_language(audio) or (self.languages[0] if self.languages else None)
        return self._run(audio, language, fast, words)

    def _run(self, audio: np.ndarray, language: str | None, fast: bool, words: bool = False):
        with self._lock:
            segments, info = self.model.transcribe(
                audio,
                language=language,
                beam_size=1 if fast else self.settings.beam_size,
                temperature=0.0 if fast else [0.0, 0.4],
                without_timestamps=not words,
                word_timestamps=words,
                # No previous text as prompt: on short or unclear audio Whisper
                # copies the prompt instead of listening, and errors snowball.
                condition_on_previous_text=False,
                vad_filter=False,  # segmentation already did VAD
                no_speech_threshold=0.6,
                log_prob_threshold=-1.0,
            )
            kept = [
                s for s in segments if not (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0)
            ]
        logprob = float("-inf")  # nothing recognised: no confidence at all
        if kept:
            tokens = [max(1, len(s.tokens)) for s in kept]
            logprob = float(np.average([s.avg_logprob for s in kept], weights=tokens))
        text = clean_text(" ".join(s.text for s in kept))
        timed = [(w.start, w.end, w.word) for s in kept for w in (s.words or [])]
        return Transcription(text=text, language=info.language, logprob=logprob, words=timed)
