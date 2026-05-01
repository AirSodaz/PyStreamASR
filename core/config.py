import os
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def default_asr_inference_workers() -> int:
    """Return the default per-process ASR inference worker count."""
    return max(1, (os.cpu_count() or 2) // 2)


class Settings(BaseSettings):
    """Application settings using Pydantic BaseSettings.

    Loads configuration from environment variables or .env file.
    """
    PROJECT_NAME: str = "PyStreamASR"
    MYSQL_DATABASE_URL: str
    MODEL_PATH: str
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"
    RETURN_TRANSCRIPTION: bool = True
    AUDIO_INPUT_FORMAT: str = "alaw"
    AUDIO_SOURCE_RATE: int = 8000
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = Field(default=8000, ge=1, le=65535)
    APP_WORKERS: int = Field(default=1, ge=1)
    ASR_INFERENCE_WORKERS: int = Field(default_factory=default_asr_inference_workers, ge=1)
    ASR_INFERENCE_QUEUE_SIZE: int | None = Field(default=None, ge=0)
    ASR_INFERENCE_QUEUE_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0)

    @field_validator("APP_HOST")
    @classmethod
    def validate_app_host(cls, value: str) -> str:
        """Ensure the configured host is not blank.

        Args:
            value: Raw host value from the environment.

        Returns:
            The normalized host value.

        Raises:
            ValueError: If the host is empty after trimming.
        """
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("APP_HOST cannot be empty")
        return normalized_value

    @model_validator(mode="after")
    def populate_asr_inference_defaults(self) -> "Settings":
        """Populate defaults that depend on other settings."""
        if self.ASR_INFERENCE_QUEUE_SIZE is None:
            self.ASR_INFERENCE_QUEUE_SIZE = self.ASR_INFERENCE_WORKERS * 4
        return self

    model_config = SettingsConfigDict(
        env_file=str(DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


def get_settings(env_file: str | Path | None = DEFAULT_ENV_FILE) -> Settings:
    """Load settings from the configured environment file.

    Args:
        env_file: Optional override for the environment file path.

    Returns:
        A validated settings instance.
    """
    if env_file is None:
        return Settings()

    return Settings(_env_file=str(env_file))


settings = get_settings()
