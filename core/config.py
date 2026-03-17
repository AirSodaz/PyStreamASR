from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_ENV_FILE = Path(".env")


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

    model_config = SettingsConfigDict(
        env_file=".env",
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
