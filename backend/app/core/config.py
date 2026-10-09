from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration.
    Neutral project naming is maintained throughout.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Application Info
    APP_NAME: str = "Project Aurora Backend"
    APP_VERSION: str = "0.3.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Server Host / Port
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # CORS Configuration
    CORS_ORIGINS: List[str] = ["*"]

    # AI Gateway Settings
    AI_PROVIDER: str = "gemini"
    AI_MODEL: str = "gemini-3.8-flash"
    AI_DEFAULT_MODEL: str = "gemini-3.8-flash"
    AI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    AI_BASE_URL: str = "https://api.openai.com/v1"
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    AI_TIMEOUT: float = 30.0
    AI_MAX_TOKENS: Optional[int] = 1024
    AI_TEMPERATURE: float = 0.7

    # Memory & Database Configuration
    DATABASE_PATH: str = "data/aurora.db"
    MEMORY_DB_PATH: str = "data/aurora.db"

    @property
    def resolved_api_key(self) -> str:
        """Helper to resolve provider key from AI_API_KEY or provider-specific keys based on active provider."""
        provider = self.AI_PROVIDER.lower().strip()
        if provider == "gemini":
            return self.GEMINI_API_KEY or self.AI_API_KEY
        elif provider == "openai":
            return self.OPENAI_API_KEY or self.AI_API_KEY
        return self.AI_API_KEY or self.GEMINI_API_KEY or self.OPENAI_API_KEY

    @property
    def resolved_model(self) -> str:
        """Helper to resolve the active model name."""
        return self.AI_MODEL or self.AI_DEFAULT_MODEL


settings = Settings()
