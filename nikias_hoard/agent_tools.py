"""Tools for assistants. One catalogue drives ``/api/agent/*`` (the MCP bridge), the web UI (``/api/ui/call``) and
``mcp_server.py``. ``figure_look`` and ``source_look`` return pictures (MCP images) so an assistant sees the figure, its
regions and its colouring before and after each change."""

from __future__ import annotations

import base64
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .config import DEFAULT_SETTINGS
from .engine.render import VIEWS
from .hoard_link.agentkit import Empty, Tool, ann
from .hoard_link.agentkit import call_tool as _call_tool
from .hoard_link.agentkit import tool_catalog as _catalog
from .hoard_link.agentkit import uncapped  # noqa: F401 - re-exported for the UI routes
from .services import Services

AGENT_INSTRUCTIONS = """Nikias's Hoard colours finished 3D-print figures (single-colour STL sculpts) for multi-colour printing: every triangle gets one of a few flat colours (3-4 by default) and the figure is written as files slicers open already painted. It makes the digital file; which material prints each colour is the buyer's business.
Flow: library_scan lists the figures in the user's folders; project_create opens one (welds it and cuts it into regions along its folds: where parts meet and engraved outlines, closing the gaps an outline leaves). Colour comes from references: source_add takes a textured model of the same figure (OBJ, GLB, DAE, FBX, .blend), pictures, or a folder; source_find looks for candidates by the figure's name in the user's folders; source_fetch looks the character up on the web and keeps its picture. A model in the same pose as the figure passes its colours directly (its fit says how well the shapes agree: under about 0.012 it is trusted). Otherwise the vision model colours: with no reference in the project it first fetches a picture of the character, then reads the references, chooses the palette, writes which colour each part of the character takes, assigns a colour to each numbered region, cuts finer the regions it finds mixed, and finally looks at its own result and corrects it. color_start runs either in the background (job_status).
See before deciding: figure_look shows the figure as clay, by regions (numbered), by colour, or clay next to colour, from any view or zoomed on a spot; source_look shows a reference.
Correct by hand: palette_set changes colours and their names, how many there are, merges two or makes one the base (colour 1); paint fills whole regions, the patch around given faces, or brush spots; regions_set cuts the figure coarser or finer (detail 1-5); clean straightens borders and absorbs patches too small to matter; paint_undo takes back the last change.
result_export writes, next to the figure in a Multicolor folder: a painted 3MF for Bambu Studio / OrcaSlicer, one for PrusaSlicer, a standard-colour 3MF, an OBJ with one material per colour, a text file listing the colours, and the same for the figure's variants (its "flat" and "pivot" cuts take the colours from the figure).
Palette index 0 is colour 1, the object's own; indexes are 0-based everywhere. Coordinates are in the figure's own units and Z is up; sizes that decide the result (regions, smallest patch) are shares of the figure's size, so it colours the same at any scale."""


def _d(first: str, detail: str = "", synonyms: str = "") -> str:
    parts = [first]
    if detail:
        parts.append(detail)
    if synonyms:
        parts.append("Sinónimos: " + synonyms)
    return "\n".join(parts)


def _img(data: bytes) -> dict[str, str]:
    return {"data": base64.b64encode(data).decode(), "mime": "image/png"}


class ProjectId(BaseModel):
    project_id: str = Field(..., min_length=1, max_length=64)


class JobId(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=64)


class ScanArgs(BaseModel):
    refresh: bool = Field(False, description="Walk the folders again instead of using the last listing.")
    query: str = Field("", max_length=120, description="Only figures whose name or group contains this text.")
    group: str = Field("", max_length=300, description="Only figures of this group (the folder inside a library folder, e.g. Sonic or Pokemon/Regional).")
    limit: int = Field(80, ge=1, le=500)
    offset: int = Field(0, ge=0)


class CreateArgs(BaseModel):
    source: str = Field(..., min_length=1, max_length=1000, description="Path on this PC of the figure's STL (as library_scan lists it).")
    name: str = Field("", max_length=120)


class RenameArgs(ProjectId):
    name: str = Field(..., min_length=1, max_length=120)


class SourceAddArgs(ProjectId):
    path: str = Field(..., min_length=1, max_length=1000, description="A textured model (.obj, .glb, .gltf, .dae, .fbx, .blend), a picture (.png, .jpg, .webp) or a folder holding them.")
    name: str = Field("", max_length=120)


class SourceId(ProjectId):
    source_id: str = Field(..., min_length=1, max_length=32)


class FindArgs(ProjectId):
    query: str = Field("", max_length=120, description="Text to look for instead of the figure's name.")
    limit: int = Field(40, ge=1, le=200)


class FetchArgs(ProjectId):
    query: str = Field("", max_length=120, description="The character's name, when the figure's own name would not find it.")


class RegionsArgs(ProjectId):
    detail: Optional[int] = Field(None, ge=1, le=5, description="1 = big parts only, 5 = every engraved line. Omit to just list the current regions.")
    max_regions: Optional[int] = Field(None, ge=4, le=250)


class ColorArgs(ProjectId):
    mode: Literal["auto", "model", "ai"] = Field("auto", description="model: colours passed from a textured model source. ai: the vision model reads the references and "
                                                 "assigns colours to regions. auto: model when a source fits the figure, else ai.")
    colors: int = Field(0, ge=0, le=16, description="How many print colours (0 = the setting colors, 4 by default).")
    wish: str = Field("", max_length=500, description='With ai: what the user wants, in words ("shoes in red", "no white, I do not have it").')
    source_id: str = Field("", max_length=32, description="With model: which model source to take the colours from (default: the best fitting).")
    rounds: int = Field(-1, ge=-1, le=6, description="With ai: times the model may review and correct its colouring (-1 = the setting director_rounds).")


class PaletteColor(BaseModel):
    hex: Optional[str] = Field(None, max_length=9, description="#rrggbb")
    name: Optional[str] = Field(None, max_length=40)


class PaletteArgs(ProjectId):
    colors: Optional[list[Optional[PaletteColor]]] = Field(None, max_length=16, description="Changes by position (null leaves a colour as it is); positions past the end add colours.")
    count: Optional[int] = Field(None, ge=1, le=16, description="Bring the palette to this many colours: fewer merges the most alike; more adds spare ones. "
                                 "When the colours came from a model, they are chosen again from it.")
    merge: Optional[list[int]] = Field(None, min_length=2, max_length=2, description="[keep, remove]: everything painted with the second becomes the first.")
    base: Optional[int] = Field(None, ge=0, le=15, description="Make this colour number 1 (the object's own, the one unpainted triangles take).")


class Brush(BaseModel):
    points: list[list[float]] = Field(..., min_length=1, max_length=400, description="Spots on the surface, [x, y, z] in the figure's units.")
    radius_mm: float = Field(1.0, gt=0, le=200)
    normals: Optional[list[list[float]]] = Field(None, max_length=400, description="Surface direction at each spot, so the back of a thin part is not painted.")


class PaintArgs(ProjectId):
    color: int = Field(..., ge=0, le=15, description="Palette index.")
    regions: Optional[list[int]] = Field(None, max_length=250, description="Fill these whole regions (numbers as figure_look what=regions shows them).")
    faces: Optional[list[int]] = Field(None, max_length=200, description="Fill the single-colour patch around each of these faces (a bucket fill).")
    brush: Optional[Brush] = None
    everything: bool = Field(False, description="Paint the whole figure.")
    only_unset: bool = Field(False, description="Only touch faces nobody has coloured yet.")


class CleanArgs(ProjectId):
    min_patch: Optional[float] = Field(None, ge=0, le=20, description="Patches narrower than this share of the figure's size (% of its diagonal) join their surroundings (default: the setting; 0 keeps everything).")
    smooth: Optional[int] = Field(None, ge=0, le=12, description="Passes that straighten colour borders.")


class LookArgs(ProjectId):
    what: Literal["color", "regions", "clay", "model", "compare", "sources"] = Field(
        "color", description="color: the palette colours. regions: numbered regions. clay: the bare sculpt. "
        "model: the colours as they came from the textured model, before reducing. compare: clay next to colour. sources: the reference pictures.")
    view: str = Field("iso", description="One of: " + ", ".join(VIEWS))
    views: Optional[list[str]] = Field(None, max_length=6, description="Several views in one picture (e.g. front, left, back, right).")
    yaw: Optional[float] = Field(None, ge=-360, le=360, description="Degrees around the figure instead of a named view (0 = front).")
    pitch: Optional[float] = Field(None, ge=-89, le=89)
    size: int = Field(900, ge=200, le=1600)
    region: Optional[list[float]] = Field(None, min_length=4, max_length=4, description="[x, y, z, radius] in the figure's units: zoom on that spot.")
    numbers: Optional[bool] = Field(None, description="With regions: write each region's number (default true).")


class ExportArgs(ProjectId):
    formats: Optional[list[Literal["bambu", "prusa", "color", "obj", "stl"]]] = Field(
        None, description="bambu: painted 3MF for Bambu Studio / OrcaSlicer. prusa: painted 3MF for PrusaSlicer. color: standard-colour 3MF. obj: OBJ + MTL. "
        "stl: one STL per colour (open surfaces, for painting guides). Default: the setting export_formats.")
    folder: str = Field("", max_length=1000, description="Where to write (default: a Multicolor folder next to the figure).")
    variants: bool = Field(True, description="Also write the figure's variants (flat, pivot) with the same colours.")


class SettingsArgs(BaseModel):
    changes: dict[str, Any] = Field(..., description="Any of: " + ", ".join(DEFAULT_SETTINGS))


def _scan(s: Services, a: ScanArgs) -> dict[str, Any]:
    lib = s.library(a.refresh)
    entries = lib["entries"]
    q, g = a.query.lower().strip(), a.group.lower().strip()
    if q:
        entries = [e for e in entries if q in e["name"].lower() or q in e["group"].lower()]
    if g:
        entries = [e for e in entries if e["group"].lower() == g or e["group"].lower().startswith(g + "/")]
    groups: dict[str, int] = {}
    for e in lib["entries"]:
        groups[e["group"]] = groups.get(e["group"], 0) + 1
    return {"count": len(entries), "entries": entries[a.offset:a.offset + a.limit], "groups": groups, "dirs": lib["dirs"], "missing": lib["missing"],
            "hint": "" if lib["dirs"] else "No hay carpetas de biblioteca: añade una con settings_set {\"library_dirs\": [...]}."}


def _look(s: Services, a: LookArgs) -> dict[str, Any]:
    data = s.look(a.project_id, a.what, a.view, a.yaw, a.pitch, a.size, a.region, a.numbers, a.views)
    return {"what": a.what, "view": a.views or a.view, "_image": _img(data)}


def _source_look(s: Services, a: SourceId) -> dict[str, Any]:
    return {"source_id": a.source_id, "_image": _img(s.source_picture(a.project_id, a.source_id))}


def _settings_set(s: Services, a: SettingsArgs) -> dict[str, Any]:
    out = s.settings.update(a.changes)
    s.forget_listings()
    return out


TOOLS = [
    Tool("status", _d("Version, whether the vision model and Blender are reachable, the library folders and the jobs in progress."), Empty,
         ann(read_only=True), lambda s, a: s.status()),
    Tool("library_scan", _d("The print figures (STL) in the user's library folders, with their variants and loose pieces.",
                            "A figure's 'flat' and 'pivot' cuts and its base are listed inside it, not as figures.", "biblioteca, figuras, listar"),
         ScanArgs, ann(read_only=True), _scan),
    Tool("project_create", _d("Open a figure: weld it, cut it into regions and start a project. Opening the same STL again returns its project.",
                              synonyms="abrir figura, nuevo proyecto"), CreateArgs, ann(draft_safe=True, idempotent=True),
         lambda s, a: s.create_project(a.source, a.name), timeout_s=600),
    Tool("project_list", _d("The projects, newest first."), Empty, ann(read_only=True), lambda s, a: {"projects": s.projects()}),
    Tool("project_get", _d("A project: its figure, references, palette (with area, share and patches per colour) and what the vision model decided."),
         ProjectId, ann(read_only=True), lambda s, a: s.project(a.project_id)),
    Tool("project_rename", _d("Rename a project."), RenameArgs, ann(draft_safe=True, idempotent=True), lambda s, a: s.rename_project(a.project_id, a.name)),
    Tool("project_delete", _d("Delete a project and its colouring (the figure and anything exported are not touched)."), ProjectId, ann(destructive=True),
         lambda s, a: s.delete_project(a.project_id)),
    Tool("source_find", _d("Look in the reference folders for textured models and pictures named like the figure.", synonyms="buscar referencias, texturas"),
         FindArgs, ann(read_only=True), lambda s, a: s.find_sources(a.project_id, a.query, a.limit)),
    Tool("source_add", _d("Add a colour reference to the project: a textured model, a picture or a folder.",
                          "A model is laid over the figure by itself; the result says how well it fits (fit, good). FBX and .blend go through Blender, "
                          "with the saved pose applied.", "añadir referencia, textura, modelo, imagen"),
         SourceAddArgs, ann(draft_safe=True), lambda s, a: s.add_source(a.project_id, a.path, a.name), timeout_s=900),
    Tool("source_fetch", _d("Look the character up on the web by the figure's name and collection and keep the picture found as a reference.",
                            "Pokémon take their official artwork; other collections their wiki (setting wikis), else the encyclopedia. The result "
                            "says which page it came from; remove it with source_remove if it is not the character.", "buscar imagen, referencia de internet, arte oficial"),
         FetchArgs, ann(draft_safe=True, open_world=True), lambda s, a: s.fetch_reference(a.project_id, a.query), timeout_s=120),
    Tool("source_remove", _d("Take a reference out of the project (the file it came from is not touched)."), SourceId, ann(draft_safe=True, idempotent=True),
         lambda s, a: s.remove_source(a.project_id, a.source_id)),
    Tool("source_look", _d("See a reference: the picture, or the textured model from four sides once laid over the figure."), SourceId, ann(read_only=True),
         _source_look, capped=False),
    Tool("regions_set", _d("The regions of the figure with their size and current colour; with detail, cut the figure again coarser or finer.",
                           "Colours already painted stay where they are.", "regiones, segmentar, detalle"), RegionsArgs, ann(draft_safe=True, idempotent=True),
         lambda s, a: s.set_regions(a.project_id, a.detail, a.max_regions) if (a.detail or a.max_regions) else s.regions(a.project_id), timeout_s=600),
    Tool("color_start", _d("Colour the figure in the background from its references. Returns a job id for job_status.",
                           "Replaces the current colouring (paint_undo brings it back).", "colorear, pintar con IA, pasar texturas"),
         ColorArgs, ann(draft_safe=True), lambda s, a: s.start_color(a.project_id, a.mode, a.colors, a.wish, a.source_id, a.rounds)),
    Tool("job_status", _d("State of a job: queued, running (stage and progress), done (with result), cancelled or error."), JobId, ann(read_only=True),
         lambda s, a: s.job(a.job_id)),
    Tool("job_cancel", _d("Cancel a queued or running job; it stops at its next step and the previous colouring stays."), JobId, ann(draft_safe=True),
         lambda s, a: s.cancel_job(a.job_id)),
    Tool("palette_set", _d("Change the palette: colours, names, how many, merge two or choose the base.",
                           synonyms="paleta, colores"), PaletteArgs, ann(draft_safe=True),
         lambda s, a: s.set_palette(a.project_id, [c.model_dump(exclude_none=True) if c else None for c in a.colors] if a.colors is not None else None,
                                    a.count, a.merge, a.base)),
    Tool("paint", _d("Paint with a palette colour: whole regions, the patch around some faces, brush spots, or everything.", synonyms="pintar, rellenar, pincel"),
         PaintArgs, ann(draft_safe=True), lambda s, a: s.paint(a.project_id, a.color, a.regions, a.faces, a.brush.model_dump() if a.brush else None,
                                                                a.everything, a.only_unset)),
    Tool("paint_undo", _d("Take back the last change to the colouring (up to 20)."), ProjectId, ann(draft_safe=True), lambda s, a: s.undo(a.project_id)),
    Tool("clean", _d("Straighten colour borders and absorb patches too small to print.", synonyms="limpiar islas, suavizar bordes"), CleanArgs,
         ann(draft_safe=True), lambda s, a: s.clean(a.project_id, a.min_patch, a.smooth), timeout_s=300),
    Tool("figure_look", _d("See the figure: clay, numbered regions, colours, or clay next to colour; any view or a close-up.",
                           synonyms="ver figura, render, comparar"), LookArgs, ann(read_only=True), _look, timeout_s=300, capped=False),
    Tool("result_export", _d("Write the painted files (3MF for Bambu Studio/OrcaSlicer and PrusaSlicer, standard-colour 3MF, OBJ) and the list of colours.",
                             synonyms="exportar, 3MF, multicolor, guardar"), ExportArgs, ann(draft_safe=True),
         lambda s, a: s.export(a.project_id, a.formats, a.folder, a.variants), timeout_s=900),
    Tool("settings_get", _d("Settings: library, reference and photo folders, colours per figure, region detail, smallest patch, web references, export formats."),
         Empty, ann(read_only=True), lambda s, a: s.settings.get()),
    Tool("settings_set", _d("Change settings (library_dirs: folders with the figures; reference_dirs: where textured models and pictures are looked for; "
                            "min_patch: smallest patch of colour kept, in % of the figure's size; web_references: fetch a picture of the character when a figure has none)."), SettingsArgs, ann(draft_safe=True, idempotent=True), _settings_set),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tool_catalog() -> list[dict[str, Any]]:
    return _catalog(TOOLS)


def call_tool(svc: Services, name: str, arguments: Optional[dict[str, Any]], caller: str = "agent") -> Any:
    return _call_tool(TOOLS_BY_NAME, svc, name, arguments)
