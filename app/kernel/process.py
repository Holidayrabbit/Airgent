"""Async wrapper around one `pi --mode rpc` subprocess.

Protocol (verified against pi 0.74.x): strict JSONL over stdin/stdout, one
JSON object per line. Commands may carry an "id" which is echoed on the
matching "response" record; all other records are session events. stdout
carries protocol traffic only; diagnostics go to stderr.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.kernel.errors import KernelError


class PiProcess:
    def __init__(self, *, command: list[str], env: dict[str, str], cwd: str, request_timeout: float) -> None:
        self._command = command
        self._env = env
        self._cwd = cwd
        self._request_timeout = request_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._counter = 0
        self._closed = False

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and not self._closed

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") == "response":
                    pending = self._pending.pop(record.get("id"), None)
                    if pending is not None and not pending.done():
                        pending.set_result(record)
                    else:
                        # Unsolicited responses (e.g. parse errors) surface as events.
                        await self._event_queue.put(record)
                else:
                    await self._event_queue.put(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            for pending in self._pending.values():
                if not pending.done():
                    pending.set_exception(KernelError("pi process closed before responding"))
            self._pending.clear()

    async def request(self, command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a command and return its response `data` payload.

        Raises KernelError when the command fails or the process dies.
        """

        if not self.alive:
            raise KernelError("pi process is not running")
        self._counter += 1
        request_id = f"airgent-{self._counter}"
        record = {"id": request_id, "type": command_type, **(payload or {})}
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(record).encode("utf-8") + b"\n")
        await self._proc.stdin.drain()

        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            response = await asyncio.wait_for(future, timeout=self._request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise KernelError(f"pi did not respond to {command_type} within {self._request_timeout}s") from None
        if not response.get("success"):
            raise KernelError(str(response.get("error") or f"{command_type} failed"))
        return response.get("data") or {}

    async def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        if timeout is None:
            return await self._event_queue.get()
        try:
            record = await asyncio.wait_for(self._event_queue.get(), timeout)
        except asyncio.TimeoutError:
            return None
        return record

    def task_done(self) -> None:
        try:
            self._event_queue.task_done()
        except ValueError:
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
        if proc is not None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
