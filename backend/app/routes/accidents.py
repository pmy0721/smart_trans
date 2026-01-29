from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Accident
from app.schemas import AccidentCreate, AccidentListResponse, AccidentRead
from app.utils import as_bjt_aware, clamp01, image_url_for_path


router = APIRouter(prefix="/api", tags=["accidents"])


def _to_read(a: Accident) -> AccidentRead:
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
    )


@router.post("/accidents", response_model=AccidentRead)
def create_accident(payload: AccidentCreate, db: Session = Depends(get_db)):
    severity = payload.severity.strip()
    if severity not in {"轻微", "中等", "严重"}:
        severity = "中等" if payload.has_accident else "轻微"

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
