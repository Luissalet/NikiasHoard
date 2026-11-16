"""Files a slicer opens with the colours already in place.

- ``painted`` 3MF: the mesh once, each triangle tagged with the filament it is painted with, the way Bambu Studio and
  OrcaSlicer (``paint_color``) or PrusaSlicer (``slic3rpe:mmu_segmentation``) store their own painting. Filament 1 is
  the object's own and carries no tag. The Bambu/Orca file is laid out as those programs save a project (the object as
  a component with its mesh in ``3D/Objects``, and the plate): with the mesh written straight into the main model file
  OrcaSlicer opens the figure and drops the painting.
- ``color`` 3MF: the 3MF materials extension (a colour group, one colour per triangle): any program that follows the
  standard shows the colours, and slicers that import standard colour map them to filaments.
- One closed solid per colour, as parts of one object (``export_parts.py``).
- OBJ + MTL: one material per colour.
- STL per colour: the faces of each colour as their own surface (for painting guides and masks; they are not closed
  solids).
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import numpy as np

from . import mesh as meshes

CONTENT_TYPES = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                 '<Default Extension="config" ContentType="text/xml"/></Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MATERIAL_NS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
SLIC3R_NS = "http://schemas.slic3r.org/3mf/2017/06"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"

DIALECTS = ("bambu", "prusa", "color")


def paint_code(filament: int) -> str:
    """The tag of a whole triangle painted with ``filament`` (1-based) in the slicers' painting format; "" for the
    object's own filament 1."""
    if filament <= 1:
        return ""
    if filament == 2:
        return "8"
    if filament > 16:
        raise ValueError("the painting format holds 16 filaments")
    return f"{filament - 3:X}C"


def _vertices(P: np.ndarray) -> str:
    return "".join('<vertex x="%.6g" y="%.6g" z="%.6g"/>\n' % tuple(t) for t in P.astype(np.float32).tolist())


def _triangles(F: np.ndarray, extra: list[str]) -> str:
    rows = F.tolist()
    return "".join('<triangle v1="%d" v2="%d" v3="%d"%s/>\n' % (r[0], r[1], r[2], e) for r, e in zip(rows, extra))


def _meta(name: str, value: str) -> str:
    return f"<metadata name={quoteattr(name)}>{escape(value)}</metadata>\n"


def model_xml(P: np.ndarray, F: np.ndarray, labels: np.ndarray, palette: list[dict[str, Any]], name: str, dialect: str) -> str:
    """The ``3D/3dmodel.model`` of a figure for PrusaSlicer or as standard colour. ``labels`` index ``palette`` per face;
    palette entry 0 is filament 1."""
    if dialect not in ("prusa", "color"):
        raise ValueError(f"unknown 3MF dialect: {dialect}")
    lab = np.asarray(labels, np.int64)
    title = name or "figure"
    notes = "; ".join(f"{k + 1} = {c.get('name', '')} {c.get('hex', '')}".strip() for k, c in enumerate(palette))
    if dialect == "color":
        head = f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:m="{MATERIAL_NS}">\n'
        meta = _meta("Title", title) + _meta("Description", "Colours: " + notes) + _meta("Application", "Nikias's Hoard")
        res = '<m:colorgroup id="1">\n' + "".join(f'<m:color color="{c["hex"].upper()}FF"/>\n' for c in palette) + "</m:colorgroup>\n"
        obj = f'<object id="2" name={quoteattr(title)} type="model" pid="1" pindex="0">\n'
        extra = [' pid="1" p1="%d"' % k for k in lab.tolist()]
        item = '<item objectid="2"/>'
    else:
        codes = [f' slic3rpe:mmu_segmentation="{paint_code(k + 1)}"' if k else "" for k in range(len(palette))]
        extra = [codes[k] for k in lab.tolist()]
        head = f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:slic3rpe="{SLIC3R_NS}">\n'
        meta = (_meta("slic3rpe:Version3mf", "1") + _meta("slic3rpe:MmPaintingVersion", "1") + _meta("Title", title)
                + _meta("Description", "Filaments: " + notes) + _meta("Application", "Nikias's Hoard"))
        res = ""
        obj = f'<object id="1" name={quoteattr(title)} type="model">\n'
        item = '<item objectid="1" printable="1"/>'
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + head + meta + "<resources>\n" + res + obj + "<mesh>\n<vertices>\n" + _vertices(P)
            + "</vertices>\n<triangles>\n" + _triangles(F, extra) + "</triangles>\n</mesh>\n</object>\n</resources>\n<build>\n" + item
            + "\n</build>\n</model>\n")


PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
BAMBU_APP = "BambuStudio-2.3.1"       # the generator tag Bambu Studio and OrcaSlicer look for to treat the file as a project
PLATE_CENTRE = (128.0, 128.0)         # where the figure is put: inside the plate of any printer of 256 mm or more


def _uuid(n: int, kind: int) -> str:
    return f"{kind:04x}{n:04x}-61cb-4c03-9d28-80fed5dfa1dc"


def bambu_parts(P: np.ndarray, F: np.ndarray, labels: np.ndarray, palette: list[dict[str, Any]], name: str) -> dict[str, str]:
    """The files of a Bambu Studio / OrcaSlicer project: the object as a component whose mesh lives in its own model file
    (with the painting on its triangles), the object and plate description, and the filaments of the project."""
    title = name or "figure"
    half = 0.5 * float(P[:, 2].max() - P[:, 2].min())
    local = P - np.array([0.0, 0.0, half])                 # the mesh centred on its own origin, as the slicers keep it
    codes = [f' paint_color="{paint_code(k + 1)}"' if k else "" for k in range(len(palette))]
    extra = [codes[k] for k in np.asarray(labels, np.int64).tolist()]
    head = (f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:BambuStudio="{BAMBU_NS}" xmlns:p="{PRODUCTION_NS}" '
            'requiredextensions="p">\n')
    notes = "; ".join(f"{k + 1} = {c.get('name', '')} {c.get('hex', '')}".strip() for k, c in enumerate(palette))
    place = f"1 0 0 0 1 0 0 0 1 {PLATE_CENTRE[0]:g} {PLATE_CENTRE[1]:g} {half:.6g}"
    main = ('<?xml version="1.0" encoding="UTF-8"?>\n' + head + _meta("Application", BAMBU_APP) + _meta("BambuStudio:3mfVersion", "1")
            + _meta("Title", title) + _meta("Description", "Filaments: " + notes)
            + f'<resources>\n<object id="2" p:UUID="{_uuid(1, 0)}" type="model">\n<components>\n'
            f'<component p:path="/3D/Objects/object_1.model" objectid="1" p:UUID="{_uuid(0, 1)}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
            f'</components>\n</object>\n</resources>\n<build p:UUID="{_uuid(1, 2)}">\n'
            f'<item objectid="2" p:UUID="{_uuid(2, 0)}" transform="{place}" printable="1"/>\n</build>\n</model>\n')
    part = ('<?xml version="1.0" encoding="UTF-8"?>\n' + head + _meta("BambuStudio:3mfVersion", "1")
            + f'<resources>\n<object id="1" p:UUID="{_uuid(0, 1)}" type="model">\n<mesh>\n<vertices>\n' + _vertices(local)
            + "</vertices>\n<triangles>\n" + _triangles(F, extra) + "</triangles>\n</mesh>\n</object>\n</resources>\n<build/>\n</model>\n")
    settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="2">\n'
                f'    <metadata key="name" value={quoteattr(title)}/>\n    <metadata key="extruder" value="1"/>\n'
                f'    <part id="1" subtype="normal_part">\n      <metadata key="name" value={quoteattr(title)}/>\n'
                '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                '      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n'
                '    </part>\n  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n    <metadata key="plater_name" value=""/>\n'
                '    <metadata key="locked" value="false"/>\n    <model_instance>\n      <metadata key="object_id" value="2"/>\n'
                '      <metadata key="instance_id" value="0"/>\n    </model_instance>\n  </plate>\n  <assemble>\n'
                f'   <assemble_item object_id="2" instance_id="0" transform="{place}" offset="0 0 0" />\n  </assemble>\n</config>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            ' <Relationship Target="/3D/Objects/object_1.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            '</Relationships>\n')
    return {"3D/3dmodel.model": main, "3D/Objects/object_1.model": part, "3D/_rels/3dmodel.model.rels": rels,
            "Metadata/model_settings.config": settings}


def bambu_project_settings(palette: list[dict[str, Any]]) -> str:
    """The filaments of the project and nothing about the printer: as many as the palette, with its colours."""
    import json
    n = len(palette)
    return json.dumps({"version": BAMBU_APP.split("-", 1)[1], "name": "project_settings", "from": "project",
                       "filament_colour": [c["hex"].upper() for c in palette], "filament_type": [str(c.get("material") or "PLA") for c in palette],
                       "filament_settings_id": ["Generic PLA"] * n, "filament_ids": ["GFL99"] * n,
                       "filament_multi_colour": [c["hex"].upper() for c in palette]}, indent=4)


def _prusa_config(name: str, faces: int) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n <object id="1" instances_count="1">\n'
            f'  <metadata type="object" key="name" value={quoteattr(name)}/>\n  <metadata type="object" key="extruder" value="1"/>\n'
            f'  <volume firstid="0" lastid="{max(faces - 1, 0)}">\n   <metadata type="volume" key="name" value={quoteattr(name)}/>\n'
            '   <metadata type="volume" key="volume_type" value="ModelPart"/>\n   <metadata type="volume" key="extruder" value="1"/>\n'
            '  </volume>\n </object>\n</config>\n')


def write_3mf(path: Path, P: np.ndarray, F: np.ndarray, labels: np.ndarray, palette: list[dict[str, Any]], name: str, dialect: str,
              project: bool = False) -> Path:
    """``project`` (Bambu/Orca only) also writes the project's filaments. Off by default: OrcaSlicer 2.3.1 takes a
    settings file that only lists filaments as unknown presets, warns and loads nothing; without it the figure opens
    painted and the filament colours are chosen in the slicer."""
    path = Path(path)
    base = P.min(0)
    shifted = P - np.array([0.5 * (P[:, 0].min() + P[:, 0].max()), 0.5 * (P[:, 1].min() + P[:, 1].max()), base[2]])   # centred, standing on z = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        if dialect == "bambu":
            for member, text in bambu_parts(shifted, F, labels, palette, name).items():
                z.writestr(member, text)
            if project:
                z.writestr("Metadata/project_settings.config", bambu_project_settings(palette))
            return path
        z.writestr("3D/3dmodel.model", model_xml(shifted, F, labels, palette, name, dialect))
        if dialect == "prusa":
            z.writestr("Metadata/Slic3r_PE_model.config", _prusa_config(name, len(F)))
    return path


def write_obj(path: Path, P: np.ndarray, F: np.ndarray, labels: np.ndarray, palette: list[dict[str, Any]], name: str) -> list[Path]:
    path = Path(path)
    mtl = path.with_suffix(".mtl")
    with open(mtl, "w", encoding="utf-8", newline="\n") as fh:
        for k, c in enumerate(palette):
            r, g, b = (int(c["hex"].lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
            fh.write(f"newmtl color_{k + 1}\nKd {r:.4f} {g:.4f} {b:.4f}\nKa 0 0 0\nKs 0 0 0\nd 1\nillum 1\n\n")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# {name}\nmtllib {mtl.name}\no {name or 'figure'}\n")
        fh.write("".join("v %.6g %.6g %.6g\n" % tuple(t) for t in P.astype(np.float32).tolist()))
        lab = np.asarray(labels, np.int64)
        for k in range(len(palette)):
            sel = F[lab == k] + 1
            if len(sel):
                fh.write(f"usemtl color_{k + 1}\n")
                fh.write("".join("f %d %d %d\n" % tuple(r) for r in sel.tolist()))
    return [path, mtl]


def write_color_stls(folder: Path, stem: str, P: np.ndarray, F: np.ndarray, labels: np.ndarray, palette: list[dict[str, Any]]) -> list[Path]:
    out = []
    lab = np.asarray(labels, np.int64)
    for k, c in enumerate(palette):
        sel = F[lab == k]
        if not len(sel):
            continue
        safe = "".join(ch if ch.isalnum() else "-" for ch in str(c.get("name") or f"color{k + 1}")).strip("-") or f"color{k + 1}"
        p = Path(folder) / f"{stem} {k + 1} {safe}.stl"
        meshes.write_stl(p, P, sel)
        out.append(p)
    return out
