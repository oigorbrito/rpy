from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


router = APIRouter(include_in_schema=False)
_FRONTEND_DIR = Path(__file__).with_name("frontend")


@router.get("/")
async def frontend_index() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "index.html")


@router.get("/app.css")
async def frontend_css() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "app.css", media_type="text/css")


@router.get("/app.js")
async def frontend_js() -> FileResponse:
    return FileResponse(_FRONTEND_DIR / "app.js", media_type="text/javascript")
