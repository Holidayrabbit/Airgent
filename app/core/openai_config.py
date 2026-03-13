from __future__ import annotations

from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.errors import AppError

try:
    from agents import (
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )
except ImportError:  # pragma: no cover
    set_default_openai_api = None  # type: ignore[assignment]
    set_default_openai_client = None  # type: ignore[assignment]
    set_tracing_disabled = None  # type: ignore[assignment]


def configure_openai_sdk(settings: Settings) -> None:
    if set_default_openai_client is None or set_default_openai_api is None or set_tracing_disabled is None:
        return

    if not settings.openai_api_key:
        raise AppError(
            code="missing_openai_api_key",
            message="OPENAI_API_KEY is not configured. Set it in your environment or .env file.",
            status_code=500,
        )

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api(settings.openai_api_mode)
    if settings.openai_agents_disable_tracing:
        set_tracing_disabled(True)
