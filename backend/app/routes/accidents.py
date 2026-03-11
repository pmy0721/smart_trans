from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Accident
from app.schemas import AccidentCreate, AccidentListResponse, AccidentRead
from app.utils import as_bjt_aware, clamp01, image_url_for_path


router = APIRouter(prefix="/api", tags=["accidents"])


_TRIPLET_RE = re.compile(r"triplet_job_id=([0-9a-f]{32})")


def _repo_root() -> Path:
    # backend/app/routes/accidents.py -> backend/app/routes -> backend/app -> backend -> repo root
    return Path(__file__).resolve().parents[3]


def _incoming_root() -> Path:
    base = os.getenv("SMART_TRANS_INCOMING_DIR", "incoming")
    p = Path(base)
    if not p.is_absolute():
        p = (_repo_root() / p).resolve()
    return p


def _load_triplet_frames_from_job(job_id: str) -> list[dict] | None:
    jid = (job_id or "").strip()
    if not jid:
        return None

    p = _incoming_root() / "jobs" / f"{jid}.json"
    if not p.is_file():
        return None

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    frames = data.get("frames")
    if not isinstance(frames, list):
        return None

    out: list[dict] = []
    for f in frames:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "").strip() or None
        image_path = f.get("image_path") if isinstance(f.get("image_path"), str) else None
        image_url = f.get("image_url") if isinstance(f.get("image_url"), str) else None
        if image_url is None and image_path is not None:
            image_url = image_url_for_path(image_path)
        if key and (image_url or image_path):
            out.append({"key": key, "image_path": image_path, "image_url": image_url})

    if not out:
        return None

    order = {"t0": 0, "t-1s": 1, "t-3s": 2}
    out.sort(key=lambda x: order.get(str(x.get("key") or ""), 99))
    return out


def _to_read(a: Accident) -> AccidentRead:
    frames = None
    if a.raw_model_output:
        m = _TRIPLET_RE.search(a.raw_model_output)
        if m:
            frames = _load_triplet_frames_from_job(m.group(1))
    if frames is None:
        # Fallback: single frame.
        frames = [
            {
                "key": "t0",
                "image_path": a.image_path,
                "image_url": image_url_for_path(a.image_path),
            }
        ]

    law_refs = None
    if a.law_refs_json:
        try:
            obj = json.loads(a.law_refs_json)
            if isinstance(obj, list):
                law_refs = obj
        except Exception:
            law_refs = None

    return AccidentRead(
        id=a.id,
        created_at=as_bjt_aware(a.created_at),
        source=a.source,
        image_path=a.image_path,
        image_url=image_url_for_path(a.image_path),
        hint=a.hint,
        has_accident=a.has_accident,
        accident_type=a.accident_type,
        severity=a.severity,
        description=a.description,
        confidence=a.confidence,
        location_text=a.location_text,
        lat=a.lat,
        lng=a.lng,
        location_source=a.location_source,
        location_confidence=a.location_confidence,
        raw_model_output=a.raw_model_output,
        cause=a.cause,
        legal_qualitative=a.legal_qualitative,
        law_refs=law_refs,
        frames=frames,
    )


@router.post("/accidents", response_model=AccidentRead)
def create_accident(payload: AccidentCreate, db: Session = Depends(get_db)):
    severity = payload.severity.strip()
    if severity not in {"轻微", "中等", "严重"}:
        severity = "中等" if payload.has_accident else "轻微"

    law_refs_json = None
    if payload.law_refs is not None:
        try:
            law_refs_json = json.dumps(payload.law_refs, ensure_ascii=False)
        except Exception:
            law_refs_json = None


    a = Accident(
        source=(payload.source or "script").strip() or "script",
        image_path=payload.image_path,
        hint=payload.hint,
        has_accident=bool(payload.has_accident),
        accident_type=payload.accident_type.strip() or "其他",
        severity=severity,
        description=payload.description.strip(),
        confidence=clamp01(float(payload.confidence)),
        location_text=(payload.location_text.strip() if payload.location_text else None),
        lat=payload.lat,
        lng=payload.lng,
        location_source=(payload.location_source.strip() if payload.location_source else None),
        location_confidence=payload.location_confidence,
        raw_model_output=payload.raw_model_output,
        cause=(payload.cause.strip() if isinstance(payload.cause, str) and payload.cause.strip() else None),
        legal_qualitative=(
            payload.legal_qualitative.strip()
            if isinstance(payload.legal_qualitative, str) and payload.legal_qualitative.strip()
            else None
        ),
        law_refs_json=law_refs_json,
    )

    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_read(a)


@router.get("/accidents", response_model=AccidentListResponse)
def list_accidents(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    has_accident: bool | None = Query(None),
    severity: str | None = Query(None),
    accident_type: str | None = Query(None, alias="type"),
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    filters = []
    if has_accident is not None:
        filters.append(Accident.has_accident == has_accident)
    if severity:
        filters.append(Accident.severity == severity)
    if accident_type:
        filters.append(Accident.accident_type == accident_type)

    bjt = ZoneInfo("Asia/Shanghai")

    def _parse_dt_bjt_naive(s: str) -> dt.datetime | None:
        try:
            d = dt.datetime.fromisoformat(s)
        except Exception:
            return None

        # Treat naive inputs as BJT local time.
        if d.tzinfo is None:
            return d

        try:
            return d.astimezone(bjt).replace(tzinfo=None)
        except Exception:
            return None

    if start:
        d = _parse_dt_bjt_naive(start)
        if d:
            filters.append(Accident.created_at >= d)
    if end:
        d = _parse_dt_bjt_naive(end)
        if d:
            filters.append(Accident.created_at <= d)

    where = and_(*filters) if filters else None

    total_stmt = select(func.count()).select_from(Accident)
    if where is not None:
        total_stmt = total_stmt.where(where)
    total = int(db.execute(total_stmt).scalar() or 0)

    stmt = select(Accident)
    if where is not None:
        stmt = stmt.where(where)
    stmt = stmt.order_by(Accident.created_at.desc()).offset((page - 1) * page_size).limit(page_size)

    rows = list(db.execute(stmt).scalars().all())
    return AccidentListResponse(items=[_to_read(a) for a in rows], total=total, page=page, page_size=page_size)


@router.get("/accidents/{accident_id}", response_model=AccidentRead)
def get_accident(accident_id: int, db: Session = Depends(get_db)):
    a = db.get(Accident, accident_id)
    if not a:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not found")
    return _to_read(a)
