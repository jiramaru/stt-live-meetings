import numpy as np
import pytest

from app.speakers import SpeakerTracker

from .fakes import FakeEmbedder, tone


def make(threshold=0.5):
    return SpeakerTracker(FakeEmbedder(), threshold=threshold)


def test_same_voice_same_label_new_voice_new_label():
    tracker = make()
    labels = [
        tracker.assign(0, tone(2.0, 220)),
        tracker.assign(1, tone(2.0, 660)),
        tracker.assign(2, tone(1.5, 220)),
        tracker.assign(3, tone(1.5, 660)),
    ]
    assert labels == ["S1", "S2", "S1", "S2"]


def test_short_segment_takes_previous_speaker():
    tracker = make()
    tracker.assign(0, tone(2.0, 220))
    assert tracker.assign(1, tone(0.4, 880)) == "S1"


def test_refine_merges_a_voice_split_in_two():
    # Live, one slightly varying voice became two speakers (similarity ~0.7);
    # at the end both clusters are close enough to merge.
    tracker = make(threshold=0.9)
    tracker.merge_threshold = 0.6
    tracker.assign(0, tone(2.0, 220))
    tracker.assign(1, tone(2.0, 275))
    assert tracker._centroids.keys() == {"S1", "S2"}
    assert set(tracker.refine().values()) == {"S1"}


def test_refine_does_not_merge_distinct_voices():
    tracker = make()
    tracker.assign(0, tone(2.0, 220))
    tracker.assign(1, tone(2.0, 290))  # similarity ~0.16
    assert set(tracker.refine().values()) == {"S1", "S2"}


def test_refine_keeps_ids_stable_and_fills_short_segments():
    tracker = make()
    tracker.assign(0, tone(0.5, 660))  # too short, nobody known yet
    tracker.assign(1, tone(2.0, 660))
    tracker.assign(2, tone(2.0, 220))
    tracker.assign(3, tone(0.5, 220))
    labels = tracker.refine()
    assert labels == {0: "S1", 1: "S1", 2: "S2", 3: "S2"}


def test_refine_without_embeddings_keeps_live_labels():
    tracker = make()
    tracker.assign(0, tone(0.3))
    assert tracker.refine() == {0: None}


# ---------------------------------------------------------------- voice profiles

from app.speakers import VoiceProfile, enrol  # noqa: E402

from .fakes import energy_vad  # noqa: E402


def profile(pid, freq, name="X"):
    p = enrol(FakeEmbedder(), energy_vad, tone(10.0, freq))
    return VoiceProfile(pid, name, p["embedding"], p["mu"], p["sd"])


def test_enrolment_needs_enough_speech():
    with pytest.raises(ValueError, match="parole"):
        enrol(FakeEmbedder(), energy_vad, tone(4.0))
    p = enrol(FakeEmbedder(), energy_vad, tone(10.0))
    assert p["seconds"] == 10.0 and p["sd"] >= 0.02


def test_enrolled_voices_are_recognised_by_name():
    tracker = make()
    tracker.profiles = [profile("Paaaaaaaa", 220, "Awa"), profile("Pbbbbbbbb", 660, "Paul")]
    labels = [tracker.assign(i, tone(2.0, f)) for i, f in enumerate([220, 660, 220])]
    assert labels == ["Paaaaaaaa", "Pbbbbbbbb", "Paaaaaaaa"]


def test_unknown_voice_becomes_a_guest_and_stays_one_after_refine():
    tracker = make()
    tracker.profiles = [profile("Paaaaaaaa", 220)]
    labels = [tracker.assign(i, tone(2.0, f)) for i, f in enumerate([220, 660, 660, 220])]
    assert labels == ["Paaaaaaaa", "S1", "S1", "Paaaaaaaa"]
    assert tracker.refine() == {0: "Paaaaaaaa", 1: "S1", 2: "S1", 3: "Paaaaaaaa"}


def test_short_reply_goes_to_the_closest_profile():
    # 1 s is too short to declare an unknown voice: the best profile wins.
    tracker = make()
    tracker.profiles = [profile("Paaaaaaaa", 220), profile("Pbbbbbbbb", 660)]
    tracker.assign(0, tone(2.0, 220))
    assert tracker.assign(1, tone(0.8, 660)) == "Pbbbbbbbb"
