from __future__ import annotations

from app.agents.context import AgentRunContext

try:
    from agents import RunContextWrapper, function_tool
except ImportError:  # pragma: no cover
    def function_tool(func):  # type: ignore[misc]
        return func

    class RunContextWrapper:  # type: ignore[override]
        context: AgentRunContext


@function_tool
async def search_memory(
    wrapper: RunContextWrapper[AgentRunContext],
    query: str,
) -> dict[str, object]:
    """Search long-term memory when the current request depends on user history, preferences, or prior commitments."""

    memories = wrapper.context.store.search_memories(
        query,
        limit=wrapper.context.settings.memory_search_limit,
    )
    return {
        "memories": [
            {
                "id": memory.id,
                "content": memory.content,
                "tags": memory.tags,
                "source_session_id": memory.source_session_id,
                "created_at": memory.created_at,
            }
            for memory in memories
        ]
    }


@function_tool
async def remember_note(
    wrapper: RunContextWrapper[AgentRunContext],
    content: str,
    tags: list[str] | None = None,
) -> dict[str, object]:
    """Persist a durable user fact, preference, project note, or commitment for future conversations."""

    record = wrapper.context.store.add_memory(
        content,
        tags=tags,
        source_session_id=wrapper.context.session_id,
    )
    return {
        "memory": {
            "id": record.id,
            "content": record.content,
            "tags": record.tags,
            "source_session_id": record.source_session_id,
            "created_at": record.created_at,
        }
    }
