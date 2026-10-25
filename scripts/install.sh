#!/usr/bin/env sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first: https://docs.astral.sh/uv/"
  exit 1
fi

if ! command -v pi >/dev/null 2>&1; then
  echo "pi (the agent kernel) is required. Install it first:"
  echo "  curl -fsSL https://pi.dev/install.sh | sh"
  echo "  or: npm install -g --ignore-scripts @earendil-works/pi-coding-agent"
  exit 1
fi

PROJECT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

uv tool install --force "$PROJECT_DIR"
echo "Airgent installed globally. Run: airgent --help"
