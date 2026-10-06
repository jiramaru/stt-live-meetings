from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SAMPLE_RATE = 16_000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STT_", env_file=".env", extra="ignore")

    # Whisper model
    model_size: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    cpu_threads: int = 0  # 0 = let CTranslate2 decide
    beam_size: int = 5  # final segments; partials always use 1 (greedy) for speed
    languages: str = "fr,en"  # allowed for auto-detection, comma separated

    # Streaming / segmentation (seconds)
    step: float = 1.0  # how often the buffer is re-analysed
    min_silence: float = 1.0  # pause that closes a segment
    max_segment: float = 15.0  # force a cut beyond this length
    vad_threshold: float = 0.5

    # Speaker labels
    diarization: bool = True
    speaker_model: Path = Path("models/speaker/wespeaker_en_voxceleb_resnet34_LM.onnx")
    speaker_threshold: float = 0.5  # similarity to join a speaker; lower = fewer speakers
    speaker_merge_threshold: float = 0.7  # similarity to merge two speakers at the end

    # Keep each meeting's audio (16 kHz WAV, about 115 MB per hour) next to its
    # transcript: to listen back, and to re-run or tune the pipeline on it.
    save_audio: bool = True

    data_dir: Path = Path("data")

    @property
    def language_list(self) -> list[str]:
        return [v.strip() for v in self.languages.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
