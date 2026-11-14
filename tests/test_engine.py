"""The engine on a figure built by code: three balls stacked into one closed surface, whose necks are the folds."""

from __future__ import annotations

import zipfile

import numpy as np
import pytest

from nikias_hoard.engine import export, labels as lab, mesh as meshes, palette as pal, regions as reg, render, source

from . import shapes


@pytest.fixture(scope="module")
def figure():
    return shapes.figure(200, 120)


def test_stl_round_trip_welds_and_finds_neighbours(tmp_path, figure):
    meshes.write_stl(tmp_path / "f.stl", figure.P, figure.F)
    back = meshes.load_stl(tmp_path / "f.stl")
    assert len(back.F) == len(figure.F) and len(back.P) == len(figure.P)
    assert back.stats()["open_edges"] == 0
    assert (back.neighbours >= 0).all()
    assert abs(back.areas.sum() - figure.areas.sum()) < 1e-3 * figure.areas.sum()


def test_ascii_stl(tmp_path):
    text = "solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid t\n"
    (tmp_path / "a.stl").write_text(text)
    assert len(meshes.load_stl(tmp_path / "a.stl").F) == 1


def test_regions_follow_the_folds(figure):
    for detail in (1, 3, 5):
        regions = reg.segment(figure, detail)
        assert regions.max() + 1 == 3
        truth = shapes.ball_of(figure)
        agree = sum(np.bincount(truth[regions == k], figure.areas[regions == k], 3).max() for k in range(3))
        assert agree / figure.areas.sum() > 0.96
    rows = reg.describe(figure, reg.segment(figure, 3))
    assert [r["id"] for r in rows] == [0, 1, 2] and rows[0]["share"] > rows[1]["share"] > rows[2]["share"]
    assert rows[0]["height"] < rows[1]["height"] < rows[2]["height"]


def test_merge_small_respects_the_region_cap(figure):
    stripes = (figure.centroids[:, 2] // 2).astype(np.int64)             # 25 bands
    merged = reg.merge_small(stripes, figure, 0.0, max_regions=6)
    assert merged.max() + 1 <= 6


def test_model_is_laid_over_the_figure_whatever_its_units_and_turn(figure):
    th = np.radians(37)
    rz = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
    y_up = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    scene = shapes.textured(turn=y_up @ rz, scale=0.013, shift=(3, -2, 8))
    pts, rgb = source.sample_scene(scene, 150_000)
    fit = source.register(pts, figure)
    assert fit["fit"] < 0.008
    face_rgb, gap = source.transfer(figure, source.apply(fit["matrix"], pts), rgb)
    colors = pal.reduce(face_rgb, figure.areas, 3)
    labels = pal.assign(face_rgb, colors)
    want = pal.assign(shapes.COLORS[shapes.ball_of(figure)], colors)
    assert (figure.areas * (labels == want)).sum() / figure.areas.sum() > 0.97
    assert float(np.median(gap)) < 0.5


def test_a_different_shape_fits_badly(figure):
    rng = np.random.default_rng(0)
    box = rng.uniform(-1, 1, (40_000, 3)) * [30, 4, 4]
    assert source.register(box, figure)["fit"] > 0.03


def test_palette_reduction_keeps_a_small_distinct_colour():
    rgb = np.array([[200, 30, 35]] * 900 + [[205, 40, 40]] * 80 + [[245, 245, 245]] * 25 + [[20, 20, 20]] * 15, np.uint8)
    colors = pal.reduce(rgb, None, 3)
    names = {pal.name_of(c) for c in colors}
    assert names == {"rojo", "blanco", "negro"}
    assert pal.name_of(colors[0]) == "rojo"                                # biggest first
    assert pal.suggest_count(rgb, np.ones(len(rgb)))["count"] == 3
    assert pal.to_hex(pal.from_hex("#Abc")) == "#aabbcc"


def test_lab_round_trip():
    rgb = np.array([[0, 0, 0], [255, 255, 255], [200, 30, 35], [35, 90, 200], [128, 128, 128]], np.uint8)
    assert np.abs(pal.lab_to_rgb(pal.rgb_to_lab(rgb)).astype(int) - rgb).max() <= 1


def test_cleanup_removes_speckle_and_small_islands(figure):
    labels = shapes.ball_of(figure).astype(np.uint8)
    rng = np.random.default_rng(1)
    noisy = labels.copy()
    flip = rng.choice(len(noisy), 400, replace=False)
    noisy[flip] = (noisy[flip] + 1) % 3
    out = lab.smooth(noisy, figure, 2)
    out, removed = lab.remove_islands(out, figure, 1.5)
    assert (out != labels).mean() < 0.002
    rows = lab.stats(out, figure, 3)
    assert [r["patches"] for r in rows] == [1, 1, 1]
    assert abs(sum(r["share"] for r in rows) - 1) < 1e-3


def test_brush_paints_only_the_side_it_touches(figure):
    labels = np.zeros(len(figure.F), np.uint8)
    spot = np.array([12.0, 0.0, 12.0])                                    # on the big ball's side
    changed = lab.brush(labels, figure, spot, 3.0, 2, normal=[1, 0, 0])
    assert changed > 10
    painted = figure.centroids[labels == 2]
    assert np.linalg.norm(painted - spot, axis=1).max() <= 3.0 + 1e-9
    assert lab.nearest_face(figure, spot) in np.flatnonzero(labels == 2)


def test_render_shows_each_face_colour_and_numbers_regions(figure):
    regions = shapes.ball_of(figure)
    frame = render.rasterize(figure.P, figure.F, yaw=0, pitch=10, size=300)
    seen = frame.face[frame.face >= 0]
    assert set(np.unique(regions[seen])) == {0, 1, 2}
    img = render.shade(frame, shapes.COLORS[regions])
    px = np.asarray(img)
    assert (px[20, 20] == render.BACKGROUND).all()
    reds = (px[..., 0] > 120) & (px[..., 2] < 80) & (px[..., 1] < 80)
    blues = (px[..., 2] > 120) & (px[..., 0] < 90)
    assert reds.sum() > 3000 and blues.sum() > 500
    assert np.argwhere(blues)[:, 0].mean() < np.argwhere(reds)[:, 0].mean()      # the head is above the body
    _, shown = render.number_regions(render.shade(frame, render.region_colors(3)[regions], flat=0.4), frame, regions)
    assert shown == [0, 1, 2]
    zoom = render.rasterize(figure.P, figure.F, yaw=0, pitch=0, size=200, region=(0, -5, 45, 3))
    assert set(np.unique(regions[zoom.face[zoom.face >= 0]])) == {2}
    assert render.sheet([img, img], ["a", "b"]).width == 2 * 300 + 6
    cloud = render.points_image(figure.centroids, shapes.COLORS[regions], size=200)
    assert (np.asarray(cloud)[100, 100] != render.BACKGROUND).any()


def test_paint_codes():
    assert [export.paint_code(n) for n in (1, 2, 3, 4, 5, 16)] == ["", "8", "0C", "1C", "2C", "DC"]
    with pytest.raises(ValueError):
        export.paint_code(17)


def test_3mf_files(tmp_path, figure):
    import json
    import xml.etree.ElementTree as ET
    labels = shapes.ball_of(figure)
    palette = [{"hex": "#c81e23", "name": "rojo"}, {"hex": "#f5d728", "name": "amarillo"}, {"hex": "#235ac8", "name": "azul & mar"}]
    faces = {k: int((labels == k).sum()) for k in range(3)}
    ns = {"m": export.CORE_NS}
    for dialect, member, attr in (("bambu", "3D/Objects/object_1.model", "paint_color"),
                                  ("prusa", "3D/3dmodel.model", "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation")):
        path = export.write_3mf(tmp_path / f"{dialect}.3mf", figure.P, figure.F, labels, palette, "Tres bolas", dialect, project=True)
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model", member} <= names
            root = ET.fromstring(z.read(member))
            main = ET.fromstring(z.read("3D/3dmodel.model"))
            project = json.loads(z.read("Metadata/project_settings.config")) if dialect == "bambu" else None
            settings = z.read("Metadata/model_settings.config").decode() if dialect == "bambu" else ""
        tris = root.findall(".//m:triangle", ns)
        assert len(tris) == len(figure.F) and len(root.findall(".//m:vertex", ns)) == len(figure.P)
        codes = [t.get(attr) for t in tris]
        assert codes.count(None) == faces[0] and codes.count("8") == faces[1] and codes.count("0C") == faces[2]
        zs = [float(v.get("z")) for v in root.findall(".//m:vertex", ns)]
        if dialect == "prusa":
            assert abs(min(zs)) < 1e-6                                       # standing on the plate
        else:                                                                # a project: mesh on its own origin, placed by the build item
            comp = main.find(".//m:component", ns)
            assert comp.get("{%s}path" % export.PRODUCTION_NS) == "/3D/Objects/object_1.model" and comp.get("objectid") == "1"
            place = [float(x) for x in main.find(".//m:item", ns).get("transform").split()]
            assert abs(min(zs) + place[11]) < 1e-3 and place[9:11] == [128.0, 128.0]
            assert main.find(".//m:item", ns).get("objectid") == "2" and '<object id="2">' in settings and '<part id="1"' in settings
            assert project["filament_colour"] == ["#C81E23", "#F5D728", "#235AC8"] and project["filament_type"] == ["PLA"] * 3
            assert "printer_settings_id" not in project
    bare = export.write_3mf(tmp_path / "bare.3mf", figure.P, figure.F, labels, palette, "Tres bolas", "bambu")
    assert "Metadata/project_settings.config" not in zipfile.ZipFile(bare).namelist()      # the default: opens painted in OrcaSlicer
    path = export.write_3mf(tmp_path / "color.3mf", figure.P, figure.F, labels, palette, "Tres bolas", "color")
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    mat = {"m": export.CORE_NS, "c": export.MATERIAL_NS}
    assert [c.get("color") for c in root.findall(".//c:color", mat)] == ["#C81E23FF", "#F5D728FF", "#235AC8FF"]
    assert [t.get("p1") for t in root.findall(".//m:triangle", mat)].count("2") == faces[2]


def test_obj_and_colour_stls(tmp_path, figure):
    labels = shapes.ball_of(figure)
    palette = [{"hex": "#c81e23", "name": "rojo"}, {"hex": "#f5d728", "name": "amarillo"}, {"hex": "#235ac8", "name": "azul"}]
    obj, mtl = export.write_obj(tmp_path / "f.obj", figure.P, figure.F, labels, palette, "f")
    text = obj.read_text()
    assert text.count("usemtl ") == 3 and text.count("\nf ") == len(figure.F) and "newmtl color_3" in mtl.read_text()
    parts = export.write_color_stls(tmp_path, "f", figure.P, figure.F, labels, palette)
    assert [p.name for p in parts] == ["f 1 rojo.stl", "f 2 amarillo.stl", "f 3 azul.stl"]
    assert sum(len(meshes.load_stl(p).F) for p in parts) == len(figure.F)


def test_an_outline_left_open_still_separates_and_the_cut_ignores_scale():
    plate = shapes.engraved_plate()
    cut = reg.segment(plate, 4)
    share = np.bincount(cut, plate.areas) / plate.areas.sum()
    assert len(share) == 2 and abs(share[1] - np.pi * 100 / 1600) < 0.02          # the engraved circle is a region although its line has a gap
    inside = np.hypot(*plate.centroids[:, :2].T) < 9.0
    assert (cut[inside] == 1).all()
    assert reg.segment(plate, 3).max() == 0                                         # a coarser cut closes narrower gaps only
    assert (reg.segment(meshes.Mesh(plate.P * 25.0, plate.F), 4) == cut).all()      # the same figure 25 times larger cuts the same
