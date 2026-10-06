"""Turns a continuous audio stream into partial and final transcript segments.

Audio is accumulated in a buffer. On every step the buffer is run through a
voice activity detector (VAD):

- a pause of `min_silence` after speech closes the segment, which is
  transcribed once more and emitted as *final*;
- a segment longer than `max_segment` is cut at its last internal pause (or
  forcibly) so text keeps flowing during long monologues;
- otherwise the open segment is transcribed and emitted as *partial*, so the
  UI can show words while the speaker is still talking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .config import SAMPLE_RATE, Settings
from .transcriber import Transcriber

# (start, end) sample offsets of speech regions within the given audio
VadFn = Callable[[np.ndarray], list[tuple[int, int]]]

PAD = int(0.2 * SAMPLE_RATE)  # audio kept around speech so words are not clipped
PROMPT_CHARS = 200  # previous text given to Whisper for context


def silero_vad(threshold: float = 0.5) -> VadFn:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=threshold, min_silence_duration_ms=200, speech_pad_ms=0)

    def run(audio: np.ndarray) -> list[tuple[int, int]]:
        return [(t["start"], t["end"]) for t in get_speech_timestamps(audio, options)]

    return run


@dataclass
class Segment:
    id: int
    start: float  # seconds since the session started
    end: float
    text: str
    language: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            "language": self.language,
        }


@dataclass
class Event:
    type: str  # "partial" or "final"
    text: str = ""
    segment: Segment | None = None


@dataclass
class StreamingSegmenter:
    transcriber: Transcriber
    vad: VadFn
    settings: Settings
    language: str | None = None  # None = auto-detect among allowed languages

    _buffer: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    _offset: int = 0  # absolute sample index of _buffer[0]
    _pending: int = 0  # samples received since the last step
    _next_id: int = 0
    _last_partial: str = ""
    _segment_language: str | None = None
    _context: str = ""

    def add_audio(self, chunk: np.ndarray) -> None:
        self._buffer = np.concatenate([self._buffer, chunk.astype(np.float32)])
        self._pending += len(chunk)

    def ready(self) -> bool:
        return self._pending >= int(self.settings.step * SAMPLE_RATE)

    def step(self) -> list[Event]:
        self._pending = 0
        buf = self._buffer
        speech = self.vad(buf) if len(buf) else []

        if not speech:
            # Nothing said: keep a short tail so the start of the next word survives.
            self._drop(max(0, len(buf) - PAD))
            return self._partial("")

        silence_tail = len(buf) - speech[-1][1]
        if silence_tail >= int(self.settings.min_silence * SAMPLE_RATE):
            return self._finalize(speech[0][0], speech[-1][1])

        if len(buf) >= int(self.settings.max_segment * SAMPLE_RATE):
            return self._finalize(speech[0][0], self._cut_point(speech, len(buf)))

        return self._partial_from(speech[0][0])

    def flush(self) -> list[Event]:
        """Finalize whatever is left (called when the session stops)."""
        speech = self.vad(self._buffer) if len(self._buffer) else []
        if not speech:
            return []
        return self._finalize(speech[0][0], speech[-1][1])

    # -- internals ---------------------------------------------------------

    def _cut_point(self, speech: list[tuple[int, int]], length: int) -> int:
        """End of the last speech region followed by a pause, or the whole buffer."""
        if len(speech) > 1:
            return speech[-2][1]
        return length

    def _finalize(self, start: int, end: int) -> list[Event]:
        start = max(0, start - PAD)
        end = min(len(self._buffer), end + PAD)
        audio = self._buffer[start:end]
        events: list[Event] = []

        # In auto mode the full segment is re-detected: more reliable than the
        # guess made on its first second.
        result = self.transcriber.transcribe(audio, language=self.language, prompt=self._context)

        if result.text:
            segment = Segment(
                id=self._next_id,
                start=(self._offset + start) / SAMPLE_RATE,
                end=(self._offset + end) / SAMPLE_RATE,
                text=result.text,
                language=result.language,
            )
            self._next_id += 1
            self._context = (self._context + " " + result.text)[-PROMPT_CHARS:]
            events.append(Event(type="final", segment=segment))

        self._segment_language = None
        self._drop(end)
        self._last_partial = ""
        events.extend(self._partial("", force=True))
        return events

    def _partial_from(self, start: int) -> list[Event]:
        audio = self._buffer[max(0, start - PAD) :]
        language = self.language or self._segment_language
        if language is None:
            # Detect once per segment; reuse it for later partials and the final pass.
            language = self._segment_language = self.transcriber.detect_language(audio)
        result = self.transcriber.transcribe(audio, language=language, prompt=self._context)
        return self._partial(result.text)

    def _partial(self, text: str, force: bool = False) -> list[Event]:
        if text == self._last_partial and not force:
            return []
        self._last_partial = text
        return [Event(type="partial", text=text)]

    def _drop(self, samples: int) -> None:
        self._buffer = self._buffer[samples:]
        self._offset += samples
