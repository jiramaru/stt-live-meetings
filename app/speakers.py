"""Speaker labelling ("who spoke when") from a single microphone.

Each final segment is turned into a voice embedding: a vector that is close
for two recordings of the same voice and far apart for different voices.

- Live: every new segment is compared with the speakers met so far and joins
  the closest one, or starts a new speaker when nobody is similar enough.
- At the end of the meeting `refine()` re-clusters all segments with the full
  picture, fixing early mistakes (a speaker split in two at the start, a short
  segment given to the wrong person).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .config import SAMPLE_RATE

log = logging.getLogger(__name__)

MIN_EMBED_SECONDS = 1.0  # shorter segments carry too little voice to identify


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

    _items: list[_Item] = field(default_factory=list)
    _centroids: dict[str, np.ndarray] = field(default_factory=dict)  # sum of embeddings
    _next: int = 1

    def assign(self, segment_id: int, audio: np.ndarray) -> str | None:
        """Label a new segment live. Returns a speaker id such as 'S1'."""
        duration = len(audio) / SAMPLE_RATE
        embedding = None
        if duration >= MIN_EMBED_SECONDS:
            embedding = _normalize(self.embedder.embed(audio))

        if embedding is None:
            # Too short to tell ("oui", "d'accord"): most often the same person
            # carries on, or it is a quick reply refine() will settle.
            speaker = self._items[-1].speaker if self._items else None
        else:
            speaker = self._closest(embedding)
            if speaker is None:
                speaker = self._new_id()
                self._centroids[speaker] = np.zeros_like(embedding)
            self._centroids[speaker] += embedding * duration

        self._items.append(_Item(segment_id, duration, embedding, speaker))
        return speaker

    def refine(self, iterations: int = 10) -> dict[int, str | None]:
        """Re-cluster every segment with hindsight. Returns segment id -> speaker.

        Speaker ids are kept stable where possible, so names given during the
        meeting stay attached to the same voice.
        """
        known = [it for it in self._items if it.embedding is not None]
        if not known:
            return {it.segment_id: it.speaker for it in self._items}

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

        return {it.segment_id: it.speaker for it in self._items}

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
