"""Lifecycle hooks: run external commands around agent turns.

Config (hooks.json, path overridable via AIRGENT_HOOKS_CONFIG):
{
  "hooks": {
    "before_run": [{"command": "/path/to/check.sh", "timeout": 10}],
    "after_run":  [{"command": "/path/to/notify.sh", "timeout": 10}]
  }
}

Each hook receives the turn payload as JSON on stdin. Conventions:
- exit 0  -> allow / success
- exit 2  -> (before_run only) block the run; stdout becomes the reason
- other   -> failure is logged but does not block the run

Disabled unless AIRGENT_HOOKS=1.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings


@dataclass(frozen=True)
class HookOutcome:
    kind: str
    ok: bool
    blocked: bool = False
    detail: str = ""


class HookService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config_path: Path = settings.hooks_config

    def _hooks(self, kind: str) -> list[dict]:
        if not self.settings.hooks_enabled or not self.config_path.is_file():
            return []
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        hooks = payload.get("hooks") if isinstance(payload, dict) else None
        entries = hooks.get(kind) if isinstance(hooks, dict) else None
        return [entry for entry in entries or [] if isinstance(entry, dict) and entry.get("command")]

    def _run_one(self, command: str, payload_json: str, timeout: float) -> HookOutcome:
        try:
            proc = subprocess.run(
                command,
                input=payload_json,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=True,
            )
        except subprocess.TimeoutExpired:
            return HookOutcome(kind="hook", ok=False, detail=f"hook timed out after {timeout}s")
        except OSError as exc:
            return HookOutcome(kind="hook", ok=False, detail=str(exc))
        if proc.returncode == 0:
            return HookOutcome(kind="hook", ok=True, detail=(proc.stdout or "").strip())
        if proc.returncode == 2:
            reason = (proc.stdout or proc.stderr or "blocked by hook").strip()
            return HookOutcome(kind="hook", ok=False, blocked=True, detail=reason)
        return HookOutcome(kind="hook", ok=False, detail=(proc.stderr or f"exit {proc.returncode}").strip())

    async def run(self, kind: str, payload: dict) -> HookOutcome:
        """Run every hook of `kind` sequentially; first block short-circuits."""

        entries = self._hooks(kind)
        if not entries:
            return HookOutcome(kind=kind, ok=True)
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        outcome = HookOutcome(kind=kind, ok=True)
        for entry in entries:
            timeout = float(entry.get("timeout") or 30)
            outcome = await asyncio.to_thread(self._run_one, str(entry["command"]), payload_json, timeout)
            if outcome.blocked:
                return outcome
        return outcome
