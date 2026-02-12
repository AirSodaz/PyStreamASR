from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings using Pydantic BaseSettings.

    Loads configuration from environment variables or .env file.
    """
    PROJECT_NAME: str = "PyStreamASR"
    MYSQL_DATABASE_URL: str
    REDIS_URL: str
    MODEL_PATH: str
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"
    RETURN_TRANSCRIPTION: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )


settings = Settings()
