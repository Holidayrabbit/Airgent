from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, Request

from app.api.schemas.cron import CronJobCreate, CronJobResponse, CronJobUpdate
from app.cron.service import CronService, JobRecord, ScheduleKind

router = APIRouter(prefix="/cron", tags=["cron"])


def _job_to_resp(job: object) -> CronJobResponse:
    j = job  # type: ignore[assignment]
    return CronJobResponse(
        id=j.id,
        name=j.name,
        agent_key=j.agent_key,
        input=j.input,
        schedule_kind=j.schedule_kind.value,
        schedule_value=j.schedule_value,
        enabled=j.enabled,
        one_shot=j.one_shot,
        last_run_at=j.last_run_at,
        next_run_at=j.next_run_at,
        created_at=j.created_at,
        metadata=j.metadata,
    )


@router.get("", response_model=list[CronJobResponse])
async def list_jobs(request: Request) -> list[CronJobResponse]:
    cron: CronService = request.app.state.services.cron
    return [_job_to_resp(j) for j in cron.list_jobs()]


@router.get("/{job_id}", response_model=CronJobResponse)
async def get_job(request: Request, job_id: str) -> CronJobResponse:
    cron: CronService = request.app.state.services.cron
    job = cron.get_job(job_id)
    if job is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _job_to_resp(job)


@router.post("", response_model=CronJobResponse, status_code=201)
async def create_job(request: Request, payload: CronJobCreate) -> CronJobResponse:
    cron: CronService = request.app.state.services.cron
    # Handle once → one_shot
    one_shot = payload.one_shot or (payload.schedule_kind == "once")
    record = JobRecord(
        name=payload.name,
        agent_key=payload.agent_key,
        input=payload.input,
        schedule_kind=ScheduleKind(payload.schedule_kind),
        schedule_value=payload.schedule_value,
        enabled=payload.enabled,
        one_shot=one_shot,
        metadata_json=json.dumps(payload.metadata),
    )
    job = cron.create_job(record)
    return _job_to_resp(job)


@router.patch("/{job_id}", response_model=CronJobResponse)
async def update_job(request: Request, job_id: str, payload: CronJobUpdate) -> CronJobResponse:
    cron: CronService = request.app.state.services.cron
    updates = payload.model_dump(exclude_unset=True)
    if "metadata" in updates:
        updates["metadata_json"] = json.dumps(updates.pop("metadata"))
    if "schedule_kind" in updates and updates["schedule_kind"] == "once":
        updates["one_shot"] = True
    job = cron.update_job(job_id, updates)
    if job is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _job_to_resp(job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(request: Request, job_id: str) -> None:
    cron: CronService = request.app.state.services.cron
    deleted = cron.delete_job(job_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.post("/{job_id}/pause", status_code=204)
async def pause_job(request: Request, job_id: str) -> None:
    cron: CronService = request.app.state.services.cron
    ok = cron.pause_job(job_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.post("/{job_id}/resume", status_code=204)
async def resume_job(request: Request, job_id: str) -> None:
    cron: CronService = request.app.state.services.cron
    ok = cron.resume_job(job_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


@router.post("/{job_id}/trigger", status_code=202)
async def trigger_job(request: Request, job_id: str) -> dict[str, str]:
    cron: CronService = request.app.state.services.cron
    job = cron.get_job(job_id)
    if job is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    cron.trigger_job(job_id)
    return {"status": "triggered", "job_id": job_id}
