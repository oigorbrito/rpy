from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.tracking_api import router as tracking_router


router = APIRouter()
router.include_router(tracking_router)
_FRONTEND_DIR = Path(__file__).with_name("frontend")


@router.get("/", include_in_schema=False)
async def frontend_index() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "index.html")


@router.get("/app.css", include_in_schema=False)
async def frontend_css() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "app.css", media_type="text/css")


@router.get("/app.js", include_in_schema=False)
async def frontend_js() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "app.js", media_type="text/javascript")
