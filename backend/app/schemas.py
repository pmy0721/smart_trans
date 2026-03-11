from __future__ import annotations

import datetime as dt

from typing import Any

from pydantic import BaseModel, Field


class AccidentCreate(BaseModel):
    has_accident: bool
    accident_type: str = Field(..., min_length=1, max_length=64)
    severity: str = Field(..., min_length=1, max_length=16)
    description: str = Field(default="", max_length=5000)
    confidence: float = Field(..., ge=0.0, le=1.0)

    source: str | None = Field(default=None, max_length=32)
    image_path: str | None = Field(default=None, max_length=512)
    hint: str | None = Field(default=None, max_length=5000)

    location_text: str | None = Field(default=None, max_length=256)
    lat: float | None = None
    lng: float | None = None
    location_source: str | None = Field(default=None, max_length=32)
    location_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    raw_model_output: str | None = Field(default=None, max_length=20000)

    # Optional: triplet pipeline outputs
    cause: str | None = Field(default=None, max_length=5000)
    legal_qualitative: str | None = Field(default=None, max_length=20000)
    law_refs: list[dict[str, Any]] | None = None



class AccidentRead(BaseModel):
    id: int
    created_at: dt.datetime

    source: str
    image_path: str | None
    image_url: str | None
    hint: str | None

    has_accident: bool
    accident_type: str
    severity: str
    description: str
    confidence: float

    location_text: str | None
    lat: float | None
    lng: float | None
    location_source: str | None
    location_confidence: float | None

    raw_model_output: str | None

    cause: str | None = None
    legal_qualitative: str | None = None
    law_refs: list[dict[str, Any]] | None = None

    # Optional triplet frames (t0/t-1s/t-3s) for UI display.
    frames: list[dict[str, Any]] | None = None



class AccidentListResponse(BaseModel):
    items: list[AccidentRead]
    total: int
    page: int
    page_size: int


class UploadResponse(BaseModel):
    image_path: str
    image_url: str
    exif: dict | None = None


class SummaryStats(BaseModel):
    total: int
    last_7d: int
    severe: int
    severe_ratio: float


class BucketCount(BaseModel):
    key: str
    count: int


class TimelinePoint(BaseModel):
    date: str
    count: int


class GeoBucket(BaseModel):
    lat: float
    lng: float
    count: int
