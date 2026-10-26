# Sandbox

Airgent can run the pi kernel subprocess inside a sandbox, so every tool the
agent executes (bash, file edits, extensions) inherits the confinement. The
feature is off by default; enable it with:

```bash
export AIRGENT_SANDBOX=seatbelt   # macOS
export AIRGENT_SANDBOX=docker     # any OS with Docker
airgent sandbox                   # print the effective provider
```

## seatbelt (macOS, recommended)

Airgent renders a `sandbox-exec` profile to `~/.airgent/pi/agent/sandbox.sb`
and launches pi behind `/usr/bin/sandbox-exec -f <profile>`. Policy:

- deny by default; system paths are readable
- writes are confined to the workspace, the Airgent data dir (`~/.airgent`),
  `/private/tmp`, and `/private/var/folders`
- network is denied unless `AIRGENT_SANDBOX_NETWORK=1`

Verified behaviors (see `tests/test_sandbox.py`): the agent can run and write
inside the workspace; outbound HTTPS fails with network confinement on.

## docker (experimental)

The kernel runs as `docker run --rm -i` with the workspace and
`~/.airgent` bind-mounted at their host paths, `--network none` unless
`AIRGENT_SANDBOX_NETWORK=1`, plus a whitelist of environment variables.

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
