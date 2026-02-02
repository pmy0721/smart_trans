from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Accident
from app.schemas import BucketCount, GeoBucket, SummaryStats, TimelinePoint
from app.utils import now_bjt_naive


router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats/summary", response_model=SummaryStats)
def summary(db: Session = Depends(get_db)):
    total = int(db.execute(select(func.count()).select_from(Accident)).scalar() or 0)

    since = now_bjt_naive() - dt.timedelta(days=7)
    last_7d = int(
        db.execute(select(func.count()).select_from(Accident).where(Accident.created_at >= since)).scalar() or 0
    )

    severe = int(
        db.execute(select(func.count()).select_from(Accident).where(Accident.severity == "严重")).scalar() or 0
    )

    severe_ratio = (severe / total) if total else 0.0
    return SummaryStats(total=total, last_7d=last_7d, severe=severe, severe_ratio=severe_ratio)


@router.get("/stats/by_type", response_model=list[BucketCount])
def by_type(db: Session = Depends(get_db)):
    rows = db.execute(select(Accident.accident_type)).scalars().all()
    c = Counter([r or "其他" for r in rows])
    return [BucketCount(key=k, count=int(v)) for k, v in c.most_common()]


@router.get("/stats/by_severity", response_model=list[BucketCount])
def by_severity(db: Session = Depends(get_db)):
    rows = db.execute(select(Accident.severity)).scalars().all()
    c = Counter([r or "轻微" for r in rows])
    order = {"轻微": 0, "中等": 1, "严重": 2}
    items = [BucketCount(key=k, count=int(v)) for k, v in c.items()]
    items.sort(key=lambda x: order.get(x.key, 99))
    return items


@router.get("/stats/timeline", response_model=list[TimelinePoint])
def timeline(db: Session = Depends(get_db), days: int = Query(30, ge=1, le=365)):
    now = now_bjt_naive()
    since = now - dt.timedelta(days=days)
    rows = db.execute(select(Accident.created_at).where(Accident.created_at >= since)).scalars().all()

    buckets: dict[str, int] = defaultdict(int)
    for d in rows:
        key = d.date().isoformat()
        buckets[key] += 1

    out: list[TimelinePoint] = []
    for i in range(days, -1, -1):
        key = (now - dt.timedelta(days=i)).date().isoformat()
        out.append(TimelinePoint(date=key, count=int(buckets.get(key, 0))))
    return out


@router.get("/stats/geo", response_model=list[GeoBucket])
def geo_buckets(
    db: Session = Depends(get_db),
    precision: int = Query(2, ge=0, le=6),
    limit: int = Query(200, ge=1, le=2000),
):
    lat_r = func.round(Accident.lat, precision).label("lat")
    lng_r = func.round(Accident.lng, precision).label("lng")
    cnt = func.count().label("count")

    stmt = (
        select(lat_r, lng_r, cnt)
        .where(Accident.lat.is_not(None), Accident.lng.is_not(None))
        .group_by(lat_r, lng_r)
        .order_by(desc(cnt))
        .limit(limit)
    )

    rows = db.execute(stmt).all()
    out: list[GeoBucket] = []
    for lat, lng, c in rows:
        if lat is None or lng is None:
            continue
        out.append(GeoBucket(lat=float(lat), lng=float(lng), count=int(c or 0)))
    return out
