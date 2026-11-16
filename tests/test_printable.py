"""Printability: finding what stops a figure from being a solid, repairing it, one closed solid per colour, and the 3MF files made of them."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest

from nikias_hoard.engine import export, export_parts, printable, solids

from . import shapes

PALETTE = [{"hex": "#c81e23", "name": "rojo"}, {"hex": "#f5d728", "name": "amarillo"}, {"hex": "#235ac8", "name": "azul"}]


@pytest.fixture(scope="module")
def figure():
    return shapes.figure(90, 56)


def volume(P, F):
    return float(printable.inspect(P, F)["volume"])


def test_a_closed_figure_is_clean_and_one_piece(figure):
    r = printable.inspect(figure.P, figure.F)
    assert r["watertight"] and not r["needs_repair"] and r["open_edges"] == 0 and r["flipped_edges"] == 0 and r["volume"] > 0
    assert len(r["pieces"]) == 1 and r["pieces"][0]["main"] and r["pieces"][0]["on_plate"] and r["floating_pieces"] == []


def test_repair_closes_holes_orients_faces_and_drops_what_is_not_a_face(figure):
    F = figure.F.copy()
    labels = shapes.ball_of(figure).astype(np.int64)
    F[::9] = F[::9][:, [0, 2, 1]]                                   # faces turned the wrong way
    keep = np.ones(len(F), bool)
    keep[500:508] = False                                           # a hole
    F, labels = F[keep], labels[keep]
    P = np.vstack([figure.P, figure.P[:1]])
    F = np.vstack([F, F[:3], [[0, 0, 5]], [[len(figure.P), len(figure.P), 1]]])        # repeated faces and faces without area
    labels = np.concatenate([labels, labels[:3], [0, 0]])
    bad = printable.inspect(P, F)
    assert bad["open_edges"] > 0 and bad["flipped_edges"] > 0 and bad["duplicate_faces"] == 3 and bad["degenerate_faces"] == 2 and bad["needs_repair"]
    P2, F2, L2, done = printable.repair(P, F, labels)
    good = printable.inspect(P2, F2)
    assert good["watertight"] and good["volume"] > 0 and not good["needs_repair"]
    assert abs(good["volume"] - volume(figure.P, figure.F)) < 0.01 * volume(figure.P, figure.F)
    assert done["duplicates_removed"] == 3 and done["degenerate_removed"] == 2 and done["holes_closed"] >= 1 and done["faces_reversed"] > 0
    assert len(L2) == len(F2) and set(np.unique(L2)) <= {0, 1, 2}


def test_an_inside_out_figure_is_turned_but_a_cavity_stays(figure):
    inverted = figure.F[:, [0, 2, 1]]
    assert printable.inspect(figure.P, inverted)["inside_out_pieces"] == 1
    P, F, _, done = printable.repair(figure.P, inverted)
    assert done["pieces_turned"] == 1 and volume(P, F) > 0
    small = figure.P * 0.3 + [0, 0, 6]                              # a hollow inside: its own shell, facing inwards
    P = np.vstack([figure.P, small])
    F = np.vstack([figure.F, figure.F[:, [0, 2, 1]] + len(figure.P)])
    P2, F2, _, done = printable.repair(P, F)
    assert done["pieces_turned"] == 0 and volume(P2, F2) < volume(figure.P, figure.F)


def test_loose_pieces_dust_and_floating_parts(figure):
    ball = shapes.figure(20, 12)
    P = np.vstack([figure.P, ball.P * 0.4 + [40, 0, 0], ball.P * 0.4 + [-40, 0, 25]])
    F = np.vstack([figure.F, ball.F + len(figure.P), ball.F + len(figure.P) + len(ball.P)])
    r = printable.inspect(P, F)
    assert len(r["pieces"]) == 3 and r["pieces"][0]["main"]
    assert len(r["floating_pieces"]) == 1 and sum(p["on_plate"] for p in r["pieces"]) == 2
    dust = np.array([[0, 0, 0], [1e-3, 0, 0], [0, 1e-3, 0.0]]) + [0, -40, 0]
    P3 = np.vstack([figure.P, dust])
    F3 = np.vstack([figure.F, [[len(figure.P), len(figure.P) + 1, len(figure.P) + 2]]])
    assert len(printable.inspect(P3, F3)["specks"]) == 1
    assert printable.repair(P3, F3)[3]["specks_removed"] == 1


def test_the_solids_fill_the_figure_and_each_is_closed(figure):
    labels = shapes.ball_of(figure)
    parts, info = solids.split(figure.P, figure.F, labels, 0.03)
    assert sorted(parts) == [0, 1, 2] and info["core"] == 0 and info["depth"] > 0
    total = 0.0
    for k, (P, F) in parts.items():
        r = printable.inspect(P, F)
        assert r["watertight"] and r["volume"] > 0, k
        total += r["volume"]
    assert abs(total - volume(figure.P, figure.F)) < 1e-6 * total
    deeper, _ = solids.split(figure.P, figure.F, labels, 0.06)
    assert volume(*deeper[1]) > volume(*parts[1])


def test_a_single_colour_is_one_solid(figure):
    parts, info = solids.split(figure.P, figure.F, np.zeros(len(figure.F), np.int64))
    assert list(parts) == [0] and len(parts[0][1]) == len(figure.F)


def test_part_files_hold_every_solid_with_its_filament(tmp_path, figure):
    labels = shapes.ball_of(figure)
    parts, _ = solids.split(figure.P, figure.F, labels, 0.03)
    listed = [(k, P, F) for k, (P, F) in sorted(parts.items())]
    triangles = sum(len(F) for _, _, F in listed)
    ns = {"m": export.CORE_NS}
    std = export_parts.write_parts_3mf(tmp_path / "std.3mf", listed, PALETTE, "Tres bolas", "parts")
    with zipfile.ZipFile(std) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    assert [b.get("displaycolor") for b in root.findall(".//m:base", ns)] == ["#C81E23FF", "#F5D728FF", "#235AC8FF"]
    objects = root.findall("m:resources/m:object", ns)
    assert len(objects) == 4 and [o.get("pindex") for o in objects[:3]] == ["0", "1", "2"] and len(objects[3].findall(".//m:component", ns)) == 3
    assert sum(len(o.findall(".//m:triangle", ns)) for o in objects) == triangles
    assert root.find("m:build/m:item", ns).get("objectid") == "5" and min(float(v.get("z")) for v in root.findall(".//m:vertex", ns)) > -1e-6
    bam = export_parts.write_parts_3mf(tmp_path / "bam.3mf", listed, PALETTE, "Tres bolas", "parts-bambu")
    with zipfile.ZipFile(bam) as z:
        main = ET.fromstring(z.read("3D/3dmodel.model"))
        meshes = ET.fromstring(z.read("3D/Objects/object_1.model"))
        settings = ET.fromstring(z.read("Metadata/model_settings.config"))
    comps = main.findall(".//m:component", ns)
    assert [c.get("objectid") for c in comps] == ["1", "2", "3"] and all(c.get("{%s}path" % export.PRODUCTION_NS) == "/3D/Objects/object_1.model" for c in comps)
    assert len(meshes.findall(".//m:object", ns)) == 3 and sum(len(m.findall(".//m:triangle", ns)) for m in meshes.findall(".//m:mesh", ns)) == triangles
    parts_xml = settings.findall(".//part")
    assert [p.get("id") for p in parts_xml] == ["1", "2", "3"]
    assert [p.find("metadata[@key='extruder']").get("value") for p in parts_xml] == ["1", "2", "3"]
    pru = export_parts.write_parts_3mf(tmp_path / "pru.3mf", listed, PALETTE, "Tres bolas", "parts-prusa")
    with zipfile.ZipFile(pru) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
        config = ET.fromstring(z.read("Metadata/Slic3r_PE_model.config"))
    tris = root.findall(".//m:triangle", ns)
    vols = config.findall(".//volume")
    assert len(tris) == triangles and [v.find("metadata[@key='extruder']").get("value") for v in vols] == ["1", "2", "3"]
    assert vols[0].get("firstid") == "0" and vols[-1].get("lastid") == str(triangles - 1)
    assert max(int(t.get("v3")) for t in tris) < len(root.findall(".//m:vertex", ns))
    with pytest.raises(ValueError):
        export_parts.write_parts_3mf(tmp_path / "x.3mf", listed, PALETTE, "x", "nope")
