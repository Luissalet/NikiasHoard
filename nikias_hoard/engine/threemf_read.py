"""Reading a painted 3MF back: the mesh, the filament of each triangle and the palette.

The dialects ``export.write_3mf`` writes are read: a Bambu Studio / OrcaSlicer project or painted model (the mesh in
``3D/Objects/*.model``, pointed at by ``p:path``, with ``paint_color`` on each triangle), PrusaSlicer
(``slic3rpe:mmu_segmentation``) and the 3MF materials extension (a colour group with ``pid``/``p1``). The XML is read
in streaming, so a figure of 800.000 triangles is never held as a tree.
"""

from __future__ import annotations

import json
import re
import zipfile
from array import array
from pathlib import Path
from typing import Any, Optional
from xml.etree import ElementTree as ET

import numpy as np

from .export import CORE_NS, MATERIAL_NS, PRODUCTION_NS, SLIC3R_NS

CORE = "{%s}" % CORE_NS
MAT = "{%s}" % MATERIAL_NS
PROD = "{%s}" % PRODUCTION_NS
SLIC3R = "{%s}" % SLIC3R_NS
HEX = re.compile(r"#([0-9a-fA-F]{6})")
MAIN = "3D/3dmodel.model"
SETTINGS = "Metadata/project_settings.config"


def _member(path: Optional[str]) -> str:
    """The name of a model file inside the package, from the path a component or a relationship points at."""
    return (path or "").lstrip("/")


def _matrix(text: Optional[str]) -> Optional[np.ndarray]:
    """A 3MF transform (12 numbers, row-major, last column omitted) as a 4x3 matrix."""
    if not text:
        return None
    values = [float(x) for x in text.split()]
    if len(values) != 12:
        raise ValueError(f"a 3MF transform holds 12 numbers, not {len(values)}")
    return np.asarray(values, np.float64).reshape(4, 3)


def _placed(P: np.ndarray, M: Optional[np.ndarray]) -> np.ndarray:
    return P if M is None else P @ M[:3] + M[3]


def _nibble(ch: str) -> int:
    try:
        return int(ch, 16)
    except ValueError:
        return -1


def _code_label(code: str) -> tuple[int, bool]:
    """Palette index of a triangle's paint code, and whether the triangle carried a subdivided painting.

    A whole triangle is ``""`` (the object's own filament), ``"4"`` (filament 1), ``"8"`` (filament 2) or
    ``"<hex>C"`` (filament hex + 3), the inverse of ``export.paint_code``. A longer string paints parts of the
    triangle: it takes the first state in it. A two-character code ending in ``C`` is read as a whole triangle, so a
    subdivided painting that short is read as whole."""
    s = str(code).strip().upper()
    if not s:
        return 0, False
    if len(s) == 1:
        return (0 if s == "4" else 1 if s == "8" else 0), False
    if len(s) == 2 and s[1] == "C":
        n = _nibble(s[0])
        return (n + 2 if n >= 0 else 0), False
    for i, ch in enumerate(s):
        if i + 1 < len(s) and s[i + 1] == "C" and _nibble(ch) >= 0:
            return _nibble(ch) + 2, True
        if ch in "48":
            return (0 if ch == "4" else 1), True
    return 0, True


def _model(z: zipfile.ZipFile, member: str) -> dict[str, Any]:
    """Objects, build items, colour groups and metadata of one model file, read in streaming."""
    objects: dict[str, dict[str, Any]] = {}
    items: list[tuple[str, Optional[str]]] = []
    colors: list[str] = []
    meta: dict[str, str] = {}
    cur: Optional[dict[str, Any]] = None
    parents: list[ET.Element] = []
    seen = 0
    with z.open(member) as fh:
        for event, el in ET.iterparse(fh, events=("start", "end")):
            if event == "start":
                parents.append(el)
                if el.tag == CORE + "object":
                    cur = {"P": array("d"), "F": array("i"), "codes": [], "pids": [], "components": [], "kinds": set()}
                    objects[el.get("id") or ""] = cur
                continue
            parents.pop()
            tag = el.tag
            if tag == CORE + "vertex" and cur is not None:
                cur["P"].extend((float(el.get("x")), float(el.get("y")), float(el.get("z"))))
            elif tag == CORE + "triangle" and cur is not None:
                cur["F"].extend((int(el.get("v1")), int(el.get("v2")), int(el.get("v3"))))
                paint, mmu = el.get("paint_color"), el.get(SLIC3R + "mmu_segmentation")
                if paint is not None:
                    cur["codes"].append(paint)
                    cur["kinds"].add("paint")
                elif mmu is not None:
                    cur["codes"].append(mmu)
                    cur["kinds"].add("mmu")
                else:
                    cur["codes"].append(None)
                cur["pids"].append(el.get("p1"))
            elif tag == CORE + "component" and cur is not None:
                cur["components"].append((el.get(PROD + "path"), el.get("objectid"), el.get("transform")))
            elif tag == CORE + "item":
                items.append((el.get("objectid") or "", el.get("transform")))
            elif tag == MAT + "color":
                colors.append(el.get("color") or "")
            elif tag == CORE + "metadata":
                meta[el.get("name") or ""] = el.text or ""
            elif tag == CORE + "object":
                cur = None
            el.clear()
            seen += 1
            if seen % 4096 == 0 and parents and parents[-1].tag in (CORE + "vertices", CORE + "triangles", CORE + "components"):
                parents[-1].clear()          # drop the children already read: the tree never grows with the figure
    return {"objects": objects, "items": items, "colors": colors, "meta": meta}


def _object_mesh(z: zipfile.ZipFile, models: dict[str, dict[str, Any]], member: str, oid: str, M: Optional[np.ndarray],
                 depth: int = 0) -> tuple[np.ndarray, np.ndarray, list, list, set]:
    """Points, triangles, paint codes and colour indices of one object, with its components' transforms applied."""
    if depth > 8:
        raise ValueError("the 3MF nests components too deep")
    if member not in models:
        models[member] = _model(z, member)
    obj = models[member]["objects"].get(str(oid))
    if obj is None:
        raise ValueError(f"the 3MF points at object {oid} in {member} and it is not there")
    P = np.frombuffer(obj["P"], np.float64).reshape(-1, 3).copy()
    F = np.frombuffer(obj["F"], np.int32).reshape(-1, 3).copy()
    codes, pids = list(obj["codes"]), list(obj["pids"])
    kinds = set(obj["kinds"])
    for path, child, transform in obj["components"]:
        sub = _object_mesh(z, models, _member(path), child, _matrix(transform), depth + 1)
        base = len(P)
        P = np.vstack([P, sub[0]])
        F = np.vstack([F, sub[1] + base]) if len(sub[1]) else F
        codes += sub[2]
        pids += sub[3]
        kinds |= sub[4]
    return _placed(P, M), F, codes, pids, kinds


def _main_model(z: zipfile.ZipFile, names: set[str]) -> str:
    """The model file the package points at, for a 3MF that does not keep it at ``3D/3dmodel.model``."""
    if "_rels/.rels" in names:
        for rel in ET.fromstring(z.read("_rels/.rels")):
            if (rel.get("Type") or "").endswith("/3dmodel"):
                return _member(rel.get("Target"))
    raise ValueError("the 3MF holds no 3D model")


def _dialect(top: dict[str, Any], kinds: set, pids: list) -> str:
    """Which dialect the file is written in."""
    meta = top["meta"]
    if meta.get("Application") == "Nikias's Hoard" and "nikias:PaintedSlots" in meta:
        return "orca"
    if "paint" in kinds:
        return "bambu"
    if "mmu" in kinds:
        return "prusa"
    if any(p is not None for p in pids):
        return "color"
    # Nothing painted: use the marks the writer left.
    if any(name.startswith("BambuStudio") for name in meta):
        return "bambu"
    if any(name.startswith("slic3rpe:") for name in meta):
        return "prusa"
    return "color"


def _palette(z: zipfile.ZipFile, names: set[str], top: dict[str, Any], dialect: str) -> list[str]:
    """The colours the file carries, as hex in palette order; empty when it carries none."""
    if dialect == "color" and top["colors"]:
        return ["#" + c.lstrip("#")[:6].lower() for c in top["colors"] if len(c.lstrip("#")) >= 6]
    if SETTINGS in names:
        try:
            settings = json.loads(z.read(SETTINGS))
        except ValueError:
            settings = {}
        colours = settings.get("filament_colour") if isinstance(settings, dict) else None
        if colours:
            return ["#" + str(c).lstrip("#")[:6].lower() for c in colours]
    return ["#" + h.lower() for h in HEX.findall(top["meta"].get("Description", ""))]


def read_3mf(path: Path) -> dict[str, Any]:
    """The mesh of a painted 3MF, the palette index of each triangle and the palette the file carries.

    ``P`` (n, 3) float64 in millimetres as the file places it, ``F`` (m, 3) int32, ``labels`` (m,) uint8 indexing
    ``palette``, ``palette`` as hex (empty when the file carries no colours), ``dialect`` and ``split_triangles``,
    how many triangles carried a subdivided painting."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        main = MAIN if MAIN in names else _main_model(z, names)
        models: dict[str, dict[str, Any]] = {}
        top = models[main] = _model(z, main)
        items = top["items"] or [(oid, None) for oid, obj in top["objects"].items() if obj["F"]]
        P, F, codes, pids, base, kinds = [], [], [], [], 0, set()
        for oid, transform in items:
            part = _object_mesh(z, models, main, oid, _matrix(transform))
            P.append(part[0])
            F.append(part[1] + base if len(part[1]) else part[1])
            codes += part[2]
            pids += part[3]
            kinds |= part[4]
            base += len(part[0])
        P = np.vstack(P) if P else np.zeros((0, 3), np.float64)
        F = np.vstack(F) if F else np.zeros((0, 3), np.int32)
        labels = np.zeros(len(F), np.uint8)
        split = 0
        for i, (code, pid) in enumerate(zip(codes, pids)):
            if pid is not None:
                labels[i] = int(pid)
            elif code:
                labels[i], was_split = _code_label(code)
                split += was_split
        dialect = _dialect(top, kinds, pids)
        palette = _palette(z, names, top, dialect)
    return {"P": P.astype(np.float64), "F": F.astype(np.int32), "labels": labels, "palette": palette,
            "dialect": dialect, "split_triangles": split}
