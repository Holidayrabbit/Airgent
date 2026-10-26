"""Materializes the isolated pi agent directory used by the Airgent kernel.

Everything pi needs lives under ~/.airgent/pi/agent so the user's own ~/.pi
configuration is never touched:
- models.json     custom "airgent" provider when OPENAI_BASE_URL points at a proxy
- APPEND_SYSTEM.md  Airgent's operating instructions (appended to pi's prompt)
- settings.json   optional skills overlay (only when the skill registry filters)
- extensions/     airgent-memory (always) + opt-in guardrails / subagent / mcp

Optional capabilities (sandbox, skill registry, MCP, sub-agents, guardrails)
default to OFF; with everything disabled the result is exactly a plain
pi-agent wrapper plus the memory tools.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.sandbox.base import SandboxSpec
from app.sandbox.factory import create_sandbox

PROXY_PROVIDER_ID = "airgent"

PI_DIR = Path(__file__).resolve().parents[2] / "pi"

SYSTEM_ADDENDUM = """\
You are Airgent, a personal AI operating system for an individual user, running
on the pi agent kernel.

Responsibilities:
1. Answer directly when no tool is needed.
2. Use the file tools (read, write, edit) for project file work; read before editing.
3. Use bash only for local, non-destructive project commands.
4. Use memory tools to recall stable preferences, ongoing projects, and prior
   commitments: `search_memory` before assuming prior context, `remember_note`
   to persist durable facts.
5. Skills in .agents/skills are discovered automatically; follow a skill when
   it clearly matches the task.

Memory policy:
- Save durable facts only when the user asks to remember them, or when a
  preference/project detail is clearly stable and useful later.
- Never store secrets, credentials, or one-off ephemeral details.

Behavior rules:
- Never fabricate tool results or prior history.
- Keep responses concise, practical, and honest about uncertainty.
- If memory seems stale or conflicting, call it out and ask for confirmation.
"""

SUBAGENT_ADDENDUM = """
Delegation:
- For large, self-contained subtasks (research sweeps, bulk refactors) you may
  call `spawn_subagent`. Put every detail the sub-agent needs into `task`;
  it cannot see this conversation.
"""

PERMISSION_ADDENDUM_TEMPLATE = """
Permissions:
- You are running under the "{mode}" permission tier.
- When a tool call is blocked, do not retry it unchanged; explain what was
  denied and propose an alternative, or ask the user to adjust permissions.
- Some commands require user approval; the user will be asked and may deny.
"""


def _permission_addendum(mode: str) -> str:
    return PERMISSION_ADDENDUM_TEMPLATE.format(mode=mode)

SANDBOX_ADDENDUM = """
Environment:
- You are running inside a sandbox: file writes outside the workspace and
  Airgent data directories may fail, and network access may be blocked.
  Prefer workspace-relative paths and say so when a sandbox denial blocks you.
"""


@dataclass(frozen=True)
class PiEnvironment:
    agent_dir: Path
    session_dir: Path
    provider_model: str | None
    """"--model" argument for proxy deployments, e.g. 'airgent/anthropic/claude-sonnet-4.5'."""
    sandbox: SandboxSpec
    extensions: tuple[str, ...]
    """Extension file names deployed into the agent dir."""


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_if_changed(path: Path, text: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    _write_private(path, text)


def _render_extension(template_name: str, replacements: dict[str, str]) -> str:
    template = (PI_DIR / template_name).read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


def _build_models_json(settings: Settings) -> dict | None:
    if not settings.openai_base_url:
        return None
    provider = {
        "baseUrl": settings.openai_base_url.rstrip("/"),
        "api": settings.pi_api_name,
        "apiKey": settings.openai_api_key,
        "models": [
            {"id": settings.default_model, "name": settings.default_model},
        ],
    }
    return {"$schema": "https://pi.dev/schemas/models.schema.json", "providers": {PROXY_PROVIDER_ID: provider}}


def _default_guardrails_policy() -> dict:
    return {
        "denied_commands": ["rm\\s+-rf\\s+/", "sudo\\s"],
        "denied_paths": ["(^|/)\\.env$", "secrets/"],
        "denied_tools": [],
        "max_bash_timeout": 300,
        "message": "Blocked by Airgent guardrails policy",
    }


def _default_permissions_policy() -> dict:
    # Patterns live in JSON so users can tune them without touching the
    # rendered extension; empty lists mean "use the extension defaults".
    return {"ask_commands": [], "deny_commands": [], "deny_tools": []}


def _has_tool_hooks(hooks_config: Path) -> bool:
    """True when hooks.json declares before_tool/after_tool entries."""

    if not hooks_config.is_file():
        return False
    try:
        payload = json.loads(hooks_config.read_text(encoding="utf-8"))
        hooks = payload.get("hooks") if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(hooks, dict):
        return False
    for kind in ("before_tool", "after_tool"):
        entries = hooks.get(kind)
        if isinstance(entries, list) and any(
            isinstance(entry, dict) and entry.get("command") for entry in entries
        ):
            return True
    return False


_PERMISSION_MODES = {"off", "read-only", "workspace-write"}


def _write_json_if_missing(path: Path, payload: dict) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(path, json.dumps(payload, indent=2))


def _apply_skills_overlay(settings: Settings) -> None:
    """Write the skills whitelist into the agent dir settings.json (opt-in)."""

    from app.skills.registry import SkillRegistry

    overlay = SkillRegistry(settings).settings_overlay()
    settings_path = settings.pi_agent_dir / "settings.json"
    if overlay is None:
        # Keep native discovery: drop a stale overlay we may have written.
        if settings_path.is_file():
            try:
                payload = json.loads(settings_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return
            if isinstance(payload, dict) and "skills" in payload:
                payload.pop("skills", None)
                if payload:
                    settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                else:
                    settings_path.unlink(missing_ok=True)
        return
    _write_if_changed(settings_path, json.dumps(overlay, indent=2))


def prepare_environment(settings: Settings) -> PiEnvironment:
    agent_dir = settings.pi_agent_dir
    session_dir = settings.pi_session_dir
    agent_dir.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)

    sandbox = create_sandbox(settings)
    for path, content in sandbox.files.items():
        _write_if_changed(path, content)

    models = _build_models_json(settings)
    if models is None:
        # Without a proxy, let pi resolve the model from its built-in catalog
        # and provider API keys in the environment.
        target = agent_dir / "models.json"
        if target.exists() and os.environ.get("AIRGENT_PI_KEEP_MODELS_JSON") != "0":
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None
            if not isinstance(payload, dict) or PROXY_PROVIDER_ID not in payload.get("providers", {}):
                target.unlink(missing_ok=True)
        else:
            target.unlink(missing_ok=True)
        provider_model = None
    else:
        _write_if_changed(agent_dir / "models.json", json.dumps(models, indent=2))
        provider_model = f"{PROXY_PROVIDER_ID}/{settings.default_model}"

    permission_mode = settings.permission_mode.strip().lower()
    if permission_mode not in _PERMISSION_MODES:
        raise ValueError(
            f"Invalid AIRGENT_PERMISSION_MODE {settings.permission_mode!r}; "
            f"expected one of: {', '.join(sorted(_PERMISSION_MODES))}"
        )

    addendum = SYSTEM_ADDENDUM
    if settings.subagents_enabled:
        addendum += SUBAGENT_ADDENDUM
    if permission_mode != "off":
        addendum += _permission_addendum(permission_mode)
    if sandbox.enabled:
        addendum += SANDBOX_ADDENDUM
    _write_if_changed(agent_dir / "APPEND_SYSTEM.md", addendum)

    python = json.dumps(sys.executable)
    deployed: list[str] = []

    _write_if_changed(
        agent_dir / "extensions" / "airgent-memory.ts",
        _render_extension("airgent-memory.ts", {"__AIRGENT_PYTHON__": python}),
    )
    deployed.append("airgent-memory.ts")

    if settings.guardrails_enabled:
        guardrails_config = settings.guardrails_config
        _write_json_if_missing(guardrails_config, _default_guardrails_policy())
        _write_if_changed(
            agent_dir / "extensions" / "airgent-guardrails.ts",
            _render_extension(
                "airgent-guardrails.ts",
                {"__AIRGENT_GUARDRAILS_CONFIG__": json.dumps(str(guardrails_config))},
            ),
        )
        deployed.append("airgent-guardrails.ts")

    if permission_mode != "off":
        permission_config = settings.permission_config
        _write_json_if_missing(permission_config, _default_permissions_policy())
        _write_if_changed(
            agent_dir / "extensions" / "airgent-permissions.ts",
            _render_extension(
                "airgent-permissions.ts",
                {
                    "__AIRGENT_PERMISSION_MODE__": json.dumps(permission_mode),
                    "__AIRGENT_WORKSPACE__": json.dumps(str(settings.project_root)),
                    "__AIRGENT_DATA_DIR__": json.dumps(str(settings.data_dir)),
                    "__AIRGENT_PERMISSIONS_CONFIG__": json.dumps(str(permission_config)),
                },
            ),
        )
        deployed.append("airgent-permissions.ts")

    if settings.subagents_enabled:
        subagent_model = settings.subagents_model or settings.default_model
        _write_if_changed(
            agent_dir / "extensions" / "airgent-subagent.ts",
            _render_extension(
                "airgent-subagent.ts",
                {
                    "__AIRGENT_PI_BIN__": json.dumps(settings.pi_bin),
                    "__AIRGENT_SUBAGENT_MODEL__": json.dumps(subagent_model),
                    "__AIRGENT_SUBAGENT_DEPTH__": json.dumps(settings.subagents_max_depth),
                },
            ),
        )
        deployed.append("airgent-subagent.ts")

    if settings.mcp_enabled:
        mcp_config = settings.mcp_config
        _write_json_if_missing(mcp_config, {"mcpServers": {}})
        _write_if_changed(
            agent_dir / "extensions" / "airgent-mcp.ts",
            _render_extension("airgent-mcp.ts", {"__AIRGENT_MCP_CONFIG__": json.dumps(str(mcp_config))}),
        )
        deployed.append("airgent-mcp.ts")

    if settings.hooks_enabled and _has_tool_hooks(settings.hooks_config):
        _write_if_changed(
            agent_dir / "extensions" / "airgent-tool-hooks.ts",
            _render_extension(
                "airgent-tool-hooks.ts",
                {"__AIRGENT_HOOKS_CONFIG__": json.dumps(str(settings.hooks_config))},
            ),
        )
        deployed.append("airgent-tool-hooks.ts")

    # Remove opt-in extensions that are no longer enabled so stale copies
    # never keep running with old config paths.
    optional = {
        "airgent-guardrails.ts",
        "airgent-permissions.ts",
        "airgent-subagent.ts",
        "airgent-mcp.ts",
        "airgent-tool-hooks.ts",
    }
    for name in optional - set(deployed):
        (agent_dir / "extensions" / name).unlink(missing_ok=True)

    _apply_skills_overlay(settings)

    return PiEnvironment(
        agent_dir=agent_dir,
        session_dir=session_dir,
        provider_model=provider_model,
        sandbox=sandbox,
        extensions=tuple(deployed),
    )


def build_spawn_env(settings: Settings, mode: str = "default") -> dict[str, str]:
    env = dict(os.environ)
    env["PI_CODING_AGENT_DIR"] = str(settings.pi_agent_dir)
    env["PI_CODING_AGENT_SESSION_DIR"] = str(settings.pi_session_dir)
    env["AIRGENT_SUBAGENT_DEPTH"] = "0"
    env["AIRGENT_RUN_MODE"] = mode
    if settings.openai_base_url:
        # Proxy deployments must not accidentally use provider keys from the
        # outer environment; models.json carries the credential.
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
            env.pop(key, None)
    return env
