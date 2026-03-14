from __future__ import annotations

from textwrap import dedent
from typing import Any

from app.agents.context import AgentRunContext

try:
    from agents import Agent, RunContextWrapper
except ImportError:  # pragma: no cover
    Agent = object  # type: ignore[assignment]

    class RunContextWrapper:  # type: ignore[override]
        context: AgentRunContext


ROOT_ASSISTANT_INSTRUCTIONS = """
You are Airgent, a personal AI operating system for an individual user.

Your responsibilities:
1. Answer the user's request directly when no tool is needed.
2. Use file tools when the task requires reading or changing project files.
3. Use the bash tool only for local project commands when shell execution is clearly necessary.
4. Use memory tools to recall stable preferences, ongoing projects, and prior commitments.
5. Use skill tools when a task needs a reusable workflow or domain playbook.
6. Keep responses concise, practical, and honest about uncertainty.

Memory policy:
- Search memory before assuming prior preferences or long-running context.
- Save durable facts only when the user explicitly asks to remember them, or when a preference/project detail is clearly stable and useful later.
- Do not store secrets, credentials, or one-off ephemeral details unless the user explicitly requests it.

Skill policy:
- Discover relevant skills with `list_skills`.
- Load a skill with `load_skill` only when it clearly matches the task.
- After loading a skill, follow it.

File policy:
- All file access is restricted to the current project root.
- Read before editing when current file contents matter.
- Use `create_file` for new files and `edit_file` for targeted updates.

Shell policy:
- Use `run_bash_command` for non-destructive local commands only.
- Stay inside the current project root.
- Never attempt dangerous commands such as deleting files or escalating privileges.

Behavior rules:
- Never fabricate tool results or prior history.
- Treat the local transcript as the source of short-term memory.
- Use long-term memory to personalize, not to override the current request.
- If memory seems stale or conflicting, call it out and ask for confirmation.
""".strip()


class _StaticRunContextWrapper:
    def __init__(self, context: AgentRunContext) -> None:
        self.context = context


def _normalize_instructions(text: str) -> str:
    return dedent(text).strip()


def _render_memory_block(context: AgentRunContext) -> str:
    if not context.context_snapshot.memories:
        return "No relevant long-term memory was found for this request."

    lines = ["Relevant long-term memory:"]
    for memory in context.context_snapshot.memories:
        tags = f" [tags: {', '.join(memory.tags)}]" if memory.tags else ""
        lines.append(f"- {memory.content}{tags}")
    return "\n".join(lines)


def _render_runtime_block(context: AgentRunContext) -> str:
    return "\n".join(
        [
            "Runtime context:",
            f"- project_root: {context.settings.project_root}",
            f"- skills_root: {context.settings.skills_root}",
            f"- session_id: {context.session_id}",
            _render_memory_block(context),
        ]
    )


def compose_instructions(base_instructions: str, context: AgentRunContext) -> str:
    return "\n\n".join(
        [
            _normalize_instructions(base_instructions),
            _render_runtime_block(context),
        ]
    )


def resolve_instructions(
    context: AgentRunContext,
    *,
    instructions: str | None = None,
    instructions_builder: str | None = None,
) -> str:
    if instructions is not None:
        return compose_instructions(instructions, context)

    if not instructions_builder:
        raise ValueError("Agent config must define either instructions or instructions_builder.")

    builder = globals().get(instructions_builder)
    if not callable(builder):
        raise AttributeError(f"Unknown instructions builder: {instructions_builder}")
    return builder(_StaticRunContextWrapper(context), None)


def build_root_instructions(
    wrapper: RunContextWrapper[AgentRunContext],
    _: Agent[Any] | None = None,
) -> str:
    context = wrapper.context
    return compose_instructions(ROOT_ASSISTANT_INSTRUCTIONS, context)
