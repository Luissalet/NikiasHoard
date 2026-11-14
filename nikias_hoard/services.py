"""Everything the studio does, behind one object: the library of figures, projects, colour sources, colouring jobs,
palette and paint edits, pictures and exports. The web UI and the agent tools call the same methods."""

from __future__ import annotations

import io
import logging
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from . import __version__, director
from .config import Config, Settings
from .engine import blender, export, labels as lab, mesh as meshes, modelio, palette as pal, regions as reg, render, source
from .errors import NikiasError
from .hoard_link.tokens import read_or_create_token
from .llm import ModelClient
from .store import Store

log = logging.getLogger("nikias")

FIGURE_EXTS = (".stl",)
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".tif", ".tiff")
MODEL_EXTS = modelio.MODEL_EXTS
SKIP_DIRS = {"multicolor", "__pycache__", ".git", "node_modules"}
MAX_FACES = 6_000_000
UNSET = lab.UNSET


class _JobCancelled(Exception):
    pass


@dataclass
class Figure:
    """A project's arrays while it is open."""
    mesh: meshes.Mesh
    regions: np.ndarray                       # (m,) int32
    labels: np.ndarray                        # (m,) uint8 palette index, UNSET where nobody coloured yet
    rgb: Optional[np.ndarray] = None          # (m, 3) uint8: colours brought from a textured model, before reducing
    undo: deque = field(default_factory=lambda: deque(maxlen=20))


def _name_of(c: dict[str, Any]) -> str:
    return str(c.get("name") or pal.name_of(pal.from_hex(c["hex"])))


class Services:
    def __init__(self, config: Config):
        self.config = config
        config.paths.ensure()
        config.projects_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(config.settings_path)
        self.token = read_or_create_token(config.token_path)
        self.store = Store(config.projects_dir)
        self.llm = ModelClient(self.settings)
        self.started = time.time()
        self.jobs: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._figures: "OrderedDict[str, Figure]" = OrderedDict()
        self._library: Optional[dict[str, Any]] = None
        self._reference_index: Optional[list[tuple[str, str]]] = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.config.background and self._worker is None:
            self._worker = threading.Thread(target=self._run, name="nikias-worker", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._queue.put(None)
            self._worker = None

    def health(self) -> dict[str, Any]:
        return {"projects": len(self.store.list()), "jobs_running": sum(1 for j in self.jobs.values() if j["state"] == "running")}

    def status(self) -> dict[str, Any]:
        found = blender.find_blender(self.settings["blender"])
        return {"version": __version__, "projects": len(self.store.list()), "library_dirs": self.settings["library_dirs"],
                "blender": found or "", "model": self.llm.status(), "formats": list(export.DIALECTS) + ["obj", "stl"],
                "jobs": [self._job_public(j) for j in self.jobs.values() if j["state"] in ("queued", "running")]}

    def forget_listings(self) -> None:
        with self._lock:
            self._library = None
            self._reference_index = None

    # ------------------------------------------------------------------ library
    def library(self, refresh: bool = False) -> dict[str, Any]:
        """The figures in the library folders. "<name> flat.stl" and the like are listed inside their figure."""
        with self._lock:
            if self._library is not None and not refresh:
                return self._library
        s = self.settings.get()
        variants, parts = tuple(s["variant_suffixes"]), tuple(s["part_suffixes"])
        photos = self._photo_index(s["photo_dirs"])
        used = {str(p.get("source", {}).get("path", "")).lower(): p["id"] for p in self.store.list()}
        entries: list[dict[str, Any]] = []
        missing: list[str] = []
        for root in s["library_dirs"]:
            base = Path(root)
            if not base.is_dir():
                missing.append(root)
                continue
            for dirpath, dirs, files in os.walk(base):
                rel = Path(dirpath).relative_to(base)
                dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith("."))
                if len(rel.parts) >= 7:
                    dirs[:] = []
                stems = {Path(f).stem.lower(): f for f in files if f.lower().endswith(FIGURE_EXTS)}
                extra: dict[str, dict[str, list]] = {}
                mains: list[str] = []
                for low in sorted(stems):
                    owner, kind, label = None, "", ""
                    for suf in variants + parts:
                        if low.endswith(" " + suf) and low[: -len(suf) - 1] in stems:
                            owner, kind, label = low[: -len(suf) - 1], ("variants" if suf in variants else "parts"), suf
                            break
                    if owner is None:                      # "<figure> glass left": a loose piece of the longest figure name it starts with
                        cands = [m for m in stems if m != low and low.startswith(m + " ") and not any(m.endswith(" " + x) for x in variants + parts)]
                        own_family = any((low + " " + x) in stems for x in variants + parts)
                        if cands and not own_family:
                            owner, kind, label = max(cands, key=len), "parts", ""
                            label = low[len(owner) + 1:]
                    if owner is None:
                        mains.append(low)
                    else:
                        extra.setdefault(owner, {"variants": [], "parts": []})[kind].append({"name": label, "path": str(Path(dirpath) / stems[low])})
                group = "/".join(rel.parts) if rel.parts else base.name
                for low in mains:
                    full = Path(dirpath) / stems[low]
                    try:
                        size = full.stat().st_size
                    except OSError:
                        continue
                    e = extra.get(low, {"variants": [], "parts": []})
                    entries.append({"path": str(full), "name": full.stem, "group": group, "size_mb": round(size / 1e6, 1),
                                    "variants": e["variants"], "parts": e["parts"], "photo": low in photos,
                                    "project_id": used.get(str(full).lower(), "")})
                if len(entries) >= 20000:
                    break
        lib = {"entries": entries, "missing": missing, "scanned": time.time(), "dirs": s["library_dirs"],
               "groups": sorted({e["group"] for e in entries})}
        with self._lock:
            self._library = lib
            self._photos = photos
        return lib

    @staticmethod
    def _photo_index(dirs: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for root in dirs:
            if not Path(root).is_dir():
                continue
            for dirpath, dnames, files in os.walk(root):
                dnames[:] = [d for d in dnames if not d.startswith(".")]
                for f in files:
                    if f.lower().endswith(IMAGE_EXTS):
                        out.setdefault(Path(f).stem.lower(), str(Path(dirpath) / f))
        return out

    def photo(self, name: str, size: int = 320) -> bytes:
        """The picture of a figure from the photo folders, small (cached in the data folder)."""
        self.library()
        path = getattr(self, "_photos", {}).get(name.lower())
        if not path:
            raise NikiasError("not_found", "No hay foto de esa figura.")
        cache = self.config.data_dir / "thumbs"
        cache.mkdir(parents=True, exist_ok=True)
        st = Path(path).stat()
        key = cache / f"{uuid.uuid5(uuid.NAMESPACE_URL, path + str(st.st_mtime_ns) + str(size)).hex}.jpg"
        if not key.is_file():
            from PIL import Image
            img = Image.open(path).convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            img.save(key, "JPEG", quality=86)
        return key.read_bytes()

    # ------------------------------------------------------------------ projects
    def _figure(self, pid: str) -> Figure:
        with self._lock:
            fig = self._figures.get(pid)
            if fig is not None:
                self._figures.move_to_end(pid)
                return fig
        d = self.store.dir(pid)
        project = self.store.get(pid)
        if not (d / "mesh.npz").is_file():
            raise NikiasError("failed", "A ese proyecto le falta la malla.", "Bórralo y vuelve a abrir la figura.")
        mesh = meshes.load_npz(d / "mesh.npz", project.get("name", ""))
        m = len(mesh.F)
        regions = np.load(d / "regions.npy") if (d / "regions.npy").is_file() else np.zeros(m, np.int32)
        labels = np.load(d / "labels.npy") if (d / "labels.npy").is_file() else np.full(m, UNSET, np.uint8)
        rgb = np.load(d / "rgb.npy") if (d / "rgb.npy").is_file() else None
        fig = Figure(mesh, regions.astype(np.int32), labels.astype(np.uint8), rgb)
        with self._lock:
            self._figures[pid] = fig
            while len(self._figures) > 2:
                self._figures.popitem(last=False)
        return fig

    def _save_labels(self, pid: str, fig: Figure) -> None:
        np.save(self.store.dir(pid) / "labels.npy", fig.labels)

    def create_project(self, source_path: str, name: str = "") -> dict[str, Any]:
        path = Path(str(source_path).strip().strip('"'))
        if not path.is_file():
            raise NikiasError("not_found", f"No encuentro {path}.", "Usa library_scan para ver las figuras de la biblioteca.")
        if path.suffix.lower() not in FIGURE_EXTS:
            raise NikiasError("invalid", "La figura tiene que ser un STL.")
        for p in self.store.list():
            if str(p.get("source", {}).get("path", "")).lower() == str(path).lower():
                return self.project(p["id"])
        try:
            mesh = meshes.load_stl(path)
        except (ValueError, OSError) as exc:
            raise NikiasError("invalid", f"No se puede leer el STL: {exc}") from None
        if len(mesh.F) > MAX_FACES:
            raise NikiasError("too_big", f"La figura tiene {len(mesh.F):,} triángulos; el máximo es {MAX_FACES:,}.".replace(",", "."))
        s = self.settings.get()
        entry = next((e for e in self.library()["entries"] if e["path"].lower() == str(path).lower()), None)
        pid = self.store.new_id()
        d = self.store.dir(pid)
        d.mkdir(parents=True, exist_ok=True)
        meshes.save_npz(mesh, d / "mesh.npz")
        regions = reg.segment(mesh, s["detail"], s["max_regions"])
        np.save(d / "regions.npy", regions)
        labels = np.full(len(mesh.F), UNSET, np.uint8)
        np.save(d / "labels.npy", labels)
        self._write_view(d, mesh)
        project = {"id": pid, "name": (name or path.stem).strip()[:120], "created": time.time(),
                   "source": {"path": str(path), "group": entry["group"] if entry else path.parent.name,
                              "variants": entry["variants"] if entry else [], "parts": entry["parts"] if entry else []},
                   "mesh": mesh.stats(), "detail": s["detail"], "regions": int(regions.max()) + 1, "palette": [], "sources": [],
                   "method": "", "director": {}, "exports": []}
        self.store.save(project)
        with self._lock:
            self._figures[pid] = Figure(mesh, regions, labels)
            self._library = None
        return self.project(pid)

    @staticmethod
    def _write_view(d: Path, mesh: meshes.Mesh) -> None:
        with open(d / "figure.bin", "wb") as fh:
            fh.write(b"NKS1")
            fh.write(np.array([len(mesh.P), len(mesh.F)], "<u4").tobytes())
            fh.write(mesh.P.astype("<f4").tobytes())
            fh.write(mesh.F.astype("<u4").tobytes())

    def project(self, pid: str) -> dict[str, Any]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        k = len(project["palette"])
        painted = fig.labels != UNSET
        project["colored_share"] = round(float(fig.mesh.areas[painted].sum() / max(fig.mesh.areas.sum(), 1e-30)), 4)
        project["colors"] = lab.stats(fig.labels, fig.mesh, k) if k else []
        project["can_undo"] = len(fig.undo)
        project["has_model_colors"] = fig.rgb is not None
        return project

    def projects(self) -> list[dict[str, Any]]:
        return [{"id": p["id"], "name": p["name"], "group": p.get("source", {}).get("group", ""), "updated": p.get("updated"),
                 "palette": [c["hex"] for c in p.get("palette", [])], "method": p.get("method", ""), "faces": p.get("mesh", {}).get("faces", 0)}
                for p in self.store.list()]

    def rename_project(self, pid: str, name: str) -> dict[str, Any]:
        project = self.store.get(pid)
        project["name"] = name.strip()[:120] or project["name"]
        self.store.save(project)
        return self.project(pid)

    def delete_project(self, pid: str) -> dict[str, Any]:
        for j in self.jobs.values():
            if j["project_id"] == pid and j["state"] in ("queued", "running"):
                raise NikiasError("busy", "Ese proyecto tiene un trabajo en marcha.", "Cancélalo o espera a que acabe.")
        self.store.delete(pid)
        with self._lock:
            self._figures.pop(pid, None)
            self._library = None
        return {"deleted": pid}

    def view_file(self, pid: str) -> Path:
        d = self.store.dir(pid)
        if not (d / "figure.bin").is_file():
            self._write_view(d, self._figure(pid).mesh)
        return d / "figure.bin"

    def face_data(self, pid: str, which: str) -> bytes:
        fig = self._figure(pid)
        return fig.labels.tobytes() if which == "labels" else np.minimum(fig.regions, 65535).astype("<u2").tobytes()

    # ------------------------------------------------------------------ regions
    def set_regions(self, pid: str, detail: Optional[int] = None, max_regions: Optional[int] = None) -> dict[str, Any]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        detail = int(detail if detail is not None else project.get("detail", self.settings["detail"]))
        fig.regions = reg.segment(fig.mesh, detail, int(max_regions or self.settings["max_regions"]))
        np.save(self.store.dir(pid) / "regions.npy", fig.regions)
        project["detail"], project["regions"] = detail, int(fig.regions.max()) + 1
        self.store.save(project)
        return {"detail": detail, "regions": reg.describe(fig.mesh, fig.regions)}

    def regions(self, pid: str) -> dict[str, Any]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        rows = reg.describe(fig.mesh, fig.regions)
        k = len(project["palette"])
        for row in rows:                                    # the colour each region mostly has now
            sel = fig.regions == row["id"]
            vals = fig.labels[sel]
            vals = vals[vals != UNSET]
            row["color"] = int(np.bincount(vals, minlength=max(k, 1)).argmax()) if len(vals) else None
        return {"detail": project.get("detail"), "regions": rows}

    # ------------------------------------------------------------------ colour sources
    def add_source(self, pid: str, path: str, name: str = "") -> dict[str, Any]:
        """A textured model, a picture or a folder of them as a colour reference for the project."""
        p = Path(str(path).strip().strip('"'))
        if p.is_dir():
            files = sorted(f for f in p.iterdir() if f.is_file())
            models = sorted((f for f in files if f.suffix.lower() in MODEL_EXTS), key=lambda f: modelio.MODEL_RANK.get(f.suffix.lower(), 9))
            images = [f for f in files if f.suffix.lower() in IMAGE_EXTS]
            if models:
                return self.add_source(pid, str(models[0]), name)
            if not images:
                raise NikiasError("invalid", "En esa carpeta no hay modelos ni imágenes.")
            last = None
            for f in images[:8]:
                last = self.add_source(pid, str(f))
            return last
        if not p.is_file():
            raise NikiasError("not_found", f"No encuentro {p}.")
        ext = p.suffix.lower()
        if ext in IMAGE_EXTS:
            return self.add_image(pid, name or p.stem, p.read_bytes(), str(p))
        if ext not in MODEL_EXTS:
            raise NikiasError("invalid", f"No sé leer {ext}.", "Modelos: OBJ, GLB, glTF, DAE, FBX, .blend. Imágenes: PNG, JPG, WebP.")
        project = self.store.get(pid)
        fig = self._figure(pid)
        d = self.store.dir(pid)
        sid = uuid.uuid4().hex[:8]
        model_path = p
        if ext in modelio.BLENDER_EXTS:
            exe = blender.find_blender(self.settings["blender"])
            if not exe:
                raise NikiasError("unavailable", "Hace falta Blender para leer FBX y .blend y no lo encuentro.", "Indica su ruta en Ajustes.")
            model_path = d / f"src_{sid}.glb"
            try:
                blender.to_glb(exe, p, model_path)
            except Exception as exc:  # noqa: BLE001
                raise NikiasError("failed", f"Blender no pudo abrir el modelo: {exc}"[:400]) from None
        try:
            scene = modelio.load_model(model_path)
        except Exception as exc:  # noqa: BLE001
            raise NikiasError("invalid", f"No se puede leer el modelo: {type(exc).__name__}: {exc}"[:300]) from None
        pts, rgb = source.sample_scene(scene, self.settings["samples"])
        if len(pts) < 100:
            raise NikiasError("invalid", "El modelo no tiene superficie con color.")
        fit = source.register(pts, fig.mesh)
        moved = source.apply(fit["matrix"], pts)
        np.savez(d / f"src_{sid}.npz", points=moved.astype(np.float32), rgb=rgb)
        textures = sum(1 for m in scene.materials if m.albedo is not None)
        good = fit["fit"] <= float(self.settings["fit_good"])
        entry = {"id": sid, "kind": "model", "name": name or p.stem, "path": str(p), "fit": fit["fit"], "fit_p90": fit["fit_p90"], "good": good,
                 "textures": textures, "materials": len(scene.materials),
                 "note": "" if good else "La forma no coincide con la figura (otra pose u otro modelo): sirve de referencia para la IA, no para pasar el color directamente."}
        render.png_file(render.sheet([render.points_image(moved, rgb, yaw=y, pitch=p_, size=520) for y, p_ in ((0, 10), (90, 10), (180, 10), (-90, 10))]),
                        d / f"src_{sid}.png")
        project["sources"].append(entry)
        self.store.save(project)
        return entry

    def add_image(self, pid: str, name: str, data: bytes, path: str = "") -> dict[str, Any]:
        project = self.store.get(pid)
        if len(data) > 60_000_000:
            raise NikiasError("too_big", "La imagen pesa demasiado.")
        try:
            img = render.fit_image(data, 1400)
        except Exception:  # noqa: BLE001
            raise NikiasError("invalid", "Eso no es una imagen que se pueda leer.") from None
        if sum(1 for s in project["sources"] if s["kind"] == "image") >= 24:
            raise NikiasError("invalid", "Ya hay 24 imágenes de referencia; quita alguna.")
        sid = uuid.uuid4().hex[:8]
        render.png_file(img, self.store.dir(pid) / f"src_{sid}.png")
        entry = {"id": sid, "kind": "image", "name": (name or "referencia").strip()[:120], "path": path, "size": list(img.size)}
        project["sources"].append(entry)
        self.store.save(project)
        return entry

    def remove_source(self, pid: str, sid: str) -> dict[str, Any]:
        project = self.store.get(pid)
        if not any(s["id"] == sid for s in project["sources"]):
            raise NikiasError("not_found", "Ese proyecto no tiene esa referencia.")
        project["sources"] = [s for s in project["sources"] if s["id"] != sid]
        self.store.save(project)
        d = self.store.dir(pid)
        for f in (d / f"src_{sid}.png", d / f"src_{sid}.npz", d / f"src_{sid}.glb"):
            f.unlink(missing_ok=True)
        return {"removed": sid}

    def source_picture(self, pid: str, sid: str) -> bytes:
        self.store.get(pid)
        if not sid.isalnum():
            raise NikiasError("not_found", "No existe esa referencia.")
        f = self.store.dir(pid) / f"src_{sid}.png"
        if not f.is_file():
            raise NikiasError("not_found", "No existe esa referencia.")
        return f.read_bytes()

    def find_sources(self, pid: str, query: str = "", limit: int = 40) -> dict[str, Any]:
        """Files in the reference folders whose name looks like the figure's (or contains ``query``)."""
        project = self.store.get(pid)
        with self._lock:
            index = self._reference_index
        if index is None:
            index = []
            for root in self.settings["reference_dirs"]:
                if not Path(root).is_dir():
                    continue
                for dirpath, dnames, files in os.walk(root):
                    dnames[:] = [x for x in dnames if not x.startswith(".") and x.lower() not in SKIP_DIRS]
                    if len(Path(dirpath).relative_to(root).parts) >= 7:
                        dnames[:] = []
                    for f in files:
                        if f.lower().endswith(MODEL_EXTS + IMAGE_EXTS):
                            index.append((f.lower(), str(Path(dirpath) / f)))
                    if len(index) > 200_000:
                        break
            with self._lock:
                self._reference_index = index
        want = (query or project["name"]).lower().strip()
        words = [w for w in want.replace("-", " ").replace("_", " ").split() if len(w) > 1]
        hits = []
        for low, full in index:
            hay = (low + " " + Path(full).parent.name.lower())
            score = (3 if Path(low).stem == want else 0) + sum(1 for w in words if w in hay)
            if score and (score >= min(len(words), 2) or Path(low).stem == want):
                kind = "model" if low.endswith(MODEL_EXTS) else "image"
                hits.append((-(score + (0.5 if kind == "model" else 0)), full, kind))
        hits.sort()
        return {"query": want, "folders": self.settings["reference_dirs"],
                "candidates": [{"path": full, "kind": kind, "name": Path(full).name} for _, full, kind in hits[:limit]]}

    # ------------------------------------------------------------------ colouring
    def start_color(self, pid: str, mode: str = "auto", colors: int = 0, wish: str = "", source_id: str = "", rounds: int = -1) -> dict[str, Any]:
        project = self.store.get(pid)
        models = [s for s in project["sources"] if s["kind"] == "model"]
        if mode == "auto":
            mode = "model" if any(s.get("good") for s in models) else "ai"
        if mode == "model" and not models:
            raise NikiasError("invalid", "No hay ningún modelo con texturas en el proyecto.", "Añade uno con source_add o usa mode ai.")
        if mode == "ai":
            st = self.llm.status()
            if not st["available"]:
                raise NikiasError("unavailable", "No hay modelo de visión disponible para colorear con IA.", st.get("reason", ""))
        return self._enqueue("color", pid, {"mode": mode, "colors": int(colors or 0), "wish": wish.strip(), "source_id": source_id,
                                             "rounds": int(rounds)})

    def model_samples(self, pid: str, sid: str) -> tuple[np.ndarray, np.ndarray]:
        f = self.store.dir(pid) / f"src_{sid}.npz"
        if not f.is_file():
            raise NikiasError("not_found", "Faltan los datos de ese modelo; vuelve a añadirlo.")
        with np.load(f) as z:
            return z["points"].astype(np.float64), z["rgb"]

    def _color_from_model(self, pid: str, say, params: dict[str, Any]) -> dict[str, Any]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        models = [s for s in project["sources"] if s["kind"] == "model"]
        src = next((s for s in models if s["id"] == params.get("source_id")), None) or min(models, key=lambda s: s["fit"])
        say("pasando el color del modelo a la figura", 0.15)
        pts, rgb = self.model_samples(pid, src["id"])
        face_rgb, gap = source.transfer(fig.mesh, pts, rgb)
        fig.rgb = face_rgb
        np.save(self.store.dir(pid) / "rgb.npy", face_rgb)
        say("eligiendo los colores de impresión", 0.6)
        s = self.settings.get()
        suggestion = pal.suggest_count(face_rgb, fig.mesh.areas, 2, 8, s["accent"])
        k = int(params.get("colors") or s["colors"])
        colors = pal.reduce(face_rgb, fig.mesh.areas, k, s["accent"])
        self._push_undo(fig)
        fig.labels = pal.assign(face_rgb, colors)
        say("limpiando bordes e islas", 0.8)
        cleaned = self._clean(fig, s["min_detail_mm"], s["smooth"])
        project = self.store.get(pid)
        project["palette"] = self._palette_entries(colors)
        project["method"] = "model"
        project["transfer"] = {"source": src["id"], "fit": src["fit"], "good": bool(src.get("good")), "suggested_colors": suggestion["count"],
                               "errors": suggestion["errors"], "far_share": round(float((gap > fig.mesh.diagonal * 0.03).mean()), 4), **cleaned}
        self.store.save(project)
        self._save_labels(pid, fig)
        return {"method": "model", "source": src["name"], "fit": src["fit"], "trusted": bool(src.get("good")), "colors": len(colors),
                "suggested_colors": suggestion["count"], **cleaned}

    def _palette_entries(self, colors) -> list[dict[str, Any]]:
        spools = self.settings["filaments"]
        out = []
        for rgb in np.asarray(colors).reshape(-1, 3):
            entry = {"hex": pal.to_hex(rgb), "name": pal.name_of(rgb)}
            near = pal.nearest_filament(rgb, spools)
            if near:
                entry["filament"] = near
            out.append(entry)
        return out

    def _clean(self, fig: Figure, min_detail_mm: float, smooth: int) -> dict[str, Any]:
        fig.labels = lab.smooth(fig.labels, fig.mesh, smooth)
        removed = 0
        if min_detail_mm > 0:
            fig.labels, removed = lab.remove_islands(fig.labels, fig.mesh, float(min_detail_mm) ** 2)
        return {"islands_removed": removed}

    def clean(self, pid: str, min_detail_mm: Optional[float] = None, smooth: Optional[int] = None) -> dict[str, Any]:
        fig = self._figure(pid)
        s = self.settings.get()
        self._push_undo(fig)
        out = self._clean(fig, s["min_detail_mm"] if min_detail_mm is None else float(min_detail_mm), s["smooth"] if smooth is None else int(smooth))
        self._save_labels(pid, fig)
        return {**out, "project": self.project(pid)}

    # ------------------------------------------------------------------ palette
    def set_palette(self, pid: str, colors: Optional[list[dict[str, Any]]] = None, count: Optional[int] = None, merge: Optional[list[int]] = None,
                    base: Optional[int] = None, snap: bool = False) -> dict[str, Any]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        palette = list(project["palette"])
        spools = self.settings["filaments"]
        changed_labels = False
        if colors is not None:
            for k, c in enumerate(colors):
                if c is None:
                    continue
                entry = dict(palette[k]) if k < len(palette) else {"hex": "#808080", "name": ""}
                if c.get("hex"):
                    rgb = pal.from_hex(c["hex"])
                    entry["hex"] = pal.to_hex(rgb)
                    if not c.get("name") and (k >= len(palette) or not palette[k].get("custom_name")):
                        entry["name"] = pal.name_of(rgb)
                    near = pal.nearest_filament(rgb, spools)
                    entry["filament"] = near if near else None
                    entry.pop("filament_chosen", None)
                if c.get("name"):
                    entry["name"], entry["custom_name"] = str(c["name"]).strip()[:40], True
                if c.get("filament"):
                    spool = next((f for f in spools if f["name"].lower() == str(c["filament"]).lower()), None)
                    if spool is None:
                        raise NikiasError("invalid", f"No hay ningún filamento llamado {c['filament']}.", "Mira settings_get: filaments.")
                    entry["filament"] = dict(spool, delta=round(pal.distance(pal.from_hex(entry["hex"]), pal.from_hex(spool["hex"])), 1))
                    entry["filament_chosen"] = True
                if k < len(palette):
                    palette[k] = entry
                else:
                    palette.append(entry)
            if len(palette) > 16:
                raise NikiasError("invalid", "Una figura admite como mucho 16 colores.")
        if merge:
            a, b = int(merge[0]), int(merge[1])
            if a == b or not (0 <= a < len(palette)) or not (0 <= b < len(palette)):
                raise NikiasError("invalid", "merge necesita dos colores distintos de la paleta.")
            self._push_undo(fig)
            fig.labels[fig.labels == b] = a
            above = (fig.labels > b) & (fig.labels != UNSET)
            fig.labels[above] -= 1
            palette.pop(b)
            changed_labels = True
        if base is not None and palette:
            b = int(base)
            if not (0 <= b < len(palette)):
                raise NikiasError("invalid", "Ese color no está en la paleta.")
            if b:
                self._push_undo(fig)
                is0, isb = fig.labels == 0, fig.labels == b
                fig.labels[is0], fig.labels[isb] = b, 0
                palette[0], palette[b] = palette[b], palette[0]
                changed_labels = True
        if count is not None and int(count) != len(palette):
            count = int(count)
            if not (1 <= count <= 16):
                raise NikiasError("invalid", "El número de colores va de 1 a 16.")
            self._push_undo(fig)
            if fig.rgb is not None and project.get("method") == "model":
                cols = pal.reduce(fig.rgb, fig.mesh.areas, count, self.settings["accent"])
                fig.labels = pal.assign(fig.rgb, cols)
                self._clean(fig, self.settings["min_detail_mm"], self.settings["smooth"])
                palette = self._palette_entries(cols)
            else:
                while len(palette) > count:                 # the two colours that look most alike become one
                    labs = pal.rgb_to_lab(np.array([pal.from_hex(c["hex"]) for c in palette]))
                    dist = ((labs[:, None] - labs[None]) ** 2).sum(2) + np.eye(len(palette)) * 1e12
                    a, b = sorted(np.unravel_index(int(dist.argmin()), dist.shape))
                    fig.labels[fig.labels == b] = a
                    above = (fig.labels > b) & (fig.labels != UNSET)
                    fig.labels[above] -= 1
                    palette.pop(int(b))
                spare = [f for f in spools if all(pal.distance(pal.from_hex(f["hex"]), pal.from_hex(c["hex"])) > 12 for c in palette)]
                while len(palette) < count:
                    f = spare.pop(0) if spare else {"hex": "#808080", "name": "gris"}
                    palette.append({"hex": f["hex"], "name": pal.name_of(pal.from_hex(f["hex"])), "filament": pal.nearest_filament(pal.from_hex(f["hex"]), spools)})
            changed_labels = True
        if snap:
            for entry in palette:
                near = entry["filament"] if entry.get("filament_chosen") and entry.get("filament") else pal.nearest_filament(pal.from_hex(entry["hex"]), spools)
                if near:
                    entry["hex"], entry["filament"] = near["hex"], dict(near, delta=0.0)
                    if not entry.get("custom_name"):
                        entry["name"] = pal.name_of(pal.from_hex(near["hex"]))
        project["palette"] = palette
        self.store.save(project)
        if changed_labels:
            self._save_labels(pid, fig)
        return self.project(pid)

    # ------------------------------------------------------------------ painting
    @staticmethod
    def _push_undo(fig: Figure) -> None:
        fig.undo.append(fig.labels.copy())

    def paint(self, pid: str, color: int, regions: Optional[list[int]] = None, faces: Optional[list[int]] = None,
              brush: Optional[dict[str, Any]] = None, everything: bool = False, only_unset: bool = False) -> dict[str, Any]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        if not (0 <= int(color) < len(project["palette"])):
            raise NikiasError("invalid", "Ese color no está en la paleta.", "Añádelo antes con palette_set.")
        color = int(color)
        before = fig.labels.copy()
        allowed = (fig.labels == UNSET) if only_unset else None
        if everything:
            fig.labels[:] = np.where(allowed, color, fig.labels) if allowed is not None else color
        if regions:
            sel = np.isin(fig.regions, np.asarray(regions, np.int64))
            if allowed is not None:
                sel &= allowed
            fig.labels[sel] = color
        if faces:
            comp, _ = lab.islands(before, fig.mesh)
            ids = np.asarray([f for f in faces if 0 <= int(f) < len(before)], np.int64)
            sel = np.isin(comp, np.unique(comp[ids])) if len(ids) else np.zeros(len(before), bool)
            fig.labels[sel] = color
        if brush:
            radius = float(brush.get("radius_mm") or 1.0)
            normals = brush.get("normals") or []
            for n, point in enumerate(brush.get("points") or []):
                lab.brush(fig.labels, fig.mesh, point, radius, color, normals[n] if n < len(normals) else brush.get("normal"), allowed)
        changed = int((before != fig.labels).sum())
        if changed:
            fig.undo.append(before)
            self._save_labels(pid, fig)
            if not project.get("method"):
                project["method"] = "manual"
                self.store.save(project)
        return {"changed_faces": changed, "can_undo": len(fig.undo)}

    def undo(self, pid: str) -> dict[str, Any]:
        fig = self._figure(pid)
        if not fig.undo:
            raise NikiasError("invalid", "No hay nada que deshacer.")
        fig.labels = fig.undo.pop()
        self._save_labels(pid, fig)
        return {"can_undo": len(fig.undo)}

    def pick(self, pid: str, face: int) -> dict[str, Any]:
        fig = self._figure(pid)
        if not (0 <= int(face) < len(fig.labels)):
            raise NikiasError("invalid", "Esa cara no existe.")
        value = int(fig.labels[int(face)])
        return {"region": int(fig.regions[int(face)]), "color": None if value == UNSET else value}

    # ------------------------------------------------------------------ pictures
    def face_colors(self, pid: str, what: str = "color") -> Optional[np.ndarray]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        if what == "clay":
            return None
        if what == "regions":
            return render.region_colors(int(fig.regions.max()) + 1)[fig.regions]
        if what == "model":
            if fig.rgb is None:
                raise NikiasError("invalid", "Todavía no se ha pasado color desde ningún modelo.")
            return fig.rgb
        table = np.full((256, 3), render.UNSET_RGB, np.uint8)
        for k, c in enumerate(project["palette"]):
            table[k] = pal.from_hex((c.get("filament") or {}).get("hex") or c["hex"]) if what == "filament" else pal.from_hex(c["hex"])
        return table[fig.labels]

    def look(self, pid: str, what: str = "color", view: str = "iso", yaw: Optional[float] = None, pitch: Optional[float] = None, size: int = 900,
             region: Optional[list[float]] = None, numbers: Optional[bool] = None, views: Optional[list[str]] = None) -> bytes:
        fig = self._figure(pid)
        size = int(np.clip(size, 200, 1600))
        if what == "sources":
            return render.png(self.reference_sheet(pid, size))
        if what not in ("color", "filament", "regions", "clay", "model", "compare"):
            raise NikiasError("invalid", "what: color, filament, regions, clay, model, compare o sources.")
        names = views or [view]
        for v in names:
            if v not in render.VIEWS and (yaw is None):
                raise NikiasError("invalid", f"Vista desconocida: {v}.", "Vistas: " + ", ".join(render.VIEWS))
        cull = fig.mesh.stats()["open_edges"] < 0.02 * len(fig.mesh.F)
        pictures = []
        for v in names:
            y, p = render.VIEWS.get(v, (35, 25))
            y, p = (yaw if yaw is not None else y), (pitch if pitch is not None else p)
            frame = render.rasterize(fig.mesh.P, fig.mesh.F, yaw=y, pitch=p, size=size, region=region, cull=cull)
            if what == "compare":
                pictures += [render.shade(frame, None), render.shade(frame, self.face_colors(pid, "color"))]
                continue
            img = render.shade(frame, self.face_colors(pid, what), flat=0.35 if what == "regions" else 0.0)
            if what == "regions" and numbers is not False:
                img, _ = render.number_regions(img, frame, fig.regions)
            pictures.append(img)
        return render.png(pictures[0] if len(pictures) == 1 else render.sheet(pictures, columns=2 if len(pictures) > 3 else 0))

    def reference_sheet(self, pid: str, size: int = 900, limit: int = 6):
        project = self.store.get(pid)
        d = self.store.dir(pid)
        from PIL import Image
        imgs, names = [], []
        for s in project["sources"][:limit]:
            f = d / f"src_{s['id']}.png"
            if f.is_file():
                im = Image.open(f).convert("RGB")
                im.thumbnail((size, size), Image.LANCZOS)
                imgs.append(im)
                names.append(s["name"])
        if not imgs:
            raise NikiasError("invalid", "El proyecto no tiene referencias de color.", "Añade un modelo con texturas o imágenes con source_add.")
        return render.sheet(imgs, names, columns=min(len(imgs), 3))

    # ------------------------------------------------------------------ export
    def export(self, pid: str, formats: Optional[list[str]] = None, folder: str = "", variants: bool = True) -> dict[str, Any]:
        project = self.store.get(pid)
        fig = self._figure(pid)
        if not project["palette"]:
            raise NikiasError("invalid", "La figura todavía no tiene colores.", "Colorea con color_start o pinta con paint.")
        formats = [f.lower() for f in (formats or self.settings["export_formats"])]
        bad = [f for f in formats if f not in ("bambu", "prusa", "color", "obj", "stl")]
        if bad:
            raise NikiasError("invalid", f"Formato desconocido: {bad[0]}.", "Formatos: bambu, prusa, color, obj, stl.")
        src = Path(project["source"]["path"])
        out_dir = Path(folder) if folder else (Path(self.settings["export_dir"]) / project["source"].get("group", "") if self.settings["export_dir"]
                                               else src.parent / "Multicolor")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise NikiasError("failed", f"No puedo crear {out_dir}: {exc}") from None
        entries = [{"hex": (c.get("filament") or {}).get("hex") or c["hex"], "name": _name_of(c),
                    "material": (c.get("filament") or {}).get("material") or "PLA"} for c in project["palette"]]
        unset = int((fig.labels == UNSET).sum())
        files: list[str] = []

        def write(stem: str, mesh: meshes.Mesh, labels: np.ndarray) -> None:
            labels = np.where(labels == UNSET, 0, labels)
            for f in formats:
                if f in export.DIALECTS:
                    suffix = {"bambu": "Bambu-Orca", "prusa": "Prusa", "color": "color"}[f]
                    files.append(str(export.write_3mf(out_dir / f"{stem} {suffix}.3mf", mesh.P, mesh.F, labels, entries, stem, f)))
                elif f == "obj":
                    files.extend(str(x) for x in export.write_obj(out_dir / f"{stem} color.obj", mesh.P, mesh.F, labels, entries, stem))
                else:
                    files.extend(str(x) for x in export.write_color_stls(out_dir, stem, mesh.P, mesh.F, labels, entries))

        write(src.stem, fig.mesh, fig.labels)
        done_variants, failed = [], []
        if variants:
            tree = None
            for v in project["source"].get("variants", []):
                vp = Path(v["path"])
                try:
                    vm = meshes.load_stl(vp)
                except (OSError, ValueError) as exc:
                    failed.append({"name": v["name"], "error": str(exc)[:200]})
                    continue
                tree = tree or cKDTree(fig.mesh.centroids)
                placed = source.register(vm.centroids, fig.mesh)       # a variant is saved turned or moved: lay it over the figure first
                if placed["fit"] > 0.02:
                    failed.append({"name": v["name"], "error": "no coincide con la figura (encaje %.1f %%)" % (100 * placed["fit"])})
                    continue
                dist, idx = tree.query(source.apply(placed["matrix"], vm.centroids), workers=-1)
                vl = fig.labels[idx]
                far = dist > max(fig.mesh.diagonal * 0.01, fig.mesh.mean_edge * 4)      # the cut that makes the variant: new faces
                if far.any():
                    vl = lab.smooth(np.where(far, UNSET, vl).astype(np.uint8), vm, 0)
                    vl = self._fill_unset(vl, vm)
                write(vp.stem, vm, vl)
                done_variants.append({"name": v["name"], "fit": placed["fit"], "new_faces_share": round(float(far.mean()), 4)})
        legend = out_dir / f"{src.stem} colores.txt"
        lines = [f"{project['name']}", ""]
        for k, c in enumerate(project["palette"]):
            spool = c.get("filament") or {}
            lines.append(f"Filamento {k + 1}: {_name_of(c)} {c['hex']}" + (f"  ->  {spool.get('name')} {spool.get('material', '')} {spool.get('hex')}" if spool else ""))
        legend.write_text("\n".join(lines) + "\n", encoding="utf-8")
        files.append(str(legend))
        project["exports"] = [{"when": time.time(), "folder": str(out_dir), "files": [Path(f).name for f in files]}] + project.get("exports", [])[:4]
        self.store.save(project)
        return {"folder": str(out_dir), "files": files, "variants": done_variants, "variants_failed": failed, "uncolored_faces": unset,
                "note": "Las caras sin colorear salen con el filamento 1." if unset else ""}

    @staticmethod
    def _fill_unset(labels: np.ndarray, mesh: meshes.Mesh) -> np.ndarray:
        """Faces without a colour take the one around them, ring by ring."""
        labels = labels.copy()
        N = mesh.neighbours
        for _ in range(2000):
            todo = np.flatnonzero(labels == UNSET)
            if not len(todo):
                break
            nb = N[todo]
            vals = np.where(nb >= 0, labels[np.where(nb >= 0, nb, 0)], UNSET).astype(np.int64)
            vals[vals == UNSET] = -1
            best = vals.max(1)
            got = best >= 0
            if not got.any():
                break
            labels[todo[got]] = best[got].astype(np.uint8)
        labels[labels == UNSET] = 0
        return labels

    # ------------------------------------------------------------------ jobs
    def _job_public(self, job: dict[str, Any]) -> dict[str, Any]:
        return {k: job[k] for k in ("id", "kind", "project_id", "state", "stage", "progress", "error", "created", "finished", "result",
                                    "cancellable", "cancel_requested")}

    def job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise NikiasError("not_found", "No hay ningún trabajo con ese id.")
            return self._job_public(job)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise NikiasError("not_found", "No hay ningún trabajo con ese id.")
            if job["state"] == "cancelled" or job["cancel_requested"]:
                return self._job_public(job)
            if job["state"] not in ("queued", "running"):
                raise NikiasError("conflict", "Ese trabajo ya ha terminado.")
            job.update(cancel_requested=True, cancellable=False, stage="cancelando")
            if job["state"] == "queued":
                job.update(state="cancelled", stage="cancelado", finished=time.time())
            return self._job_public(job)

    def _enqueue(self, kind: str, pid: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.store.get(pid)
        with self._lock:
            for j in self.jobs.values():
                if j["project_id"] == pid and j["state"] in ("queued", "running"):
                    raise NikiasError("busy", "Ese proyecto ya tiene un trabajo en marcha.", "Consulta job_status y espera a que acabe.")
            job = {"id": uuid.uuid4().hex[:10], "kind": kind, "project_id": pid, "state": "queued", "stage": "en cola", "progress": 0.0,
                   "error": "", "created": time.time(), "finished": None, "result": None, "params": params or {}, "cancellable": True,
                   "cancel_requested": False}
            self.jobs[job["id"]] = job
            while len(self.jobs) > 60:
                expired = next((k for k, v in self.jobs.items() if v["state"] not in ("queued", "running")), None)
                if expired is None:
                    break
                self.jobs.pop(expired)
        if self.config.background and self._worker is not None:
            self._queue.put(job["id"])
        else:
            self._execute(job)
        return self._job_public(job)

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            job = self.jobs.get(job_id)
            if job is not None:
                self._execute(job)

    def _execute(self, job: dict[str, Any]) -> None:
        with self._lock:
            if job["state"] != "queued":
                return
            job["state"] = "running"

        def say(stage: str, frac: float) -> None:
            with self._lock:
                if job["cancel_requested"]:
                    raise _JobCancelled()
                job["stage"], job["progress"] = stage, round(float(frac), 3)

        try:
            params = job["params"]
            if params["mode"] == "model":
                job["result"] = self._color_from_model(job["project_id"], say, params)
            else:
                job["result"] = director.run(self, job["project_id"], say, params)
            job["state"], job["stage"], job["progress"] = "done", "hecho", 1.0
        except _JobCancelled:
            job.update(state="cancelled", stage="cancelado", finished=time.time())
        except NikiasError as exc:
            job["state"], job["error"] = "error", str(exc) + (f" {exc.hint}" if getattr(exc, "hint", "") else "")
        except MemoryError:
            job["state"], job["error"] = "error", "No hay memoria para una figura tan grande."
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s", job["id"])
            job["state"], job["error"] = "error", f"{type(exc).__name__}: {exc}"
        with self._lock:
            job.update(cancellable=False, finished=job["finished"] or time.time())
