from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings
from app.hooks.service import HookService


def _settings(tmp_path: Path, *, hooks_enabled: bool, hooks_config: Path | None = None) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        hooks_enabled=hooks_enabled,
        hooks_config_path=hooks_config,
    )


def test_hooks_disabled_is_noop(tmp_path: Path) -> None:
    service = HookService(_settings(tmp_path, hooks_enabled=False))
    outcome = await_unwrap(service.run("before_run", {"input": "hi"}))
    assert outcome.ok and not outcome.blocked


def test_before_run_hook_blocks_on_exit_two(tmp_path: Path) -> None:
    config = tmp_path / "hooks.json"
    block_script = tmp_path / "block.sh"
    block_script.write_text("#!/bin/sh\necho 'not allowed'\nexit 2\n", encoding="utf-8")
    config.write_text(
        json.dumps({"hooks": {"before_run": [{"command": f"sh {block_script}", "timeout": 5}]}}),
        encoding="utf-8",
    )

    service = HookService(_settings(tmp_path, hooks_enabled=True, hooks_config=config))
    outcome = await_unwrap(service.run("before_run", {"input": "hi"}))

    assert outcome.blocked
    assert "not allowed" in outcome.detail


def test_hooks_pass_and_receive_payload(tmp_path: Path) -> None:
    config = tmp_path / "hooks.json"
    sink = tmp_path / "sink.jsonl"
    echo_script = tmp_path / "echo.sh"
    echo_script.write_text(f"cat >> {sink}\necho >> {sink}\nexit 0\n", encoding="utf-8")
    config.write_text(
        json.dumps(
            {
                "hooks": {
                    "before_run": [{"command": f"sh {echo_script}"}],
                    "after_run": [{"command": f"sh {echo_script}"}],
                }
            }
        ),
        encoding="utf-8",
    )

    service = HookService(_settings(tmp_path, hooks_enabled=True, hooks_config=config))
    assert await_unwrap(service.run("before_run", {"input": "hello"})).ok
    assert await_unwrap(service.run("after_run", {"output": "world"})).ok

    lines = [json.loads(line) for line in sink.read_text().splitlines()]
    assert {"input": "hello"} in lines
    assert {"output": "world"} in lines


def await_unwrap(coroutine):
    import asyncio

    return asyncio.run(coroutine)
