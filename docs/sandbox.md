# Sandbox

Airgent can run the pi kernel subprocess inside a sandbox, so every tool the
agent executes (bash, file edits, extensions) inherits the confinement. The
feature is off by default; enable it with:

```bash
export AIRGENT_SANDBOX=seatbelt   # macOS
export AIRGENT_SANDBOX=docker     # any OS with Docker
export AIRGENT_SANDBOX=landlock   # Linux (kernel >= 5.13, no helpers needed)
airgent sandbox                   # print the effective provider + mode
```

## Modes (AIRGENT_SANDBOX_MODE)

Codex-style tiers, applied by every provider:

| Mode | Behavior |
|------|----------|
| `read-only` | The workspace is read-only; the agent persists only its own state (`~/.airgent`) |
| `workspace-write` (default) | Writes allowed inside the workspace + `~/.airgent` |
| `full` | File writes unrestricted (network still follows the sandbox flag) |

Network is denied in every mode unless `AIRGENT_SANDBOX_NETWORK=1`.

## seatbelt (macOS, recommended)

Airgent renders a `sandbox-exec` profile to `~/.airgent/pi/agent/sandbox.sb`
and launches pi behind `/usr/bin/sandbox-exec -f <profile>`. In `read-only`
the workspace write subpath is omitted entirely; in `full` the write filter is
dropped. Verified behaviors (see `tests/test_sandbox.py`): the agent can run
and write inside the workspace; outbound HTTPS fails with network confinement.

## landlock (Linux, zero dependencies)

Unprivileged kernel sandboxing — no helper binary, no compilation. Airgent
re-execs the kernel through `python -m app.sandbox.landlock --root DIR ... --`
, which installs a Landlock ruleset (write rights granted only under the
allowed roots) and then `exec`s pi, so the restriction covers the kernel
process and everything it spawns. Landlock governs filesystem access only;
network is not covered by this provider.

Requires Linux with Landlock support (kernel ≥ 5.13); on other hosts the
provider raises with a clear error.

## docker (experimental)

The kernel runs as `docker run --rm -i` with the workspace and `~/.airgent`
bind-mounted at their host paths (`:ro` on the workspace in `read-only` mode),
`--network none` unless `AIRGENT_SANDBOX_NETWORK=1`, plus a whitelist of
environment variables.

The image must provide Node.js, a global `pi` install, and Python 3 (for the
memory extension CLI). A minimal recipe:

```dockerfile
FROM node:20-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent
```

```bash
docker build -t airgent-sandbox:latest .
export AIRGENT_SANDBOX=docker
export AIRGENT_SANDBOX_IMAGE=airgent-sandbox:latest
```

Note: inside the container the Airgent Python package is not installed, so the
memory extension's `python -m app` calls need the repo available in the image
(mount the project or bake it in). Docker sandboxing is best suited for
projects that already run inside a container.
