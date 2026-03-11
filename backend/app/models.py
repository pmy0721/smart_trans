from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.utils import now_bjt_naive


class Base(DeclarativeBase):
    pass


class Accident(Base):
    __tablename__ = "accidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=now_bjt_naive, nullable=False)

    source: Mapped[str] = mapped_column(String(32), default="script", nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    hint: Mapped[str | None] = mapped_column(Text, nullable=True)

    has_accident: Mapped[bool] = mapped_column(Boolean, nullable=False)
    accident_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    location_text: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    location_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_model_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Triplet pipeline outputs (stored from /api/ingest_triplet)
    cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_qualitative: Mapped[str | None] = mapped_column(Text, nullable=True)
    law_refs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
