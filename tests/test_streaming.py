import numpy as np

from app.config import SAMPLE_RATE, Settings
from app.streaming import StreamingSegmenter

from .fakes import FakeTranscriber, energy_vad, silence, tone

STEP = 0.25


def make(**overrides) -> tuple[StreamingSegmenter, FakeTranscriber]:
    settings = Settings(**{"step": STEP, "min_silence": 0.6, "max_segment": 8.0, **overrides})
    transcriber = FakeTranscriber()
    return StreamingSegmenter(transcriber, energy_vad, settings), transcriber


def feed(segmenter: StreamingSegmenter, audio: np.ndarray) -> list:
    """Push audio in 100 ms chunks, stepping whenever the segmenter is ready."""
    events = []
    chunk = SAMPLE_RATE // 10
    for i in range(0, len(audio), chunk):
        segmenter.add_audio(audio[i : i + chunk])
        if segmenter.ready():
            events += segmenter.step()
    return events


def finals(events):
    return [e.segment for e in events if e.type == "final"]


def test_pause_closes_segment_with_absolute_timestamps():
    seg, _ = make()
    events = feed(seg, np.concatenate([silence(1.0), tone(2.0), silence(1.0)]))

    [segment] = finals(events)
    assert segment.text == "speech 2.0s"
    assert segment.start == 0.8  # 1.0 s minus padding
    assert segment.end == 3.2  # 3.0 s plus padding


def test_partials_are_emitted_while_speaking():
    seg, _ = make()
    events = feed(seg, tone(3.0))

    partials = [e.text for e in events if e.type == "partial"]
    assert partials, "expected partial results during speech"
    assert not finals(events)


def test_two_utterances_give_two_segments_and_carry_context():
    seg, transcriber = make()
    audio = np.concatenate([tone(1.5), silence(1.0), tone(1.0), silence(1.0)])
    segments = finals(feed(seg, audio))

    assert [s.text for s in segments] == ["speech 1.5s", "speech 1.0s"]
    assert [s.id for s in segments] == [0, 1]
    assert segments[1].start > segments[0].end
    # The second final pass is primed with the first segment's text.
    assert transcriber.calls[-1][2].strip() == "speech 1.5s"


def test_pause_is_found_even_when_steps_are_late():
    # On a slow CPU, the next step can come after the following speaker has
    # already started: the pause is then inside the buffer, not at its end.
    seg, _ = make(step=2.0)
    audio = np.concatenate([tone(1.5), silence(1.0), tone(1.0), silence(0.5)])
    events = feed(seg, audio)

    assert [s.text for s in finals(events)] == ["speech 1.5s"]
    assert [s.text for s in finals(seg.flush())] == ["speech 1.0s"]


def test_long_monologue_is_cut_at_max_segment():
    seg, _ = make()
    segments = finals(feed(seg, tone(20.0)))

    assert len(segments) >= 2
    assert all(s.end - s.start <= 8.0 + 0.5 for s in segments)


def test_silence_is_dropped_from_buffer():
    seg, transcriber = make()
    events = feed(seg, silence(30.0))

    assert not finals(events)
    assert not transcriber.calls
    assert len(seg._buffer) < SAMPLE_RATE


def test_flush_finalizes_open_segment():
    seg, _ = make()
    feed(seg, tone(1.2))
    [segment] = finals(seg.flush())
    assert segment.text == "speech 1.2s"


def test_forced_language_is_passed_through():
    seg, transcriber = make()
    seg.language = "en"
    feed(seg, np.concatenate([tone(1.0), silence(1.0)]))
    assert transcriber.calls and all(lang == "en" for _, lang, _ in transcriber.calls)


def test_partials_are_skipped_while_behind_real_time():
    seg, transcriber = make()
    seg.add_audio(tone(2.0))  # 8 steps' worth of audio arrives at once
    assert seg.step() == []
    assert not transcriber.calls
