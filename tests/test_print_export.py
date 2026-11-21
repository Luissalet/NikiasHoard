import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np

from nikias_hoard.engine import export


def test_painted_export_records_only_slots_assigned_to_triangles(tmp_path):
    vertices = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]])
    faces = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]])
    labels = np.array([0, 2, 2, 2])
    palette = [{"hex": "#26213f"}, {"hex": "#ffffff"}, {"hex": "#efc66b"}]
    ns = {"m": export.CORE_NS, "mat": export.MATERIAL_NS}
    painted = export.write_3mf(tmp_path / "painted.3mf", vertices, faces, labels, palette, "Figure", "bambu")
    with ZipFile(painted) as package:
        main = ET.fromstring(package.read("3D/3dmodel.model"))
        assert main.find("m:metadata[@name='Application']", ns).text == export.BAMBU_APP
        metadata = main.find("m:metadata[@name='nikias:PaintedSlots']", ns)
        assert json.loads(metadata.text) == [
            {"slot": 1, "colour": "#26213F", "faces": 1},
            {"slot": 3, "colour": "#EFC66B", "faces": 3},
        ]
        part = ET.fromstring(package.read("3D/Objects/object_1.model"))
        assert [row.get("paint_color") for row in part.findall(".//m:triangle", ns)] == [None, "0C", "0C", "0C"]
    model = export.write_3mf(tmp_path / "model.3mf", vertices, faces, labels, palette, "Figure", "orca")
    with ZipFile(model) as package, ZipFile(painted) as source:
        assert package.namelist() == source.namelist()
        for item in source.namelist():
            original = source.read(item)
            expected = original.replace(export.BAMBU_APP.encode(), b"Nikias's Hoard", 1) if item == "3D/3dmodel.model" else original
            assert package.read(item) == expected
    standard = export.write_3mf(tmp_path / "standard.3mf", vertices, faces, labels, palette, "Figure", "color")
    with ZipFile(standard) as package:
        main = ET.fromstring(package.read("3D/3dmodel.model"))
        assert main.find("m:metadata[@name='nikias:PaintedSlots']", ns) is None
        assert [row.get("p1") for row in main.findall(".//m:triangle", ns)] == ["0", "2", "2", "2"]
