from __future__ import annotations

from dataclasses import dataclass

from app.agents.registry import AgentRegistry
from app.agents.runner import AgentRunnerService
from app.core.config import Settings, get_settings
from app.core.openai_config import configure_openai_sdk
from app.cron.service import CronService
from app.memory.context_builder import ContextBuilder
from app.memory.store import LocalStore
from app.sessions.factory import SessionFactory
from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    store: LocalStore
    context_builder: ContextBuilder
    session_factory: SessionFactory
    tool_registry: ToolRegistry
    agent_registry: AgentRegistry
    runner: AgentRunnerService
    cron: CronService


def build_services(settings: Settings | None = None) -> AppServices:
    resolved_settings = settings or get_settings()
    configure_openai_sdk(resolved_settings)
    store = LocalStore(resolved_settings.db_path)
    store.initialize_cron()
    context_builder = ContextBuilder(
        store=store,
        transcript_limit=resolved_settings.transcript_context_limit,
        memory_limit=resolved_settings.memory_search_limit,
    )
    session_factory = SessionFactory(
        store=store,
        history_limit=resolved_settings.session_history_limit,
    )
    tool_registry = ToolRegistry()
    agent_registry = AgentRegistry(tool_registry)
    runner = AgentRunnerService(
        settings=resolved_settings,
        store=store,
        context_builder=context_builder,
        session_factory=session_factory,
        agent_registry=agent_registry,
    )
    cron = CronService(store=store, runner=runner)
    return AppServices(
        settings=resolved_settings,
        store=store,
        context_builder=context_builder,
        session_factory=session_factory,
        tool_registry=tool_registry,
        agent_registry=agent_registry,
        runner=runner,
        cron=cron,
    )
