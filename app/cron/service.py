from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from app.memory.store import utcnow


class ScheduleKind(str, Enum):
    CRON = "cron"
    ONCE = "once"
    INTERVAL = "interval"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_cron_next(cron_expr: str, *, base: datetime) -> datetime | None:
    """Very lightweight cron parser supporting: min hour day month dow

    Supports: \"* * * * *\", \"30 * * * *\", \"0 9 * * *\", \"*/5 * * * *\"
    Does NOT support: ranges (1-5), lists (1,3,5), seconds field.
    Returns the next matching time (same base logic as standard cron).
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return None

    def matches(val: int, pattern: str) -> bool:
        if pattern == "*":
            return True
        if pattern.startswith("*/"):
            step = int(pattern[2:])
            return val % step == 0
        return val == int(pattern)

    min_p, hour_p, day_p, month_p, dow_p = parts
    d = datetime(
        base.year, base.month, base.day, base.hour, base.minute,
        tzinfo=timezone.utc
    ) + __import__("datetime").timedelta(minutes=1)

    for _ in range(366 * 24 * 60):
        if (
            matches(d.minute, min_p)
            and matches(d.hour, hour_p)
            and matches(d.day, day_p)
            and matches(d.month, month_p)
            and matches(d.weekday(), dow_p)
        ):
            return d
        d += __import__("datetime").timedelta(minutes=1)

    return None


def parse_interval_next(interval_seconds: int, *, base: datetime) -> datetime:
    return base + __import__("datetime").timedelta(seconds=interval_seconds)


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    name: str
    agent_key: str
    input: str
    schedule_kind: ScheduleKind
    schedule_value: str  # cron expr, "once", or interval seconds as str
    enabled: bool
    one_shot: bool
    last_run_at: str | None
    next_run_at: str | None
    created_at: str
    metadata_json: str

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json) if self.metadata_json else {}


@dataclass
class JobRecord:
    """Mutable shell for building a ScheduledJob."""
    name: str
    agent_key: str
    input: str
    schedule_kind: ScheduleKind
    schedule_value: str
    enabled: bool = True
    one_shot: bool = False
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str = field(default_factory=utcnow)
    metadata_json: str = "{}"
    id: str | None = None

    def to_job(self, id: str) -> ScheduledJob:
        return ScheduledJob(
            id=id,
            name=self.name,
            agent_key=self.agent_key,
            input=self.input,
            schedule_kind=self.schedule_kind,
            schedule_value=self.schedule_value,
            enabled=self.enabled,
            one_shot=self.one_shot,
            last_run_at=self.last_run_at,
            next_run_at=self.next_run_at,
            created_at=self.created_at,
            metadata_json=self.metadata_json,
        )


class CronService:
    """Lightweight cron scheduler backed by LocalStore SQLite.

    Schedules are evaluated lazily — next_run_at is recalculated after each run.
    A background loop polls every 30 seconds and fires due jobs as asyncio tasks.
    """

    def __init__(
        self,
        store: Any,  # LocalStore — avoid circular import
        runner: Any,  # AgentRunnerService
        poll_interval: float = 30.0,
    ) -> None:
        self._store = store
        self._runner = runner
        self._poll_interval = poll_interval
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Schema migration (adds scheduled_jobs table if missing)
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        self._store.initialize_cron()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def list_jobs(self) -> list[ScheduledJob]:
        rows = self._store.list_cron_jobs()
        return [self._row_to_job(row) for row in rows]

    def get_job(self, job_id: str) -> ScheduledJob | None:
        row = self._store.get_cron_job(job_id)
        return self._row_to_job(row) if row else None

    def create_job(self, record: JobRecord) -> ScheduledJob:
        job_id = uuid4().hex[:12]
        now = utcnow()

        # Compute initial next_run_at
        base = datetime.now(timezone.utc)
        if record.schedule_kind == ScheduleKind.CRON:
            next_at = parse_cron_next(record.schedule_value, base=base)
        elif record.schedule_kind == ScheduleKind.ONCE:
            next_at = parse_cron_next("0 0 1 1 *", base=base)  # far future, overridden
            record.one_shot = True
        else:  # INTERVAL
            next_at = parse_interval_next(int(record.schedule_value), base=base)

        next_str = next_at.isoformat() if next_at else None

        self._store.insert_cron_job(
            id=job_id,
            name=record.name,
            agent_key=record.agent_key,
            input=record.input,
            schedule_kind=record.schedule_kind.value,
            schedule_value=record.schedule_value,
            enabled=record.enabled,
            one_shot=record.one_shot,
            last_run_at=None,
            next_run_at=next_str,
            created_at=now,
            metadata_json=record.metadata_json,
        )
        return self._store.get_cron_job(job_id)  # type: ignore[return-value]

    def update_job(self, job_id: str, updates: dict[str, Any]) -> ScheduledJob | None:
        job = self._store.get_cron_job(job_id)
        if not job:
            return None

        # Recompute next_run_at if schedule changed
        kind_changed = "schedule_kind" in updates or "schedule_value" in updates
        if kind_changed:
            kind = updates.get("schedule_kind", job["schedule_kind"])
            value = updates.get("schedule_value", job["schedule_value"])
            base = datetime.now(timezone.utc)
            if kind == ScheduleKind.CRON.value:
                next_at = parse_cron_next(value, base=base)
            else:
                next_at = parse_interval_next(int(value), base=base)
            updates["next_run_at"] = next_at.isoformat() if next_at else None

        self._store.update_cron_job(job_id, updates)
        return self._store.get_cron_job(job_id)  # type: ignore[return-value]

    def delete_job(self, job_id: str) -> bool:
        # Cancel if running
        async def _cancel():
            async with self._lock:
                if job_id in self._tasks:
                    self._tasks[job_id].cancel()
                    del self._tasks[job_id]

        # Run synchronously for simplicity
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_cancel())
        except RuntimeError:
            pass

        return self._store.delete_cron_job(job_id)

    def pause_job(self, job_id: str) -> bool:
        return self._store.update_cron_job(job_id, {"enabled": False}) is not None

    def resume_job(self, job_id: str) -> bool:
        job = self._store.get_cron_job(job_id)
        if not job:
            return False
        # Recompute next_run_at from now
        base = datetime.now(timezone.utc)
        kind = job["schedule_kind"]
        value = job["schedule_value"]
        if kind == ScheduleKind.CRON.value:
            next_at = parse_cron_next(value, base=base)
        else:
            next_at = parse_interval_next(int(value), base=base)
        return self._store.update_cron_job(job_id, {
            "enabled": True,
            "next_run_at": next_at.isoformat() if next_at else None,
        }) is not None

    def trigger_job(self, job_id: str) -> asyncio.Task:
        """Immediately fire a job (skipping schedule check)."""
        async def _run():
            await self._execute_job(job_id)

        loop = asyncio.get_running_loop()
        task = loop.create_task(_run())
        return task

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._stopping = False
        self._loop_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stopping = True
        if hasattr(self, "_loop_task"):
            self._loop_task.cancel()
        async with self._lock:
            for task in list(self._tasks.values()):
                task.cancel()
            self._tasks.clear()

    async def _poll_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._poll_interval)
                await self._check_and_fire()
            except asyncio.CancelledError:
                break
            except Exception:
                # Swallow errors silently — don't crash the background loop
                pass

    async def _check_and_fire(self) -> None:
        now = datetime.now(timezone.utc)
        due = self._store.list_due_cron_jobs(now.isoformat())
        for row in due:
            job = self._row_to_job(row)
            if job is None:
                continue
            async with self._lock:
                if job.id in self._tasks and not self._tasks[job.id].done():
                    continue  # already running
                task = asyncio.create_task(self._execute_job(job.id))
                self._tasks[job.id] = task

    async def _execute_job(self, job_id: str) -> None:
        job = self._store.get_cron_job(job_id)
        if not job or not job["enabled"]:
            return

        try:
            from app.api.schemas.agent import AgentRunRequest
            session_id = f"cron:{job_id}:{uuid4().hex[:8]}"
            request = AgentRunRequest(
                input=job["input"],
                session_id=session_id,
                agent_key=job["agent_key"],
                max_turns=None,
            )
            result = await self._runner.run(request, request_id=f"cron:{job_id}")
            # Log result to session
            self._store.append_message(
                session_id=session_id,
                role="system",
                content=f"[CRON {job['name']}] completed: {result.output[:200]}",
                agent_key=job["agent_key"],
                metadata={"cron_job_id": job_id, "kind": "cron_result"},
            )
        except Exception as exc:
            # Log failure
            self._store.append_message(
                session_id=session_id,
                role="system",
                content=f"[CRON {job['name']}] failed: {exc}",
                agent_key=job["agent_key"],
                metadata={"cron_job_id": job_id, "kind": "cron_error"},
            )
        finally:
            # Advance schedule
            now = datetime.now(timezone.utc)
            kind = job["schedule_kind"]
            value = job["schedule_value"]

            if job["one_shot"]:
                self._store.update_cron_job(job_id, {
                    "enabled": False,
                    "last_run_at": now.isoformat(),
                    "next_run_at": None,
                })
            else:
                if kind == ScheduleKind.CRON.value:
                    next_at = parse_cron_next(value, base=now)
                else:
                    next_at = parse_interval_next(int(value), base=now)
                self._store.update_cron_job(job_id, {
                    "last_run_at": now.isoformat(),
                    "next_run_at": next_at.isoformat() if next_at else None,
                })

            async with self._lock:
                if job_id in self._tasks:
                    del self._tasks[job_id]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_job(row: dict[str, Any] | None) -> ScheduledJob | None:
        if row is None:
            return None
        return ScheduledJob(
            id=row["id"],
            name=row["name"],
            agent_key=row["agent_key"],
            input=row["input"],
            schedule_kind=ScheduleKind(row["schedule_kind"]),
            schedule_value=row["schedule_value"],
            enabled=bool(row["enabled"]),
            one_shot=bool(row["one_shot"]),
            last_run_at=row["last_run_at"],
            next_run_at=row["next_run_at"],
            created_at=row["created_at"],
            metadata_json=row.get("metadata_json", "{}"),
        )
