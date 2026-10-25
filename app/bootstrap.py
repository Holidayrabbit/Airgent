from __future__ import annotations

from dataclasses import dataclass

from app.agents.runner import AgentRunnerService
from app.core.config import Settings, get_settings
from app.cron.service import CronService
from app.kernel.manager import PiKernel
from app.memory.context_builder import ContextBuilder
from app.memory.store import LocalStore


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    store: LocalStore
    context_builder: ContextBuilder
    kernel: PiKernel
    runner: AgentRunnerService
    cron: CronService


def build_services(settings: Settings | None = None) -> AppServices:
    resolved_settings = settings or get_settings()
    store = LocalStore(resolved_settings.db_path)
    store.initialize_cron()
    context_builder = ContextBuilder(
        store=store,
        transcript_limit=resolved_settings.transcript_context_limit,
        memory_limit=resolved_settings.memory_search_limit,
    )
    kernel = PiKernel(settings=resolved_settings, store=store)
    kernel.prepare()
    runner = AgentRunnerService(
        settings=resolved_settings,
        store=store,
        context_builder=context_builder,
        kernel=kernel,
    )
    cron = CronService(store=store, runner=runner)
    return AppServices(
        settings=resolved_settings,
        store=store,
        context_builder=context_builder,
        kernel=kernel,
        runner=runner,
        cron=cron,
    )
