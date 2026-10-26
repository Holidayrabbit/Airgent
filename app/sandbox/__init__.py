"""Sandbox providers for the Airgent kernel.

When enabled, the `pi --mode rpc` subprocess is launched behind a sandbox
prefix so every tool it runs (bash, file edits, extensions) inherits the
confinement. Providers:

- seatbelt: macOS `sandbox-exec` profile (deny-by-default network, write
  confinement to the workspace and Airgent data dirs).
- docker: run the kernel inside a container with bind-mounted workspace and
  data directories (experimental; the image must ship node + pi).
- none (default): no sandbox — the plain pi-agent wrapper.

The default is `none`; enable with AIRGENT_SANDBOX=seatbelt|docker.
"""

from __future__ import annotations

from app.sandbox.base import SandboxError, SandboxSpec
from app.sandbox.factory import create_sandbox

__all__ = ["SandboxError", "SandboxSpec", "create_sandbox"]
