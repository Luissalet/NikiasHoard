"""A painted 3MF read back: the three dialects the exporter writes come back with the same faces and the same colours,
the paint codes of the slicers decode to palette indices, and the model file is read in streaming."""

from __future__ import annotations

import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pytest

from nikias_hoard.engine import export, threemf_read

from . import shapes

PALETTE = [{"hex": "#c81e23", "name": "rojo"}, {"hex": "#f5d728", "name": "amarillo"}, {"hex": "#235ac8", "name": "azul"}]


@pytest.fixture(scope="module")
def figure():
    return shapes.figure(120, 72)


def _standing(P: np.ndarray) -> np.ndarray:
    """Where ``export.write_3mf`` puts the figure: centred on x and y, standing on z = 0."""
    return P - np.array([0.5 * (P[:, 0].min() + P[:, 0].max()), 0.5 * (P[:, 1].min() + P[:, 1].max()), P[:, 2].min()])


def _package(path, model: str, extra: dict[str, str] | None = None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", export.CONTENT_TYPES)
        z.writestr("_rels/.rels", export.RELS)
        z.writestr("3D/3dmodel.model", model)
        for member, text in (extra or {}).items():
            z.writestr(member, text)
    return path


def _model(triangles: str, metadata: str = "", resources: str = "") -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xmlns="%s">%s<resources>%s'
            '<object id="1" type="model"><mesh><vertices>'
            '<vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/><vertex x="1" y="1" z="0"/>'
            '</vertices><triangles>%s</triangles></mesh></object></resources>'
            '<build><item objectid="1"/></build></model>') % (export.CORE_NS, metadata, resources, triangles)


@pytest.mark.parametrize("dialect", export.DIALECTS)
def test_a_written_figure_comes_back_with_its_faces_and_colours(tmp_path, figure, dialect):
    labels = shapes.ball_of(figure)
    path = export.write_3mf(tmp_path / f"{dialect}.3mf", figure.P, figure.F, labels, PALETTE, "Tres bolas", dialect, project=True)
    back = threemf_read.read_3mf(path)
    assert back["dialect"] == dialect
    assert back["P"].dtype == np.float64 and back["F"].dtype == np.int32 and back["labels"].dtype == np.uint8
    assert back["P"].shape == (len(figure.P), 3) and back["F"].shape == (len(figure.F), 3)
    assert (back["F"] == figure.F).all()
    expected = _standing(figure.P)
    if dialect in ("bambu", "orca"):              # the build item places the component mesh on the plate
        expected = expected + np.array([*export.PLATE_CENTRE, 0.0])
    assert np.abs(back["P"] - expected).max() < 2e-3
    assert (back["labels"] == labels).all()
    assert back["split_triangles"] == 0
    assert back["palette"] == [c["hex"] for c in PALETTE]


def test_a_bambu_project_without_its_settings_still_gives_the_palette(tmp_path, figure):
    labels = shapes.ball_of(figure)
    path = export.write_3mf(tmp_path / "bare.3mf", figure.P, figure.F, labels, PALETTE, "Tres bolas", "bambu")
    back = threemf_read.read_3mf(path)
    assert back["dialect"] == "bambu" and back["palette"] == [c["hex"] for c in PALETTE]
    assert (back["labels"] == labels).all()


def test_whole_triangle_paint_codes_decode_to_palette_indices(tmp_path):
    triangles = ('<triangle v1="0" v2="1" v3="2"/>'
                 '<triangle v1="1" v2="3" v3="2" paint_color="4"/>'
                 '<triangle v1="0" v2="2" v3="1" paint_color="8"/>'
                 '<triangle v1="0" v2="1" v3="3" paint_color="0C"/>'
                 '<triangle v1="1" v2="2" v3="3" paint_color="DC"/>')
    back = threemf_read.read_3mf(_package(tmp_path / "codes.3mf", _model(triangles)))
    assert back["dialect"] == "bambu"
    assert back["labels"].tolist() == [0, 0, 1, 2, 15]
    assert back["split_triangles"] == 0
    assert back["palette"] == []                                  # the file carries no colours


def test_a_subdivided_triangle_takes_its_first_state_and_is_counted(tmp_path):
    triangles = ('<triangle v1="0" v2="1" v3="2" paint_color="0C8C4C"/>'
                 '<triangle v1="1" v2="3" v3="2" paint_color="48C"/>'
                 '<triangle v1="0" v2="2" v3="1" paint_color="4"/>')
    back = threemf_read.read_3mf(_package(tmp_path / "split.3mf", _model(triangles)))
    assert back["labels"].tolist() == [2, 0, 0]
    assert back["split_triangles"] == 2


def test_a_colour_group_gives_the_palette_and_the_index_of_each_triangle(tmp_path):
    resources = ('<m:colorgroup id="1"><m:color color="#C81E23FF"/><m:color color="#F5D728FF"/></m:colorgroup>')
    triangles = ('<triangle v1="0" v2="1" v3="2" pid="1" p1="0"/>'
                 '<triangle v1="1" v2="3" v3="2" pid="1" p1="1"/>')
    model = _model(triangles, resources=resources).replace(
        'xmlns="%s"' % export.CORE_NS, 'xmlns="%s" xmlns:m="%s"' % (export.CORE_NS, export.MATERIAL_NS))
    back = threemf_read.read_3mf(_package(tmp_path / "group.3mf", model))
    assert back["dialect"] == "color"
    assert back["palette"] == ["#c81e23", "#f5d728"]
    assert back["labels"].tolist() == [0, 1]


def test_the_model_file_is_read_in_streaming(tmp_path, figure, monkeypatch):
    labels = shapes.ball_of(figure)
    path = export.write_3mf(tmp_path / "stream.3mf", figure.P, figure.F, labels, PALETTE, "Tres bolas", "prusa")

    def whole_tree(*args, **kwargs):
        raise AssertionError("the model was parsed as a whole tree")

    monkeypatch.setattr(ET, "fromstring", whole_tree)
    back = threemf_read.read_3mf(path)
    assert len(back["F"]) == len(figure.F) and (back["labels"] == labels).all()
