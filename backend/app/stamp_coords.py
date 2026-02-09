from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


@dataclass(frozen=True)
class BBox:
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float


DEFAULT_HZ_BBOX = BBox(lat_min=30.18, lat_max=30.35, lng_min=120.05, lng_max=120.25)


def _env_flag(name: str) -> bool:
    v = os.getenv(name, "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _parse_bbox(text: str | None) -> BBox:
    if not text:
        return DEFAULT_HZ_BBOX
    s = str(text).strip()
    if not s:
        return DEFAULT_HZ_BBOX

    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        return DEFAULT_HZ_BBOX
    try:
        lat_min = float(parts[0])
        lat_max = float(parts[1])
        lng_min = float(parts[2])
        lng_max = float(parts[3])
    except Exception:
        return DEFAULT_HZ_BBOX

    # Basic sanity.
    if not (-90.0 <= lat_min <= 90.0 and -90.0 <= lat_max <= 90.0):
        return DEFAULT_HZ_BBOX
    if not (-180.0 <= lng_min <= 180.0 and -180.0 <= lng_max <= 180.0):
        return DEFAULT_HZ_BBOX
    if lat_max <= lat_min or lng_max <= lng_min:
        return DEFAULT_HZ_BBOX
    return BBox(lat_min=lat_min, lat_max=lat_max, lng_min=lng_min, lng_max=lng_max)


def _seed_for_file(global_seed: int, name: str) -> int:
    h = hashlib.sha256(f"{global_seed}:{name}".encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def pick_coord_hz(*, filename: str, bbox: BBox | None = None) -> tuple[float, float]:
    bb = bbox or _parse_bbox(os.getenv("SMART_TRANS_STAMP_HZ_BBOX"))

    seed_raw = os.getenv("SMART_TRANS_STAMP_SEED", "42").strip()
    try:
        global_seed = int(seed_raw)
    except Exception:
        global_seed = 42

    rng = random.Random(_seed_for_file(global_seed, filename))
    lat = rng.uniform(bb.lat_min, bb.lat_max)
    lng = rng.uniform(bb.lng_min, bb.lng_max)
    return float(lat), float(lng)


def _load_font(size: int) -> Any:
    # Keep consistent with tools/stamp_coords.py; best-effort on Linux.
    for fp in [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]:
        try:
            if os.path.isfile(fp):
                return ImageFont.truetype(fp, size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _draw_stamp(img: Image.Image, text: str) -> Image.Image:
    w, h = img.size
    font_size = int(max(14, min(40, round(w * 0.03))))
    font = _load_font(font_size)

    pad_x = int(max(10, round(w * 0.02)))
    pad_y = int(max(10, round(h * 0.02)))
    inner = int(max(6, round(font_size * 0.4)))

    base = ImageOps.exif_transpose(img)
    if base is None:
        raise RuntimeError("failed to load image")
    if base.mode != "RGBA":
        base = base.convert("RGBA")

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    x1 = w - pad_x - (tw + inner * 2)
    y1 = pad_y
    x2 = w - pad_x
    y2 = pad_y + (th + inner * 2)

    r = int(max(8, round(font_size * 0.6)))
    try:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=r, fill=(0, 0, 0, 145), outline=(255, 255, 255, 60))
    except Exception:
        draw.rectangle((x1, y1, x2, y2), fill=(0, 0, 0, 145), outline=(255, 255, 255, 60))

    tx = x1 + inner
    ty = y1 + inner
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 235))
    return Image.alpha_composite(base, overlay)


def stamp_inplace_hz(image_file: Path) -> dict[str, Any]:
    if not image_file.is_file():
        raise FileNotFoundError(str(image_file))

    if not _env_flag("SMART_TRANS_STAMP_COORDS") and os.getenv("SMART_TRANS_STAMP_COORDS") is not None:
        # If explicitly set to a falsy value, disable.
        return {"ok": False, "skipped": True, "reason": "SMART_TRANS_STAMP_COORDS disabled"}

    lat, lng = pick_coord_hz(filename=image_file.name)
    text = f"Lat: {lat:.6f}, Lng: {lng:.6f}"

    with Image.open(image_file) as img:
        stamped = _draw_stamp(img, text)
        suf = image_file.suffix.lower()

        # Best-effort preserve EXIF bytes (mostly for JPEG); may be missing after processing.
        exif_bytes = None
        try:
            exif_bytes = img.info.get("exif")
        except Exception:
            exif_bytes = None

        if suf in {".jpg", ".jpeg"}:
            out = stamped.convert("RGB")
            if exif_bytes:
                out.save(image_file, quality=92, optimize=True, exif=exif_bytes)
            else:
                out.save(image_file, quality=92, optimize=True)
        else:
            # png/webp/bmp/gif: keep default save behavior.
            stamped.save(image_file)

    return {"ok": True, "lat": lat, "lng": lng, "text": text}
