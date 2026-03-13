from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from app.api.schemas.agent import AgentRunRequest
from app.bootstrap import build_services
from app.core.config import get_settings
from app.core.errors import AppError
from app.tui import run_tui

app = typer.Typer(
    help="Airgent: local-first personal agent runtime with CLI, API, and WebUI.",
    no_args_is_help=True,
)
sessions_app = typer.Typer(help="Inspect or delete saved sessions.")
memory_app = typer.Typer(help="Inspect or manage long-term memory.")
app.add_typer(sessions_app, name="sessions")
app.add_typer(memory_app, name="memory")


async def _run_once(message: str, *, session_id: str | None, agent_key: str, max_turns: int | None) -> None:
    services = build_services()
    result = await services.runner.run(
        AgentRunRequest(
            input=message,
            session_id=session_id,
            agent_key=agent_key,
            max_turns=max_turns,
        ),
        request_id="cli",
    )
    typer.echo(f"[session {result.session_id}]")
    typer.echo(result.output)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, min=1, max=65535, help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto reload."),
) -> None:
    """Start the HTTP API and WebUI."""

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise AppError("missing_dependency", "uvicorn is not installed.", 500) from exc

    uvicorn.run("app.main:app", host=host, port=port, reload=reload)


@app.command()
def chat(
    message: Annotated[str | None, typer.Argument(help="Run a single prompt. Omit for interactive chat.")] = None,
    session_id: str | None = typer.Option(None, "--session", help="Reuse an existing session id."),
    agent_key: str = typer.Option(get_settings().default_agent_key, "--agent", help="Agent config key."),
    max_turns: int | None = typer.Option(None, "--max-turns", min=1, max=50, help="Max SDK turns."),
) -> None:
    """Chat with Airgent in one-shot or interactive mode."""

    if message is not None:
        asyncio.run(_run_once(message, session_id=session_id, agent_key=agent_key, max_turns=max_turns))
        return

    services = build_services()
    active_session_id = session_id
    typer.echo("Interactive mode. Use /exit to quit, /new to start a new session.")
    while True:
        prompt = typer.prompt("You", prompt_suffix=" > ").strip()
        if not prompt:
            continue
        if prompt in {"/exit", "/quit"}:
            break
        if prompt == "/new":
            active_session_id = None
            typer.echo("Started a new session.")
            continue
        result = asyncio.run(
            services.runner.run(
                AgentRunRequest(
                    input=prompt,
                    session_id=active_session_id,
                    agent_key=agent_key,
                    max_turns=max_turns,
                ),
                request_id="cli",
            )
        )
        active_session_id = result.session_id
        typer.echo(f"Airgent [{active_session_id}] > {result.output}")


@app.command()
def tui(
    agent_key: str = typer.Option(get_settings().default_agent_key, "--agent", help="Agent config key."),
    max_turns: int | None = typer.Option(None, "--max-turns", min=1, max=50, help="Max SDK turns."),
) -> None:
    """Open the full-screen terminal UI."""

    services = build_services()
    asyncio.run(run_tui(services=services, agent_key=agent_key, max_turns=max_turns))


@sessions_app.command("list")
def list_sessions() -> None:
    """List saved sessions."""

    services = build_services()
    sessions = services.store.list_sessions(limit=services.settings.session_list_limit)
    if not sessions:
        typer.echo("No saved sessions.")
        return
    for session in sessions:
        preview = session.last_message or ""
        typer.echo(f"{session.session_id}  {session.title}  {preview[:80]}")


@sessions_app.command("show")
def show_session(session_id: str) -> None:
    """Show a saved session transcript."""

    services = build_services()
    messages = services.store.get_messages(session_id)
    if not messages:
        raise typer.Exit(code=1)
    for message in messages:
        typer.echo(f"{message.role}: {message.content}")


@sessions_app.command("delete")
def delete_session(session_id: str) -> None:
    """Delete a saved session."""

    services = build_services()
    services.store.delete_session(session_id)
    typer.echo(f"Deleted {session_id}")


@memory_app.command("list")
def list_memory(limit: int = typer.Option(20, min=1, max=100)) -> None:
    """List recent memory records."""

    services = build_services()
    memories = services.store.list_memories(limit=limit)
    if not memories:
        typer.echo("No memory records.")
        return
    for record in memories:
        tags = f" [{', '.join(record.tags)}]" if record.tags else ""
        typer.echo(f"{record.id}  {record.content}{tags}")


@memory_app.command("search")
def search_memory(query: str, limit: int = typer.Option(10, min=1, max=50)) -> None:
    """Search saved memory."""

    services = build_services()
    records = services.store.search_memories(query, limit=limit)
    if not records:
        typer.echo("No matching memory.")
        return
    for record in records:
        tags = f" [{', '.join(record.tags)}]" if record.tags else ""
        typer.echo(f"{record.id}  {record.content}{tags}")


@memory_app.command("add")
def add_memory(
    content: str,
    tags: str = typer.Option("", help="Comma-separated tags."),
    source_session_id: str | None = typer.Option(None, help="Optional source session id."),
) -> None:
    """Add a memory record manually."""

    services = build_services()
    record = services.store.add_memory(
        content,
        tags=[tag.strip() for tag in tags.split(",") if tag.strip()],
        source_session_id=source_session_id,
    )
    typer.echo(f"Saved memory {record.id}")


def main() -> None:
    app()
