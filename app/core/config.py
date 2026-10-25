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
        populate_by_name=True,
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
    # Either "provider/model-id" for a custom proxy model (e.g. "anthropic/claude-sonnet-4.5"
    # routed through OPENAI_BASE_URL) or any model id resolvable by the pi kernel
    # (e.g. "gpt-4o", "anthropic/claude-sonnet-4.5" with provider env keys).
    default_model: str = Field(
        default="anthropic/claude-sonnet-4.5",
        validation_alias=AliasChoices("AIRGENT_MODEL", "AIRGENT_DEFAULT_MODEL"),
    )
    default_max_turns: int = 12

    project_root: Path = Field(default_factory=Path.cwd)
    data_dir: Path = Field(default=Path.home() / ".airgent")
    db_path: Path | None = None
    skills_root: Path | None = None
    session_history_limit: int = 40
    transcript_context_limit: int = 6
    memory_search_limit: int = 5
    session_list_limit: int = 50

    # pi kernel
    pi_bin: str = Field(default="pi", validation_alias=AliasChoices("AIRGENT_PI_BIN", "PI_BIN"))
    pi_agent_dir: Path = Field(default=Path.home() / ".airgent" / "pi" / "agent")
    pi_session_dir: Path = Field(default=Path.home() / ".airgent" / "pi" / "sessions")
    pi_idle_grace_seconds: float = 0.4
    pi_request_timeout_seconds: float = 30.0
    pi_process_idle_timeout_seconds: float = 900.0

    def ensure_directories(self) -> None:
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.skills_root.mkdir(parents=True, exist_ok=True)

    @property
    def pi_api_name(self) -> str:
        """Map the legacy OpenAI API mode onto a pi model api identifier."""
        if self.openai_api_mode.lower().startswith("responses"):
            return "openai-responses"
        return "openai-completions"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.project_root = settings.project_root.expanduser().resolve()
    settings.data_dir = settings.data_dir.expanduser().resolve()
    settings.db_path = (settings.db_path or settings.data_dir / "airgent.db").expanduser().resolve()
    settings.skills_root = (
        (settings.skills_root or settings.project_root / ".agents" / "skills").expanduser().resolve()
    )
    settings.pi_agent_dir = settings.pi_agent_dir.expanduser().resolve()
    settings.pi_session_dir = settings.pi_session_dir.expanduser().resolve()
    settings.ensure_directories()
    return settings
