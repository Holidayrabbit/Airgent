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
    embedder = None
    if resolved_settings.memory_embeddings_enabled and resolved_settings.openai_base_url:
        from app.memory.embeddings import EmbeddingClient

        embedder = EmbeddingClient(
            base_url=resolved_settings.openai_base_url,
            api_key=resolved_settings.openai_api_key,
            model=resolved_settings.embedding_model,
        )
    store = LocalStore(resolved_settings.db_path, dedup=resolved_settings.memory_dedup, embedder=embedder)
    store.initialize_cron()
    context_builder = ContextBuilder(
        store=store,
        memory_limit=resolved_settings.memory_search_limit,
        decay_halflife_days=resolved_settings.memory_decay_halflife_days,
        core_blocks=resolved_settings.memory_core_blocks,
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
