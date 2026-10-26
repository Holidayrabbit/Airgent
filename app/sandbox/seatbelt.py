"""macOS Seatbelt (sandbox-exec) profile rendering.

Policy shape:
- deny by default, then allow what an agent runtime actually needs
- reads: system locations + workspace + Airgent data
- writes: workspace, Airgent data, temp dirs only
- network: denied unless sandbox_network is on
"""

from __future__ import annotations

from app.core.config import Settings
from app.sandbox.base import SandboxSpec

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
  (subpath "{workspace}")
  (subpath "{data_dir}")
  (subpath "/private/tmp")
  (subpath "/private/var/folders")
  (subpath "/dev/null")
)

{network}
"""


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
    return _PROFILE_TEMPLATE.format(
        workspace=settings.project_root,
        data_dir=settings.data_dir,
        network=_network_clause(settings.sandbox_network),
    )


def seatbelt_spec(settings: Settings) -> SandboxSpec:
    profile_path = settings.pi_agent_dir / "sandbox.sb"
    network = "on" if settings.sandbox_network else "off"
    return SandboxSpec(
        provider="seatbelt",
        argv_prefix=["/usr/bin/sandbox-exec", "-f", str(profile_path)],
        files={profile_path: render_seatbelt_profile(settings)},
        summary=f"macOS seatbelt (network={network}, workspace={settings.project_root})",
    )
