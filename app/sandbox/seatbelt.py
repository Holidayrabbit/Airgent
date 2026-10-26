"""macOS Seatbelt (sandbox-exec) profile rendering.

Modes (AIRGENT_SANDBOX_MODE, Codex-style tiers):
- read-only:      the workspace is read-only; the agent may still persist its
                  own state under the Airgent data dir (sessions, agent dir)
- workspace-write (default): writes allowed inside the workspace + data dir
- full:           writes unrestricted (network still follows the sandbox flag)

Network is denied unless AIRGENT_SANDBOX_NETWORK=1, in every mode.
"""

from __future__ import annotations

from app.core.config import Settings
from app.sandbox.base import SandboxError, SandboxSpec

_PROFILE_TEMPLATE = """\
(version 1)
(deny default)

; Process lifecycle
(allow process-exec*)
(allow process-fork)
(allow sysctl-read)
(allow mach-lookup)

; File reads: system + workspace + Airgent data
(allow file-read*)
(allow file-write*
  (subpath "{data_dir}")
  (subpath "/private/tmp")
  (subpath "/private/var/folders")
  (subpath "/dev/null"){workspace_clause}
)

{network}
"""

_WORKSPACE_WRITE_CLAUSE = '\n  (subpath "{workspace}")'
_FULL_CLAUSE = ""  # no write subpath restriction at all


def _workspace_clause(mode: str, workspace: str) -> str:
    if mode == "read-only":
        return ""
    if mode == "full":
        # Allow writes everywhere: replace the subpath block entirely.
        return _FULL_CLAUSE
    return _WORKSPACE_WRITE_CLAUSE.format(workspace=workspace)


def _network_clause(allowed: bool) -> str:
    if allowed:
        return "(allow network*)\n(allow network-outbound)\n(allow network-inbound)\n(allow system-socket)"
    return """\
; Network denied by default
(deny network*)
(deny network-outbound)
(deny network-inbound)
; Allow DNS and local IPC so the process can still start
(allow network-outbound (remote tcp "*:53"))
(allow network-outbound (remote udp "*:53"))
(allow system-socket)
"""


def render_seatbelt_profile(settings: Settings) -> str:
    mode = settings.sandbox_mode
    if mode == "full":
        # In full mode the whole file-write* filter is dropped.
        return f"""\
(version 1)
(deny default)
(allow process-exec*)
(allow process-fork)
(allow sysctl-read)
(allow mach-lookup)
(allow file-read*)
(allow file-write*)
{_network_clause(settings.sandbox_network)}
"""
    return _PROFILE_TEMPLATE.format(
        data_dir=settings.data_dir,
        workspace_clause=_workspace_clause(mode, settings.project_root),
        network=_network_clause(settings.sandbox_network),
    )


def seatbelt_spec(settings: Settings) -> SandboxSpec:
    profile_path = settings.pi_agent_dir / "sandbox.sb"
    network = "on" if settings.sandbox_network else "off"
    if settings.sandbox_mode not in {"read-only", "workspace-write", "full"}:
        raise SandboxError(
            f"Unknown sandbox mode {settings.sandbox_mode!r}; "
            "expected read-only, workspace-write, or full."
        )
    return SandboxSpec(
        provider="seatbelt",
        argv_prefix=["/usr/bin/sandbox-exec", "-f", str(profile_path)],
        files={profile_path: render_seatbelt_profile(settings)},
        summary=(
            f"macOS seatbelt mode={settings.sandbox_mode} "
            f"network={network} workspace={settings.project_root}"
        ),
    )
