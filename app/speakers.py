"""Speaker labelling ("who spoke when") from a single microphone.

Each stretch of speech is turned into a voice embedding: a vector that is
close for two recordings of the same voice and far apart for different voices.

- Voice profiles (like a phone's voice unlock): team members enrol once by
  reading for ~20 s. Their embedding is compared with each new stretch of
  speech, relative to how much their own voice varies (calibration).
- Voices matching no profile are clustered among themselves: each new one is
  compared with the unknown speakers met so far, and joins the closest or
  starts a new speaker ("Intervenant N").
- At the end of the meeting `refine()` re-clusters the unknown voices with
  the full picture, fixing early mistakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .config import SAMPLE_RATE

log = logging.getLogger(__name__)

MIN_EMBED_SECONDS = 1.0  # shorter speech is too little to start a new speaker
MIN_MATCH_SECONDS = 0.5  # shorter speech is too little to compare with profiles
# A stretch of speech belongs to the best matching profile unless its
# similarity falls this many standard deviations below the profile's own
# enrolment consistency, and it is long enough to judge.
GUEST_Z = 3.0
GUEST_MIN_SECONDS = 1.5

ENROL_WINDOW = 2.0  # seconds per enrolment window
ENROL_MIN_SPEECH = 8.0  # seconds of speech needed to enrol a voice


class Embedder(Protocol):
    def embed(self, audio: np.ndarray) -> np.ndarray: ...


class SherpaEmbedder:
    """Speaker embeddings with an ONNX model run by sherpa-onnx (no PyTorch)."""

    def __init__(self, model_path: str, threads: int = 2):
        import sherpa_onnx

        log.info("Loading speaker model '%s'", model_path)
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path, num_threads=threads, provider="cpu"
        )
        if not config.validate():
            raise ValueError(f"Invalid speaker model: {model_path}")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)

    def embed(self, audio: np.ndarray) -> np.ndarray:
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, waveform=audio)
        stream.input_finished()
        return np.asarray(self.extractor.compute(stream), dtype=np.float32)


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v


@dataclass
class VoiceProfile:
    id: str  # "P" + 8 hex digits, also used as the speaker id in transcripts
    name: str
    embedding: np.ndarray  # normalized
    mu: float  # mean similarity of the enrolment windows to `embedding`
    sd: float  # their spread: how much this voice varies

    def z(self, embedding: np.ndarray) -> float:
        return (float(self.embedding @ embedding) - self.mu) / self.sd


def enrol(embedder: Embedder, vad, audio: np.ndarray) -> dict:
    """Voice print from a recording of one person reading.

    The speech is cut into 2 s windows; the profile is their average, and the
    windows' similarity to it calibrates how much this voice varies.
    Raises ValueError when there is not enough speech.
    """
    speech = [audio[s:e] for s, e in vad(audio)]
    voiced = np.concatenate(speech) if speech else audio[:0]
    seconds = len(voiced) / SAMPLE_RATE
    if seconds < ENROL_MIN_SPEECH:
        raise ValueError(
            f"Seulement {seconds:.0f} s de parole détectée, il en faut au moins "
            f"{ENROL_MIN_SPEECH:.0f}. Lisez le texte à voix haute, près du micro de réunion."
        )
    size, hop = int(ENROL_WINDOW * SAMPLE_RATE), int(ENROL_WINDOW * SAMPLE_RATE / 2)
    windows = [voiced[i : i + size] for i in range(0, len(voiced) - size + 1, hop)]
    E = np.stack([_normalize(embedder.embed(w)) for w in windows])
    centroid = _normalize(E.mean(axis=0))
    sims = E @ centroid
    return {
        "embedding": centroid,
        "mu": float(sims.mean()),
        "sd": max(float(sims.std()), 0.02),  # floor: a handful of windows can look too regular
        "seconds": round(seconds, 1),
    }


@dataclass
class _Item:
    segment_id: int
    duration: float
    embedding: np.ndarray | None  # None when the segment was too short
    speaker: str | None


@dataclass
class SpeakerTracker:
    embedder: Embedder
    threshold: float = 0.5  # cosine similarity for a segment to join a speaker
    # Similarity for two speakers to be merged in refine(). Stricter, because
    # averaged embeddings are less noisy: on real recordings, averages of the
    # same voice scored 0.75+ (5th percentile) and of different voices <= 0.58.
    merge_threshold: float = 0.7
    profiles: list[VoiceProfile] = field(default_factory=list)

    _items: list[_Item] = field(default_factory=list)
    _centroids: dict[str, np.ndarray] = field(default_factory=dict)  # sum of embeddings
    _next: int = 1

    def assign(self, segment_id: int, audio: np.ndarray) -> str | None:
        """Label a whole segment live. Returns a speaker id ('P...' or 'S1')."""
        speaker, embedding = self.classify(audio)
        self.record(segment_id, len(audio) / SAMPLE_RATE, embedding, speaker)
        return speaker

    def classify(
        self, audio: np.ndarray, previous: str | None = None
    ) -> tuple[str | None, np.ndarray | None]:
        """Who is speaking in this stretch of audio: (speaker id, embedding).

        Too short to tell ("oui", "d'accord"): `previous` (by default the last
        segment's speaker), since most often the same person carries on;
        refine() settles the rest.
        """
        duration = len(audio) / SAMPLE_RATE
        if previous is None and self._items:
            previous = self._items[-1].speaker
        min_seconds = MIN_MATCH_SECONDS if self.profiles else MIN_EMBED_SECONDS
        if duration < min_seconds:
            return previous, None
        embedding = _normalize(self.embedder.embed(audio))

        if self.profiles:
            best = max(self.profiles, key=lambda p: p.z(embedding))
            if best.z(embedding) >= -GUEST_Z or duration < GUEST_MIN_SECONDS:
                return best.id, embedding
        if duration < MIN_EMBED_SECONDS:
            return previous, None

        speaker = self._closest(embedding)
        if speaker is None:
            speaker = self._new_id()
            self._centroids[speaker] = np.zeros_like(embedding)
        self._centroids[speaker] += embedding * duration
        return speaker, embedding

    def record(
        self, segment_id: int, duration: float, embedding: np.ndarray | None, speaker: str | None
    ) -> None:
        """Remember a final segment's voice, for refine()."""
        self._items.append(_Item(segment_id, duration, embedding, speaker))

    def _is_profile(self, speaker: str | None) -> bool:
        return bool(speaker) and speaker.startswith("P")

    def refine(self, iterations: int = 10) -> dict[int, str | None]:
        """Re-cluster every segment with hindsight. Returns segment id -> speaker.

        Speaker ids are kept stable where possible, so names given during the
        meeting stay attached to the same voice.
        """
        # Profile matches are decided against enrolled voices: kept as they are.
        # Only the unknown voices are re-clustered.
        known = [
            it for it in self._items
            if it.embedding is not None and not self._is_profile(it.speaker)
        ]
        if known and self._centroids:
            self._recluster(known, iterations)
        self._fill_short_segments()
        return {it.segment_id: it.speaker for it in self._items}

    def _recluster(self, known: list[_Item], iterations: int) -> None:
        X = np.stack([it.embedding for it in known])
        w = np.array([it.duration for it in known])
        centroids = np.stack([_normalize(c) for c in self._centroids.values()])

        for _ in range(iterations):
            labels = np.argmax(X @ centroids.T, axis=1)
            centroids = self._recompute(X, w, labels, len(centroids))
            centroids = self._merge_close(centroids)
        labels = np.argmax(X @ centroids.T, axis=1)

        new_ids = self._stable_ids(known, labels)
        for it, label in zip(known, labels):
            it.speaker = new_ids[label]
        self._centroids = {
            new_ids[k]: centroids[k] * w[labels == k].sum() for k in range(len(centroids))
        }

    def _fill_short_segments(self) -> None:
        # Short segments: same speaker as the neighbouring segment before,
        # or after for those at the very start.
        previous = None
        for it in self._items:
            if it.embedding is not None:
                previous = it.speaker
            else:
                it.speaker = previous
        following = None
        for it in reversed(self._items):
            if it.embedding is not None:
                following = it.speaker
            elif it.speaker is None:
                it.speaker = following

    # -- internals ---------------------------------------------------------

    def _closest(self, embedding: np.ndarray) -> str | None:
        best, best_sim = None, self.threshold
        for speaker, centroid in self._centroids.items():
            sim = float(_normalize(centroid) @ embedding)
            if sim >= best_sim:
                best, best_sim = speaker, sim
        return best

    def _new_id(self) -> str:
        speaker = f"S{self._next}"
        self._next += 1
        return speaker

    @staticmethod
    def _recompute(X, w, labels, k) -> np.ndarray:
        centroids = []
        for c in range(k):
            members = labels == c
            if members.any():
                centroids.append(_normalize((X[members] * w[members, None]).sum(axis=0)))
        return np.stack(centroids)

    def _merge_close(self, centroids: np.ndarray) -> np.ndarray:
        """Merge the most similar pair of speakers while above merge_threshold."""
        while len(centroids) > 1:
            sims = centroids @ centroids.T
            np.fill_diagonal(sims, -1)
            i, j = np.unravel_index(np.argmax(sims), sims.shape)
            if sims[i, j] < self.merge_threshold:
                break
            merged = _normalize(centroids[i] + centroids[j])
            centroids = np.vstack([np.delete(centroids, [i, j], axis=0), merged])
        return centroids

    def _stable_ids(self, items: list[_Item], labels: np.ndarray) -> dict[int, str]:
        """Give each cluster the old id that covers most of its speech time."""
        overlap: dict[tuple[int, str], float] = {}
        for it, label in zip(items, labels):
            if it.speaker:
                key = (int(label), it.speaker)
                overlap[key] = overlap.get(key, 0.0) + it.duration
        ids: dict[int, str] = {}
        taken: set[str] = set()
        for (label, old), _ in sorted(overlap.items(), key=lambda kv: -kv[1]):
            if label not in ids and old not in taken:
                ids[label] = old
                taken.add(old)
        for label in sorted(set(int(l) for l in labels)):
            if label not in ids:
                ids[label] = self._new_id()
        return ids
