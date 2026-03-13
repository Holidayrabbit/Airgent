from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agents.context import AgentRunContext
from app.agents.registry import AgentRegistry
from app.api.schemas.agent import AgentRunRequest
from app.core.config import Settings
from app.core.errors import AppError
from app.memory.context_builder import ContextBuilder
from app.memory.store import LocalStore
from app.sessions.factory import SessionFactory

try:
    from agents import Runner
except ImportError:  # pragma: no cover
    Runner = None  # type: ignore[assignment]


@dataclass(frozen=True)
class AgentExecutionResult:
    request_id: str
    session_id: str
    agent_key: str
    output: str
    context: dict[str, Any]


class AgentRunnerService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: LocalStore,
        context_builder: ContextBuilder,
        session_factory: SessionFactory,
        agent_registry: AgentRegistry,
    ) -> None:
        self.settings = settings
        self.store = store
        self.context_builder = context_builder
        self.session_factory = session_factory
        self.agent_registry = agent_registry

    async def run(self, request: AgentRunRequest, *, request_id: str) -> AgentExecutionResult:
        if Runner is None:
            raise AppError(
                code="missing_dependency",
                message="openai-agents is not installed.",
                status_code=500,
            )

        session = self.session_factory.create(request.session_id)
        snapshot = self.context_builder.build(session_id=session.session_id, query=request.input)
        context = AgentRunContext(
            settings=self.settings,
            store=self.store,
            request_id=request_id,
            agent_key=request.agent_key,
            session_id=session.session_id,
            user_input=request.input,
            context_snapshot=snapshot,
        )
        self.store.append_message(
            session.session_id,
            role="user",
            content=request.input,
            agent_key=request.agent_key,
        )
        try:
            agent, runtime_spec = self.agent_registry.build(context)
            result = await Runner.run(
                starting_agent=agent,
                input=request.input,
                context=context,
                session=session,
                max_turns=request.max_turns or runtime_spec.max_turns,
            )
        except KeyError as exc:
            raise AppError(
                code="agent_not_found",
                message=f"Agent '{request.agent_key}' is not configured.",
                status_code=404,
                details={"agent_key": request.agent_key},
            ) from exc
        except Exception as exc:
            reason = str(exc).strip()
            raise AppError(
                code="agent_run_failed",
                message=f"Agent execution failed: {reason}" if reason else "Agent execution failed.",
                status_code=500,
                details={
                    "type": exc.__class__.__name__,
                    "reason": reason,
                },
            ) from exc

        final_output = self._stringify_output(result.final_output)
        self.store.append_message(
            session.session_id,
            role="assistant",
            content=final_output,
            agent_key=request.agent_key,
        )
        return AgentExecutionResult(
            request_id=request_id,
            session_id=session.session_id,
            agent_key=request.agent_key,
            output=final_output,
            context={
                "memory_hits": len(snapshot.memories),
                "recent_message_count": len(snapshot.recent_messages),
            },
        )

    @staticmethod
    def _stringify_output(output: Any) -> str:
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=True, default=str)
