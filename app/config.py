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
    beam_size: int = 1
    languages: str = "fr,en"  # allowed for auto-detection, comma separated

    # Streaming / segmentation (seconds)
    step: float = 1.0  # how often the buffer is re-analysed
    min_silence: float = 0.6  # pause that closes a segment
    max_segment: float = 15.0  # force a cut beyond this length
    vad_threshold: float = 0.5

    data_dir: Path = Path("data")

    @property
    def language_list(self) -> list[str]:
        return [v.strip() for v in self.languages.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
