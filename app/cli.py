from __future__ import annotations

import asyncio
import json
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
cron_app = typer.Typer(help="Manage scheduled cron jobs.")
skills_app = typer.Typer(help="Inspect and toggle skills (requires AIRGENT_SKILLS_REGISTRY=1).")
app.add_typer(sessions_app, name="sessions")
app.add_typer(memory_app, name="memory")
app.add_typer(cron_app, name="cron")
app.add_typer(skills_app, name="skills")


async def _run_once(
    message: str, *, session_id: str | None, agent_key: str, plan: bool = False
) -> None:
    services = build_services()
    try:
        result = await services.runner.run(
            AgentRunRequest(
                input=message,
                session_id=session_id,
                agent_key=agent_key,
                plan=plan,
            ),
            request_id="cli",
        )
        typer.echo(f"[session {result.session_id}]")
        typer.echo(result.output)
    finally:
        await services.kernel.shutdown()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(10304, min=1, max=65535, help="Bind port."),
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
    plan: bool = typer.Option(False, "--plan", help="Plan mode: read-only run ending in a proposal."),
) -> None:
    """Chat with Airgent in one-shot or interactive mode."""

    if message is not None:
        asyncio.run(_run_once(message, session_id=session_id, agent_key=agent_key, plan=plan))
        return

    services = build_services()
    active_session_id = session_id
    typer.echo("Interactive mode. Use /exit to quit, /new to start a new session.")
    try:
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
                    ),
                    request_id="cli",
                )
            )
            active_session_id = result.session_id
            typer.echo(f"Airgent [{active_session_id}] > {result.output}")
    finally:
        asyncio.run(services.kernel.shutdown())


@app.command()
def tui(
    agent_key: str = typer.Option(get_settings().default_agent_key, "--agent", help="Agent config key."),
) -> None:
    """Open the full-screen terminal UI."""

    services = build_services()
    asyncio.run(run_tui(services=services, agent_key=agent_key))


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


@sessions_app.command("rewind")
def rewind_session(
    session_id: str,
    entry_id: str | None = typer.Option(None, "--entry-id", help="Fork entry id (skip the picker)."),
) -> None:
    """Rewind a session to an earlier user message (pi fork checkpoint)."""

    services = build_services()
    try:
        if entry_id is None:
            points = asyncio.run(services.runner.list_rewind_points(session_id))
            if not points:
                typer.echo("No rewind points in this session.")
                return
            for index, point in enumerate(points, start=1):
                preview = " ".join(str(point.get("text") or "").split())[:70]
                typer.echo(f"{index}. {preview}  (entry {point.get('entryId')})")
            choice = typer.prompt("Rewind to which message (number)", type=int)
            if not 1 <= choice <= len(points):
                typer.echo("Invalid choice.", err=True)
                raise typer.Exit(code=1)
            entry_id = str(points[choice - 1].get("entryId") or "")
        result = asyncio.run(services.runner.rewind(session_id, entry_id))
    finally:
        asyncio.run(services.kernel.shutdown())
    preview = " ".join(result.get("text", "").split())[:80]
    typer.echo(f"Rewound session {session_id} to: {preview}")


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
def search_memory(
    query: str,
    limit: int = typer.Option(10, min=1, max=50),
    format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Search saved memory."""

    services = build_services()
    records = services.store.search_memories(query, limit=limit)
    if format == "json":
        typer.echo(
            json.dumps(
                [
                    {
                        "id": record.id,
                        "content": record.content,
                        "tags": record.tags,
                        "created_at": record.created_at,
                    }
                    for record in records
                ],
                ensure_ascii=False,
            )
        )
        return
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
    kind: str = typer.Option("fact", help="Memory kind: fact, preference, profile, project, event."),
    importance: float = typer.Option(0.5, min=0.0, max=1.0, help="Importance used by ranked search."),
    source_session_id: str | None = typer.Option(None, help="Optional source session id."),
    format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Add a memory record manually."""

    services = build_services()
    record = services.store.add_memory(
        content,
        tags=[tag.strip() for tag in tags.split(",") if tag.strip()],
        source_session_id=source_session_id,
        kind=kind,
        importance=importance,
    )
    if format == "json":
        typer.echo(
            json.dumps(
                {
                    "id": record.id,
                    "content": record.content,
                    "tags": record.tags,
                    "created_at": record.created_at,
                },
                ensure_ascii=False,
            )
        )
        return
    typer.echo(f"Saved memory {record.id}")


# ------------------------------------------------------------------
# Cron commands
# ------------------------------------------------------------------
@cron_app.command("list")
def cron_list() -> None:
    """List all scheduled jobs."""

    services = build_services()
    jobs = services.cron.list_jobs()
    if not jobs:
        typer.echo("No scheduled jobs.")
        return
    for j in jobs:
        status = "enabled" if j.enabled else "paused"
        next_run = j.next_run_at or "—"
        typer.echo(f"{j.id}  [{status}] {j.name}  agent={j.agent_key}  schedule={j.schedule_kind.value}:{j.schedule_value}  next={next_run}")


@cron_app.command("create")
def cron_create(
    name: str,
    input: str = typer.Option(..., prompt=True, help="Prompt input sent to the agent"),
    agent_key: str = typer.Option("root_assistant", help="Agent config key."),
    schedule_kind: str = typer.Option("cron", help="Schedule type: cron, once, interval."),
    schedule_value: str = typer.Option(..., prompt=True, help="Cron expr (e.g. '0 * * * *'), 'once', or interval seconds."),
    enabled: bool = typer.Option(True, help="Start enabled immediately."),
) -> None:
    """Create a new scheduled job."""

    from app.cron.service import JobRecord, ScheduleKind as SK
    kind = SK(schedule_kind)
    services = build_services()
    record = JobRecord(
        name=name,
        agent_key=agent_key,
        input=input,
        schedule_kind=kind,
        schedule_value=schedule_value,
        enabled=enabled,
        one_shot=(schedule_kind == "once"),
    )
    job = services.cron.create_job(record)
    typer.echo(f"Created job {job.id}: {job.name}  next_run={job.next_run_at}")


@cron_app.command("delete")
def cron_delete(job_id: str) -> None:
    """Delete a scheduled job."""

    services = build_services()
    deleted = services.cron.delete_job(job_id)
    if deleted:
        typer.echo(f"Deleted {job_id}")
    else:
        typer.echo(f"Job {job_id} not found", err=True)
        raise typer.Exit(code=1)


@cron_app.command("pause")
def cron_pause(job_id: str) -> None:
    """Pause a scheduled job."""

    services = build_services()
    ok = services.cron.pause_job(job_id)
    if ok:
        typer.echo(f"Paused {job_id}")
    else:
        typer.echo(f"Job {job_id} not found", err=True)
        raise typer.Exit(code=1)


@cron_app.command("resume")
def cron_resume(job_id: str) -> None:
    """Resume a paused job."""

    services = build_services()
    ok = services.cron.resume_job(job_id)
    if ok:
        typer.echo(f"Resumed {job_id}")
    else:
        typer.echo(f"Job {job_id} not found", err=True)
        raise typer.Exit(code=1)


@cron_app.command("trigger")
def cron_trigger(job_id: str) -> None:
    """Immediately fire a job (skipping its schedule)."""

    services = build_services()
    job = services.cron.get_job(job_id)
    if job is None:
        typer.echo(f"Job {job_id} not found", err=True)
        raise typer.Exit(code=1)
    services.cron.trigger_job(job_id)
    typer.echo(f"Triggered {job_id} ({job.name}) — running in background.")


@app.command("mcp-serve")
def mcp_serve() -> None:
    """Serve Airgent as an MCP stdio server (memory, sessions, agent_run)."""

    from app.mcp_server import main as mcp_main

    mcp_main()


@app.command()
def status() -> None:
    """Show Airgent configuration and enabled optional features."""

    services = build_services()
    settings = services.settings
    typer.echo(f"{settings.app_name} ({settings.environment})")
    typer.echo(f"  workspace : {settings.project_root}")
    typer.echo(f"  data dir  : {settings.data_dir}")
    typer.echo(f"  model     : {settings.default_model}")
    typer.echo(f"  kernel    : {settings.pi_bin} (agent dir {settings.pi_agent_dir})")
    typer.echo("Features:")
    for name, state in settings.enabled_features().items():
        mark = "enabled " if state not in {False, "none", ""} else "disabled"
        typer.echo(f"  [{mark}] {name}" + (f" ({state})" if isinstance(state, str) and state else ""))


@app.command()
def cost() -> None:
    """Show cumulative recorded token/cost usage (requires AIRGENT_COST_TRACKING=1)."""

    services = build_services()
    total = services.store.total_cost_usd()
    limit = services.settings.max_budget_usd
    typer.echo(f"Recorded spend: ${total:.4f}")
    if limit > 0:
        used = min(100.0, total / limit * 100) if limit else 0.0
        typer.echo(f"Budget cap:     ${limit:.2f}  ({used:.1f}% used)")
    else:
        typer.echo("Budget cap:     none (AIRGENT_MAX_BUDGET_USD unset)")
    if not services.settings.cost_tracking_enabled:
        typer.echo("Note: AIRGENT_COST_TRACKING is off; only new runs record usage.", err=True)


@app.command()
def sandbox() -> None:
    """Show the sandbox provider that the kernel would run under."""

    from app.sandbox.factory import create_sandbox
    from app.sandbox.base import SandboxError

    settings = get_settings()
    try:
        spec = create_sandbox(settings)
    except SandboxError as exc:
        typer.echo(f"sandbox error: {exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"provider: {spec.provider}")
    typer.echo(f"summary : {spec.summary}")
    if spec.argv_prefix:
        typer.echo(f"prefix  : {' '.join(spec.argv_prefix)}")


# ------------------------------------------------------------------
# Memory extras (core blocks)
# ------------------------------------------------------------------
@memory_app.command("reindex")
def memory_reindex() -> None:
    """Backfill embeddings for memories (requires AIRGENT_MEMORY_EMBEDDINGS=1)."""

    services = build_services()
    if services.store.embedder is None:
        typer.echo(
            "Embeddings are off. Set AIRGENT_MEMORY_EMBEDDINGS=1 (and OPENAI_BASE_URL), "
            "then `uv sync --extra memory` for sqlite-vec.",
            err=True,
        )
        raise typer.Exit(code=1)
    count = services.store.embed_missing_memories()
    typer.echo(f"Embedded {count} memories.")


@memory_app.command("blocks")
def memory_blocks() -> None:
    """List core memory blocks (Letta-style always-in-context notes)."""

    services = build_services()
    blocks = services.store.list_blocks()
    if not blocks:
        typer.echo("No core memory blocks.")
        return
    for block in blocks:
        typer.echo(f"{block.key} ({block.updated_at}):")
        typer.echo(f"  {block.value}")


@memory_app.command("set-block")
def memory_set_block(key: str, value: str) -> None:
    """Create or update a core memory block."""

    services = build_services()
    block = services.store.set_block(key, value)
    typer.echo(f"Saved block {block.key}")


@memory_app.command("delete-block")
def memory_delete_block(key: str) -> None:
    """Delete a core memory block."""

    services = build_services()
    if services.store.delete_block(key):
        typer.echo(f"Deleted block {key}")
    else:
        typer.echo(f"Block {key} not found", err=True)
        raise typer.Exit(code=1)


# ------------------------------------------------------------------
# Skills registry commands
# ------------------------------------------------------------------
@skills_app.command("list")
def skills_list() -> None:
    """List discovered skills and their registry state."""

    from app.skills.registry import SkillRegistry

    services = build_services()
    registry = SkillRegistry(services.settings)
    entries = registry.discover()
    if not entries:
        typer.echo("No skills discovered.")
        return
    for entry in entries:
        state = "on" if registry.is_enabled(entry.name) else "off"
        typer.echo(f"[{state}] {entry.name}  ({entry.source})  {entry.path}")
        typer.echo(f"    {entry.description[:120]}")


@skills_app.command("enable")
def skills_enable(name: str) -> None:
    """Enable a skill in the registry."""

    from app.skills.registry import SkillRegistry

    services = build_services()
    if SkillRegistry(services.settings).set_enabled(name, True):
        typer.echo(f"Enabled skill {name}")
    else:
        typer.echo(f"Skill {name} not found", err=True)
        raise typer.Exit(code=1)


@skills_app.command("disable")
def skills_disable(name: str) -> None:
    """Disable a skill in the registry."""

    from app.skills.registry import SkillRegistry

    services = build_services()
    if SkillRegistry(services.settings).set_enabled(name, False):
        typer.echo(f"Disabled skill {name}")
    else:
        typer.echo(f"Skill {name} not found", err=True)
        raise typer.Exit(code=1)


@skills_app.command("mode")
def skills_mode(mode: str = typer.Argument(help="allow_all or deny_all")) -> None:
    """Set the default policy for undisabled/unenabled skills."""

    from app.skills.registry import SkillRegistry

    services = build_services()
    try:
        SkillRegistry(services.settings).set_mode(mode)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Registry mode set to {mode}")


@skills_app.command("add-dir")
def skills_add_dir(path: str) -> None:
    """Register an extra skills directory to scan."""

    from app.skills.registry import SkillRegistry

    services = build_services()
    SkillRegistry(services.settings).add_extra_dir(path)
    typer.echo(f"Added skills dir {path}")


@skills_app.command("install")
def skills_install(
    git_url: str,
    name: str | None = typer.Option(None, "--name", help="Directory name (defaults to the repo stem)."),
) -> None:
    """Install a skill package from a git URL (shallow clone)."""

    from app.skills.registry import SkillRegistry

    services = build_services()
    try:
        result = SkillRegistry(services.settings).install(git_url, name=name)
    except (ValueError, OSError) as exc:
        typer.echo(f"Install failed: {exc}", err=True)
        raise typer.Exit(code=1)
    if isinstance(result, list):
        names = ", ".join(entry.name for entry in result)
        typer.echo(f"Installed {len(result)} skills from {git_url}: {names}")
    else:
        typer.echo(f"Installed skill {result.name} at {result.path}")


@skills_app.command("remove")
def skills_remove(name: str) -> None:
    """Remove an installed skill package."""

    from app.skills.registry import SkillRegistry

    services = build_services()
    if SkillRegistry(services.settings).remove(name):
        typer.echo(f"Removed skill {name}")
    else:
        typer.echo(f"Installed skill {name} not found", err=True)
        raise typer.Exit(code=1)


def main() -> None:
    app()
