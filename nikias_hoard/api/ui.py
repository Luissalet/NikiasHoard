"""Routes for the bundled studio. Whatever the buttons do is also an agent tool (``POST /api/ui/call`` runs those
handlers); the rest are the files the page shows: the mesh for the 3-D view, the colour and region of each face,
pictures, dropped reference images and downloads."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..agent_tools import TOOLS_BY_NAME
from ..errors import NikiasError
from .deps import services, tool

router = APIRouter()
NO_CACHE = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}


class CallBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    arguments: Optional[dict[str, Any]] = None


@router.post("/api/ui/call")
def ui_call(request: Request, body: CallBody):
    if body.name not in TOOLS_BY_NAME:
        raise NikiasError("not_found", f"No existe la herramienta {body.name}.")
    result = tool(request, body.name, body.arguments or {})
    if isinstance(result, dict):
        result.pop("_image", None)
    return result


@router.get("/api/status")
def status(request: Request):
    return services(request).status()


@router.get("/api/library/photo")
def photo(request: Request, name: str):
    return Response(services(request).photo(name), media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@router.get("/api/projects/{pid}/figure.bin")
def figure(request: Request, pid: str):
    return FileResponse(services(request).view_file(pid), media_type="application/octet-stream", headers={"Cache-Control": "max-age=3600"})


@router.get("/api/projects/{pid}/faces/{which}.bin")
def faces(request: Request, pid: str, which: str):
    if which not in ("labels", "regions"):
        raise NikiasError("not_found", "No existe ese dato.")
    return Response(services(request).face_data(pid, which), media_type="application/octet-stream", headers=NO_CACHE)


@router.get("/api/projects/{pid}/pick")
def pick(request: Request, pid: str, face: int):
    return services(request).pick(pid, face)


@router.get("/api/projects/{pid}/look.png")
def look(request: Request, pid: str, what: str = "color", view: str = "iso", yaw: Optional[float] = None, pitch: Optional[float] = None,
         size: int = 900):
    return Response(services(request).look(pid, what, view, yaw, pitch, size), media_type="image/png", headers=NO_CACHE)


@router.get("/api/projects/{pid}/sources/{sid}.png")
def source_picture(request: Request, pid: str, sid: str):
    return Response(services(request).source_picture(pid, sid), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@router.post("/api/projects/{pid}/sources/upload")
async def source_upload(request: Request, pid: str, name: str = "referencia"):
    data = await request.body()
    if not data:
        raise NikiasError("invalid", "No ha llegado ninguna imagen.")
    return services(request).add_image(pid, name, data)


@router.get("/api/projects/{pid}/download/{fmt}")
def download(request: Request, pid: str, fmt: str):
    """One export written to the data folder and sent to the browser."""
    svc = services(request)
    project = svc.store.get(pid)
    if fmt not in ("bambu", "orca", "prusa", "color", "parts", "parts-bambu", "parts-prusa"):
        raise NikiasError("not_found", "Formatos de descarga: bambu, orca, prusa, color, parts, parts-bambu, parts-prusa.")
    folder = svc.store.dir(pid) / "download"
    out = svc.export(pid, [fmt], str(folder), variants=False)
    path = next(f for f in out["files"] if f.endswith(".3mf"))
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return FileResponse(path, media_type="model/3mf", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", **NO_CACHE})
