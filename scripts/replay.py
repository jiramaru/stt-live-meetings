"""Re-run the transcription pipeline on a recorded meeting, offline.

    python -m scripts.replay data/sessions/<id>.wav [--speaker-threshold 0.5]

Prints each segment with its live and refined speaker, then the voice
similarity between segments: the numbers needed to tune speaker thresholds.
Partials are skipped, so a meeting replays faster than real time.
"""

import argparse
import time
import wave

import numpy as np

from app.config import SAMPLE_RATE, get_settings
from app.speakers import SherpaEmbedder, SpeakerTracker
from app.streaming import StreamingSegmenter, silero_vad
from app.transcriber import WhisperTranscriber


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wav")
    parser.add_argument("--speaker-threshold", type=float)
    parser.add_argument("--merge-threshold", type=float)
    parser.add_argument("--language", choices=["fr", "en"])
    args = parser.parse_args()

    settings = get_settings()
    with wave.open(args.wav) as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1
        audio = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768

    tracker = SpeakerTracker(
        SherpaEmbedder(str(settings.speaker_model)),
        threshold=args.speaker_threshold or settings.speaker_threshold,
        merge_threshold=args.merge_threshold or settings.speaker_merge_threshold,
    )
    segmenter = StreamingSegmenter(
        WhisperTranscriber(settings), silero_vad(settings.vad_threshold), settings,
        language=args.language, speakers=tracker,
    )
    segmenter._partial_from = lambda start: []  # offline: final text only

    t0 = time.time()
    segments = []
    chunk = SAMPLE_RATE // 10
    for i in range(0, len(audio), chunk):
        segmenter.add_audio(audio[i : i + chunk])
        if segmenter.ready():
            segments += [e.segment for e in segmenter.step() if e.type == "final"]
    segments += [e.segment for e in segmenter.flush() if e.type == "final"]
    live = {s.id: s.speaker for s in segments}
    refined = tracker.refine()
    print(f"{len(audio) / SAMPLE_RATE:.0f} s of audio processed in {time.time() - t0:.0f} s\n")

    for s in segments:
        print(f"#{s.id:<3} {s.start:7.1f}-{s.end:7.1f} {s.language} "
              f"live={live[s.id] or '-':4} refined={refined[s.id] or '-':4} {s.text}")

    items = [it for it in tracker._items if it.embedding is not None]
    if len(items) > 1:
        print("\nVoice similarity between segments (>= 1 s):")
        ids = [it.segment_id for it in items]
        print("      " + " ".join(f"{i:>5}" for i in ids))
        for a in items:
            row = " ".join(f"{float(a.embedding @ b.embedding):5.2f}" for b in items)
            print(f"#{a.segment_id:<4} {row}")


if __name__ == "__main__":
    main()
