from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Airgent"
    environment: str = "local"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_BASE_URL", "OPENAI_API_BASE"),
    )
    openai_api_mode: str = Field(
        default="chat_completions",
        validation_alias=AliasChoices("OPENAI_API_MODE", "AIRGENT_OPENAI_API_MODE"),
    )

    default_agent_key: str = "root_assistant"
    default_model: str = "gpt-4o"
    default_max_turns: int = 12

    data_dir: Path = Field(default=Path.home() / ".airgent")
    db_path: Path | None = None
    skills_root: Path = Path(__file__).resolve().parents[2] / "skills"
    session_history_limit: int = 40
    transcript_context_limit: int = 6
    memory_search_limit: int = 5
    session_list_limit: int = 50
    openai_agents_disable_tracing: bool = True

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.skills_root.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir = settings.data_dir.expanduser().resolve()
    settings.db_path = (settings.db_path or settings.data_dir / "airgent.db").expanduser().resolve()
    settings.skills_root = settings.skills_root.expanduser().resolve()
    settings.ensure_directories()
    return settings
