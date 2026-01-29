from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from app.schemas import UploadResponse
from app.utils import image_url_for_path, try_extract_exif_gps, uploads_dir


router = APIRouter(prefix="/api", tags=["uploads"])


@router.post("/uploads", response_model=UploadResponse)
async def upload_image(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    name = f"{uuid.uuid4().hex}{suffix}"

    base = uploads_dir()
    dst = base / name

    data = await file.read()
    dst.write_bytes(data)

    image_path = f"uploads/{name}"
    image_url = image_url_for_path(image_path) or f"/uploads/{name}"

    exif = try_extract_exif_gps(dst)
    return UploadResponse(image_path=image_path, image_url=image_url, exif=exif)
