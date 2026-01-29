from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.db import engine
from app.models import Base
from app.routes.accidents import router as accidents_router
from app.routes.stats import router as stats_router
from app.routes.uploads import router as uploads_router
from app.utils import uploads_dir


def create_app() -> FastAPI:
    app = FastAPI(title="smart_trans", version="0.1.0")

    origins = [
        os.getenv("SMART_TRANS_CORS_ORIGIN", "http://localhost:5173"),
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in origins if o],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(uploads_router)
    app.include_router(accidents_router)
    app.include_router(stats_router)

    up = uploads_dir()
    app.mount("/uploads", StaticFiles(directory=str(up)), name="uploads")

    static_dir = Path(__file__).resolve().parent.parent / "static"
    index = static_dir / "index.html"
    assets_dir = static_dir / "assets"

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    if index.is_file():

        @app.get("/")
        def serve_index():
            return FileResponse(index)

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str):
            if full_path.startswith("api/") or full_path.startswith("uploads/") or full_path.startswith("assets/"):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="not found")
            return FileResponse(index)

    @app.on_event("startup")
    def _init_db() -> None:
        Base.metadata.create_all(bind=engine)

    return app


app = create_app()
