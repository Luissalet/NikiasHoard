"""3MF files with one closed solid per colour.

- ``parts``: plain 3MF. A mesh object per colour with its colour as base material, and one assembly object holding them,
  which is what the build plate lists. Readers that follow the core specification see every colour as its own body.
- ``parts-bambu``: the layout Bambu Studio and OrcaSlicer save for an object made of parts: the assembly in
  ``3D/3dmodel.model`` with a component per colour, the meshes in ``3D/Objects/object_1.model`` and, in
  ``Metadata/model_settings.config``, each part with the filament (extruder) of its colour: the palette index + 1.
- ``parts-prusa``: one PrusaSlicer object whose volumes are the solids, each with the extruder of its colour.

The solids come from ``solids.split``; they fill the figure exactly and do not overlap."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import numpy as np

from .export import (BAMBU_APP, BAMBU_NS, CONTENT_TYPES, CORE_NS, PLATE_CENTRE, PRODUCTION_NS, RELS, SLIC3R_NS, _meta, _triangles, _uuid,
                     _vertices)

PART_DIALECTS = ("parts", "parts-bambu", "parts-prusa")
Solid = tuple[int, np.ndarray, np.ndarray]


def _label(c: dict[str, Any], k: int) -> str:
    return str(c.get("name") or f"color {k + 1}")


def _mesh(P: np.ndarray, F: np.ndarray, offset: int = 0) -> str:
    return "<mesh>\n<vertices>\n" + _vertices(P) + "</vertices>\n<triangles>\n" + _triangles(F + offset, [""] * len(F)) + "</triangles>\n</mesh>\n"


def _standard(parts: list[Solid], palette: list[dict[str, Any]], title: str, notes: str) -> dict[str, str]:
    used = sorted({k for k, _, _ in parts})
    slot = {k: i for i, k in enumerate(used)}
    res = '<basematerials id="1">\n' + "".join(
        f'<base name={quoteattr(_label(palette[k], k))} displaycolor="{palette[k]["hex"].upper()}FF"/>\n' for k in used) + "</basematerials>\n"
    objs, refs = "", ""
    for n, (k, P, F) in enumerate(parts):
        oid = n + 2
        objs += f'<object id="{oid}" name={quoteattr(title + " " + _label(palette[k], k))} type="model" pid="1" pindex="{slot[k]}">\n{_mesh(P, F)}</object>\n'
        refs += f'<component objectid="{oid}"/>\n'
    top = len(parts) + 2
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n' + f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}">\n'
           + _meta("Title", title) + _meta("Description", "Colours: " + notes) + _meta("Application", "Nikias's Hoard")
           + "<resources>\n" + res + objs + f'<object id="{top}" name={quoteattr(title)} type="model">\n<components>\n{refs}</components>\n</object>\n</resources>\n'
           + f'<build>\n<item objectid="{top}" printable="1"/>\n</build>\n</model>\n')
    return {"3D/3dmodel.model": xml}


def _prusa(parts: list[Solid], palette: list[dict[str, Any]], title: str, notes: str) -> dict[str, str]:
    offset, first = 0, 0
    verts, tris, volumes = "", "", ""
    for k, P, F in parts:
        verts += _vertices(P)
        tris += _triangles(F + offset, [""] * len(F))
        volumes += (f'  <volume firstid="{first}" lastid="{first + len(F) - 1}">\n   <metadata type="volume" key="name" value={quoteattr(_label(palette[k], k))}/>\n'
                    '   <metadata type="volume" key="volume_type" value="ModelPart"/>\n'
                    f'   <metadata type="volume" key="extruder" value="{k + 1}"/>\n  </volume>\n')
        offset += len(P)
        first += len(F)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n' + f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:slic3rpe="{SLIC3R_NS}">\n'
           + _meta("slic3rpe:Version3mf", "1") + _meta("Title", title) + _meta("Description", "Filaments: " + notes) + _meta("Application", "Nikias's Hoard")
           + f'<resources>\n<object id="1" name={quoteattr(title)} type="model">\n<mesh>\n<vertices>\n{verts}</vertices>\n<triangles>\n{tris}</triangles>\n</mesh>\n</object>\n'
           + '</resources>\n<build>\n<item objectid="1" printable="1"/>\n</build>\n</model>\n')
    config = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n <object id="1" instances_count="1">\n'
              f'  <metadata type="object" key="name" value={quoteattr(title)}/>\n  <metadata type="object" key="extruder" value="1"/>\n'
              + volumes + " </object>\n</config>\n")
    return {"3D/3dmodel.model": xml, "Metadata/Slic3r_PE_model.config": config}


def _bambu(parts: list[Solid], palette: list[dict[str, Any]], title: str, notes: str, height: float) -> dict[str, str]:
    half = 0.5 * height
    top = len(parts) + 1
    place = f"1 0 0 0 1 0 0 0 1 {PLATE_CENTRE[0]:g} {PLATE_CENTRE[1]:g} {half:.6g}"
    head = (f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:BambuStudio="{BAMBU_NS}" xmlns:p="{PRODUCTION_NS}" '
            'requiredextensions="p">\n')
    comps, objs, setting = "", "", ""
    for n, (k, P, F) in enumerate(parts):
        oid = n + 1
        objs += f'<object id="{oid}" p:UUID="{_uuid(oid, 1)}" type="model">\n{_mesh(P - np.array([0.0, 0.0, half]), F)}</object>\n'
        comps += f'<component p:path="/3D/Objects/object_1.model" objectid="{oid}" p:UUID="{_uuid(oid, 3)}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
        setting += (f'    <part id="{oid}" subtype="normal_part">\n      <metadata key="name" value={quoteattr(_label(palette[k], k))}/>\n'
                    '      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                    f'      <metadata key="extruder" value="{k + 1}"/>\n'
                    '      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n    </part>\n')
    main = ('<?xml version="1.0" encoding="UTF-8"?>\n' + head + _meta("Application", BAMBU_APP) + _meta("BambuStudio:3mfVersion", "1")
            + _meta("Title", title) + _meta("Description", "Filaments: " + notes)
            + f'<resources>\n<object id="{top}" p:UUID="{_uuid(1, 0)}" type="model">\n<components>\n{comps}</components>\n</object>\n</resources>\n'
            + f'<build p:UUID="{_uuid(1, 2)}">\n<item objectid="{top}" p:UUID="{_uuid(2, 0)}" transform="{place}" printable="1"/>\n</build>\n</model>\n')
    part = ('<?xml version="1.0" encoding="UTF-8"?>\n' + head + _meta("BambuStudio:3mfVersion", "1") + "<resources>\n" + objs
            + "</resources>\n<build/>\n</model>\n")
    settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
                f'  <object id="{top}">\n    <metadata key="name" value={quoteattr(title)}/>\n    <metadata key="extruder" value="1"/>\n' + setting
                + '  </object>\n  <plate>\n    <metadata key="plater_id" value="1"/>\n    <metadata key="plater_name" value=""/>\n'
                '    <metadata key="locked" value="false"/>\n    <model_instance>\n'
                f'      <metadata key="object_id" value="{top}"/>\n      <metadata key="instance_id" value="0"/>\n    </model_instance>\n  </plate>\n  <assemble>\n'
                f'   <assemble_item object_id="{top}" instance_id="0" transform="{place}" offset="0 0 0" />\n  </assemble>\n</config>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            ' <Relationship Target="/3D/Objects/object_1.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
            "</Relationships>\n")
    return {"3D/3dmodel.model": main, "3D/Objects/object_1.model": part, "3D/_rels/3dmodel.model.rels": rels, "Metadata/model_settings.config": settings}


def write_parts_3mf(path: Path, solids: list[Solid], palette: list[dict[str, Any]], name: str, dialect: str = "parts") -> Path:
    """``solids``: ``(palette index, P, F)`` per colour in the figure's own coordinates. They are moved together, centred and
    standing on z = 0."""
    if dialect not in PART_DIALECTS:
        raise ValueError(f"unknown 3MF dialect: {dialect}")
    if not solids:
        raise ValueError("no solids to write")
    title = name or "figure"
    lo = np.min([P.min(0) for _, P, _ in solids], axis=0)
    hi = np.max([P.max(0) for _, P, _ in solids], axis=0)
    move = np.array([0.5 * (lo[0] + hi[0]), 0.5 * (lo[1] + hi[1]), lo[2]])
    parts = [(k, P - move, F) for k, P, F in solids]
    notes = "; ".join(f"{k + 1} = {_label(c, k)} {c.get('hex', '')}".strip() for k, c in enumerate(palette))
    if dialect == "parts":
        files = _standard(parts, palette, title, notes)
    elif dialect == "parts-prusa":
        files = _prusa(parts, palette, title, notes)
    else:
        files = _bambu(parts, palette, title, notes, float(hi[2] - lo[2]))
    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        for member, text in files.items():
            z.writestr(member, text)
    return path
