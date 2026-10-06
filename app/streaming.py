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
from .speakers import SpeakerTracker
from .transcriber import Transcriber

# (start, end) sample offsets of speech regions within the given audio
VadFn = Callable[[np.ndarray], list[tuple[int, int]]]

PAD = int(0.2 * SAMPLE_RATE)  # audio kept around speech so words are not clipped


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
    speaker: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text,
            "language": self.language,
            "speaker": self.speaker,
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
    speakers: SpeakerTracker | None = None  # None = no speaker labels

    _buffer: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    _offset: int = 0  # absolute sample index of _buffer[0]
    _pending: int = 0  # samples received since the last step
    _next_id: int = 0
    _last_partial: str = ""
    _partial_language: str | None = None  # language guess for partials in auto mode

    def add_audio(self, chunk: np.ndarray) -> None:
        self._buffer = np.concatenate([self._buffer, chunk.astype(np.float32)])
        self._pending += len(chunk)

    def ready(self) -> bool:
        return self._pending >= int(self.settings.step * SAMPLE_RATE)

    def step(self) -> list[Event]:
        # Audio piled up while the previous step ran: we are behind real time,
        # so spend the CPU on final segments and skip partials until caught up.
        behind = self._pending >= 2 * int(self.settings.step * SAMPLE_RATE)
        self._pending = 0
        events: list[Event] = []
        while True:
            speech = self.vad(self._buffer) if len(self._buffer) else []
            if not speech:
                # Nothing said: keep a short tail so the start of the next word survives.
                self._drop(max(0, len(self._buffer) - PAD))
                return events + self._partial("")

            end = self._utterance_end(speech)
            if end is None and len(self._buffer) >= int(self.settings.max_segment * SAMPLE_RATE):
                end = self._cut_point(speech, len(self._buffer))
            if end is None:
                return events + ([] if behind else self._partial_from(speech[0][0]))
            # Loop: on a slow CPU several utterances can be waiting in the buffer.
            events += self._finalize(speech[0][0], end)

    def flush(self) -> list[Event]:
        """Finalize whatever is left (called when the session stops)."""
        events: list[Event] = []
        while speech := (self.vad(self._buffer) if len(self._buffer) else []):
            end = self._utterance_end(speech) or speech[-1][1]
            events += self._finalize(speech[0][0], end)
        return events

    # -- internals ---------------------------------------------------------

    def _utterance_end(self, speech: list[tuple[int, int]]) -> int | None:
        """End of the first speech run followed by a long enough pause.

        The pause may sit anywhere in the buffer, not only at its end: when
        transcription is slower than real time, the next speaker has often
        started by the time the buffer is analysed again.
        """
        min_gap = int(self.settings.min_silence * SAMPLE_RATE)
        starts = [start for start, _ in speech[1:]] + [len(self._buffer)]
        for (_, end), next_start in zip(speech, starts):
            if next_start - end >= min_gap:
                return end
        return None

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

        # In auto mode the previous segment's language is tried first; the
        # transcriber re-detects when the result looks unreliable.
        result = self.transcriber.transcribe(
            audio, language=self.language, hint=self._partial_language
        )

        if result.text:
            segment = Segment(
                id=self._next_id,
                start=(self._offset + start) / SAMPLE_RATE,
                end=(self._offset + end) / SAMPLE_RATE,
                text=result.text,
                language=result.language,
            )
            if self.speakers:
                segment.speaker = self.speakers.assign(segment.id, audio)
            self._next_id += 1
            events.append(Event(type="final", segment=segment))
            self._partial_language = result.language

        self._drop(end)
        return events

    def _partial_from(self, start: int) -> list[Event]:
        audio = self._buffer[max(0, start - PAD) :]
        language = self.language or self._partial_language
        if language is None:
            # First words of the meeting: detect once, later partials reuse the
            # language of the previous final segment. A guess on less than two
            # seconds is unreliable, and a wrong one makes Whisper translate.
            if len(audio) < 2 * SAMPLE_RATE:
                return []
            language = self._partial_language = self.transcriber.detect_language(audio)
        result = self.transcriber.transcribe(audio, language=language, fast=True)
        return self._partial(result.text)

    def _partial(self, text: str) -> list[Event]:
        if text == self._last_partial:
            return []
        self._last_partial = text
        return [Event(type="partial", text=text)]

    def _drop(self, samples: int) -> None:
        self._buffer = self._buffer[samples:]
        self._offset += samples
