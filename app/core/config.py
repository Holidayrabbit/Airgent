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

    project_root: Path = Field(default_factory=Path.cwd)
    data_dir: Path = Field(default=Path.home() / ".airgent")
    db_path: Path | None = None
    skills_root: Path | None = None
    skills_user_root: Path | None = None
    memory_search_limit: int = 5
    session_list_limit: int = 50

    # pi kernel
    pi_bin: str = Field(default="pi", validation_alias=AliasChoices("AIRGENT_PI_BIN", "PI_BIN"))
    pi_agent_dir: Path = Field(default=Path.home() / ".airgent" / "pi" / "agent")
    pi_session_dir: Path = Field(default=Path.home() / ".airgent" / "pi" / "sessions")
    pi_request_timeout_seconds: float = 30.0

    # ------------------------------------------------------------------
    # Optional capabilities — every feature below defaults to OFF so the
    # baseline deployment stays a plain pi-agent wrapper.
    # ------------------------------------------------------------------

    # Sandbox: run the pi kernel subprocess inside an OS/container sandbox.
    # provider: "none" | "seatbelt" (macOS sandbox-exec) | "docker"
    sandbox_provider: str = Field(
        default="none",
        validation_alias=AliasChoices("AIRGENT_SANDBOX", "AIRGENT_SANDBOX_PROVIDER"),
    )
    sandbox_image: str = Field(default="node:20-bookworm-slim")
    sandbox_network: bool = Field(default=False, validation_alias="AIRGENT_SANDBOX_NETWORK")
    sandbox_docker_args: str = Field(
        default="", description="Extra docker run args (comma separated)."
    )

    # Skill registry: enable/disable individual skills on top of pi's native
    # .agents/skills discovery. Disabled by default = pure native discovery.
    skills_registry_enabled: bool = Field(
        default=False, validation_alias="AIRGENT_SKILLS_REGISTRY"
    )
    skills_registry_path: Path | None = None

    # MCP (Model Context Protocol): bridge external MCP servers into pi tools.
    mcp_enabled: bool = Field(default=False, validation_alias="AIRGENT_MCP")
    mcp_config_path: Path | None = None

    # Sub-agents: expose a spawn_subagent tool so the root agent can delegate.
    subagents_enabled: bool = Field(default=False, validation_alias="AIRGENT_SUBAGENTS")
    subagents_max_depth: int = Field(default=1, ge=1, le=3)
    subagents_model: str | None = None

    # Guardrails: policy-driven tool-call filtering inside the kernel.
    guardrails_enabled: bool = Field(default=False, validation_alias="AIRGENT_GUARDRAILS")
    guardrails_config_path: Path | None = None

    # Hooks: lifecycle commands (before_run / after_run) fed JSON on stdin.
    hooks_enabled: bool = Field(default=False, validation_alias="AIRGENT_HOOKS")
    hooks_config_path: Path | None = None

    # Memory upgrades (mem0/Letta/Zep-inspired):
    # auto_extract: harvest durable facts from each user message.
    memory_auto_extract: bool = Field(default=False, validation_alias="AIRGENT_MEMORY_AUTO_EXTRACT")
    # decay: importance halves every N days of no access (0 = off).
    memory_decay_halflife_days: int = Field(default=0, validation_alias="AIRGENT_MEMORY_DECAY_DAYS")
    # core blocks: Letta-style always-in-context memory blocks.
    memory_core_blocks: bool = Field(default=False, validation_alias="AIRGENT_MEMORY_CORE_BLOCKS")
    # dedup: skip storing a memory when near-identical content exists.
    memory_dedup: bool = Field(default=False, validation_alias="AIRGENT_MEMORY_DEDUP")

    # Observability: record per-run trace events in SQLite.
    tracing_enabled: bool = Field(default=False, validation_alias="AIRGENT_TRACING")

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

    @property
    def skills_user_dir(self) -> Path:
        return self.skills_user_root or (Path.home() / ".agents" / "skills")

    @property
    def mcp_config(self) -> Path:
        return self.mcp_config_path or (self.data_dir / "mcp.json")

    @property
    def guardrails_config(self) -> Path:
        return self.guardrails_config_path or (self.data_dir / "guardrails.json")

    @property
    def hooks_config(self) -> Path:
        return self.hooks_config_path or (self.data_dir / "hooks.json")

    @property
    def skills_registry_file(self) -> Path:
        return self.skills_registry_path or (self.data_dir / "skills.json")

    def enabled_features(self) -> dict[str, bool | str]:
        """Feature flags reported by /api/v1/features and `airgent status`."""
        return {
            "sandbox": self.sandbox_provider,
            "skills_registry": self.skills_registry_enabled,
            "mcp": self.mcp_enabled,
            "subagents": self.subagents_enabled,
            "guardrails": self.guardrails_enabled,
            "hooks": self.hooks_enabled,
            "memory_auto_extract": self.memory_auto_extract,
            "memory_decay": self.memory_decay_halflife_days > 0,
            "memory_core_blocks": self.memory_core_blocks,
            "memory_dedup": self.memory_dedup,
            "tracing": self.tracing_enabled,
        }


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
    settings.skills_registry_path = (
        settings.skills_registry_file.expanduser().resolve()
        if settings.skills_registry_path
        else None
    )
    settings.ensure_directories()
    return settings
