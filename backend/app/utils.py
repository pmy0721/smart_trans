from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


_BJT = ZoneInfo("Asia/Shanghai")


def now_bjt_naive() -> dt.datetime:
    """Return Beijing time as naive datetime for SQLite storage."""

    return dt.datetime.now(tz=_BJT).replace(tzinfo=None)


def as_bjt_aware(d: dt.datetime) -> dt.datetime:
    """Interpret naive datetimes as BJT and return timezone-aware BJT."""

    if d.tzinfo is None:
        return d.replace(tzinfo=_BJT)
    return d.astimezone(_BJT)


def uploads_dir() -> Path:
    base = os.getenv("SMART_TRANS_UPLOADS")
    if base:
        p = Path(base)
    else:
        p = Path(__file__).resolve().parent.parent / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def image_url_for_path(image_path: str | None) -> str | None:
    if not image_path:
        return None
    # We store image_path as relative "uploads/<filename>".
    if image_path.startswith("uploads/"):
        return "/" + image_path
    if image_path.startswith("/uploads/"):
        return image_path
    return None


def try_extract_exif_gps(image_file: Path) -> dict[str, Any] | None:
    try:
        from PIL import Image
    except Exception:
        return None

    try:
        with Image.open(image_file) as img:
            exif = img.getexif()
            if not exif:
                return None
    except Exception:
        return None

    gps_info = None
    try:
        # 34853 is GPSInfo
        gps_info = exif.get(34853)
    except Exception:
        gps_info = None

    if not gps_info:
        return None

    def _to_float(v):
        try:
            return float(v)
        except Exception:
            return None

    def _rational_to_float(x):
        # Pillow can return IFDRational or tuple
        if hasattr(x, "numerator") and hasattr(x, "denominator"):
            denom = _to_float(x.denominator)
            num = _to_float(x.numerator)
            if denom is None or denom == 0.0 or num is None:
                return None
            return num / denom
        if isinstance(x, (tuple, list)) and len(x) == 2:
            denom = _to_float(x[1])
            num = _to_float(x[0])
            if denom is None or denom == 0.0 or num is None:
                return None
            return num / denom
        return _to_float(x)

    def _dms_to_deg(dms):
        if not isinstance(dms, (tuple, list)) or len(dms) != 3:
            return None
        d = _rational_to_float(dms[0])
        m = _rational_to_float(dms[1])
        s = _rational_to_float(dms[2])
        if d is None or m is None or s is None:
            return None
        return d + (m / 60.0) + (s / 3600.0)

    lat = None
    lng = None
    try:
        # Common keys: 1=LatRef, 2=Lat, 3=LngRef, 4=Lng
        lat_ref = gps_info.get(1)
        lat_val = gps_info.get(2)
        lng_ref = gps_info.get(3)
        lng_val = gps_info.get(4)

        lat = _dms_to_deg(lat_val)
        lng = _dms_to_deg(lng_val)
        if lat is None or lng is None:
            return None

        if isinstance(lat_ref, str) and lat_ref.upper() == "S":
            lat = -lat
        if isinstance(lng_ref, str) and lng_ref.upper() == "W":
            lng = -lng
    except Exception:
        return None

    return {"lat": lat, "lng": lng, "location_confidence": 1.0, "location_source": "exif"}
