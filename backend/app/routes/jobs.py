from __future__ import annotations

from fastapi import APIRouter, Query

from app.routes.ingest import store


router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    j = store.get((job_id or "").strip())
    if j is None:
        return {"ok": False, "error": "job not found"}
    return {"ok": True, "job": j.__dict__}


@router.get("/jobs")
def list_jobs(limit: int = Query(50, ge=1, le=200)) -> dict:
    items = [j.__dict__ for j in store.list(limit=int(limit))]
    return {"ok": True, "items": items}
