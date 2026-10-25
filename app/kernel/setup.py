"""Materializes the isolated pi agent directory used by the Airgent kernel.

Everything pi needs lives under ~/.airgent/pi/agent so the user's own ~/.pi
configuration is never touched:
- models.json     custom "airgent" provider when OPENAI_BASE_URL points at a proxy
- APPEND_SYSTEM.md  Airgent's operating instructions (appended to pi's prompt)
- extensions/airgent-memory.ts  search_memory / remember_note tools backed by
  `python -m app memory ...` against the same SQLite store
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings

PROXY_PROVIDER_ID = "airgent"

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


@dataclass(frozen=True)
class PiEnvironment:
    agent_dir: Path
    session_dir: Path
    provider_model: str | None
    """"--model" argument for proxy deployments, e.g. 'airgent/anthropic/claude-sonnet-4.5'."""


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


def _render_memory_extension() -> str:
    template_path = Path(__file__).resolve().parents[2] / "pi" / "airgent-memory.ts"
    template = template_path.read_text(encoding="utf-8")
    return template.replace("__AIRGENT_PYTHON__", json.dumps(sys.executable))


def prepare_environment(settings: Settings) -> PiEnvironment:
    agent_dir = settings.pi_agent_dir
    session_dir = settings.pi_session_dir
    agent_dir.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)

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

    _write_if_changed(agent_dir / "APPEND_SYSTEM.md", SYSTEM_ADDENDUM)
    _write_if_changed(agent_dir / "extensions" / "airgent-memory.ts", _render_memory_extension())

    return PiEnvironment(agent_dir=agent_dir, session_dir=session_dir, provider_model=provider_model)


def build_spawn_env(settings: Settings) -> dict[str, str]:
    env = dict(os.environ)
    env["PI_CODING_AGENT_DIR"] = str(settings.pi_agent_dir)
    env["PI_CODING_AGENT_SESSION_DIR"] = str(settings.pi_session_dir)
    if settings.openai_base_url:
        # Proxy deployments must not accidentally use provider keys from the
        # outer environment; models.json carries the credential.
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
            env.pop(key, None)
    return env
