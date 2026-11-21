"""The whole app over HTTP: library, a project, references, both ways of colouring, palette and paint edits, pictures,
export and the agent tools. The vision model is a stand-in that answers like one."""

from __future__ import annotations

import zipfile

import numpy as np
import pytest

from nikias_hoard.engine import mesh as meshes

from . import shapes


def write_reference_obj(folder):
    scene = shapes.textured()
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "amy.obj", "w") as fh:
        fh.write("mtllib amy.mtl\n")
        for v in scene.P:
            fh.write("v %f %f %f\n" % tuple(v))
        fh.write("vt 0 0\n")
        for k in range(3):
            fh.write(f"usemtl m{k}\n")
            for f in scene.F[scene.M == k]:
                fh.write("f %d/1 %d/1 %d/1\n" % tuple(f + 1))
    with open(folder / "amy.mtl", "w") as fh:
        for k in range(3):
            fh.write("newmtl m%d\nKd %f %f %f\n" % (k, *(shapes.COLORS[k] / 255)))
    return folder / "amy.obj"


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from PIL import Image

    from nikias_hoard.config import Config
    from nikias_hoard.main import create_app
    from nikias_hoard.services import Services

    lib = tmp_path / "lib" / "Sonic"
    lib.mkdir(parents=True)
    fig = shapes.figure(120, 72)
    lying = fig.P @ np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]]).T + [40.0, -7, 3]      # the flat variant is saved lying down, elsewhere
    for name in ("Amy", "Amy flat", "Amy pivot", "Amy base", "Amy hammer left", "Big"):
        meshes.write_stl(lib / f"{name}.stl", lying if name == "Amy flat" else fig.P, fig.F)
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (64, 64), (10, 200, 200)).save(photos / "Amy.png")
    refs = tmp_path / "refs"
    write_reference_obj(refs / "Amy model")
    Image.new("RGB", (80, 60), (200, 30, 35)).save(refs / "amy art.png")
    svc = Services(Config(data_dir=tmp_path / "data", background=False))
    svc.settings.update({"library_dirs": [str(tmp_path / "lib")], "photo_dirs": [str(photos)], "reference_dirs": [str(refs)], "web_references": False})
    with TestClient(create_app(services=svc), base_url="http://127.0.0.1") as c:
        c.svc, c.lib, c.refs = svc, lib, refs
        yield c


def call(c, tool, **arguments):
    r = c.post("/api/ui/call", json={"name": tool, "arguments": arguments})
    assert r.status_code == 200, r.text
    return r.json()


def fails(c, tool, **arguments):
    r = c.post("/api/ui/call", json={"name": tool, "arguments": arguments})
    assert r.status_code >= 400, r.text
    return r.json()


def test_health_and_catalogue(client):
    assert client.get("/api/health").json()["service"] == "nikias-hoard"
    tools = client.get("/api/agent/tools").json()
    names = {t["name"] for t in tools["tools"]}
    assert {"library_scan", "project_create", "source_add", "color_start", "palette_set", "paint", "figure_look", "result_export"} <= names
    assert "multi-colour" in tools["instructions"]
    assert client.post("/api/agent/call", json={"name": "status"}).status_code in (401, 403)
    r = client.post("/api/agent/call", json={"name": "status", "arguments": {}}, headers={"Authorization": f"Bearer {client.svc.token}"})
    assert r.status_code == 200 and "formats" in r.text


def test_library_groups_variants_and_pieces(client):
    lib = call(client, "library_scan")
    assert [e["name"] for e in lib["entries"]] == ["Amy", "Big"]
    amy = lib["entries"][0]
    assert [v["name"] for v in amy["variants"]] == ["flat", "pivot"]
    assert [p["name"] for p in amy["parts"]] == ["base", "hammer left"]
    assert amy["photo"] and not lib["entries"][1]["photo"] and amy["group"] == "Sonic"
    assert call(client, "library_scan", query="big")["count"] == 1
    assert call(client, "library_scan", group="sonic")["count"] == 2
    assert client.get("/api/library/photo", params={"name": "Amy"}).headers["content-type"] == "image/jpeg"
    assert client.get("/api/library/photo", params={"name": "Big"}).status_code == 404


def open_amy(client):
    return call(client, "project_create", source=str(client.lib / "Amy.stl"))


def test_project_opens_once_and_serves_its_mesh(client):
    p = open_amy(client)
    assert p["regions"] == 3 and p["mesh"]["open_edges"] == 0 and p["colored_share"] == 0
    assert open_amy(client)["id"] == p["id"]
    assert call(client, "library_scan", refresh=True)["entries"][0]["project_id"] == p["id"]
    raw = client.get(f"/api/projects/{p['id']}/figure.bin").content
    nv, nf = np.frombuffer(raw[4:12], "<u4")
    assert raw[:4] == b"NKS1" and len(raw) == 12 + nv * 12 + nf * 12 and nf == p["mesh"]["faces"]
    assert len(client.get(f"/api/projects/{p['id']}/faces/labels.bin").content) == nf
    assert len(client.get(f"/api/projects/{p['id']}/faces/regions.bin").content) == 2 * nf
    assert fails(client, "project_create", source=str(client.lib / "nope.stl"))["code"] == "not_found"
    assert call(client, "project_rename", project_id=p["id"], name="Amy Rose")["name"] == "Amy Rose"


def test_colour_from_a_textured_model(client):
    pid = open_amy(client)["id"]
    found = call(client, "source_find", project_id=pid)
    assert {c["kind"] for c in found["candidates"]} == {"model", "image"} and found["candidates"][0]["kind"] == "model"
    src = call(client, "source_add", project_id=pid, path=str(client.refs / "Amy model"))
    assert src["kind"] == "model" and src["good"] and src["fit"] < 0.012
    assert client.get(f"/api/projects/{pid}/sources/{src['id']}.png").status_code == 200
    job = call(client, "color_start", project_id=pid, colors=3)
    assert job["state"] == "done", job
    assert job["result"]["method"] == "model" and job["result"]["suggested_colors"] == 3
    p = call(client, "project_get", project_id=pid)
    assert [c["name"] for c in p["palette"]] == ["rojo", "amarillo", "azul"]
    assert p["colored_share"] == 1 and [c["patches"] for c in p["colors"]] == [1, 1, 1]
    reference_colors = p["transfer"]["reference_colors"]
    assert reference_colors == job["result"]["reference_colors"]
    assert len(reference_colors) == 3 and all(c["retained"] for c in reference_colors)
    assert 0.5 < p["colors"][0]["share"] < 0.65
    rows = call(client, "regions_set", project_id=pid)["regions"]
    assert [r["color"] for r in rows] == [0, 1, 2]
    two = call(client, "palette_set", project_id=pid, count=2)
    assert len(two["palette"]) == 2 and two["colored_share"] == 1


def fake_model(answers):
    """A stand-in for the vision model: gives the next canned answer and remembers what it was asked."""
    asked = []

    def ask(system, user, images, max_tokens=0, timeout=None):
        asked.append({"user": user, "images": len(images)})
        return answers[min(len(asked) - 1, len(answers) - 1)]
    return ask, asked


def test_colour_directed_by_the_vision_model(client):
    pid = open_amy(client)["id"]
    r = client.post(f"/api/projects/{pid}/sources/upload", params={"name": "arte oficial"}, content=(client.refs / "amy art.png").read_bytes())
    assert r.status_code == 200 and r.json()["kind"] == "image"
    ask, asked = fake_model([
        {"character": "Amy Rose", "palette": [{"name": "rosa", "hex": "#f08cb4", "where": "body"}, {"name": "rojo", "hex": "#c81e23", "where": "dress"},
                                               {"name": "blanco", "hex": "nonsense"}, {"name": "carne", "hex": "#f0c3a5", "where": "face"}],
         "parts": [{"part": "dress", "color": 1}, {"part": "face", "color": 2}, {"part": "wings", "color": 9}, {"part": "", "color": 0}]},
        {"parts": {"0": {"is": "dress and boots", "color": 1, "mixed": True}, "1": {"is": "chest", "color": 0}, "2": {"is": "head", "row": 1, "color": 0},      # the row it names decides, not the colour written next to it
                   "9": {"is": "nothing", "color": 0}}, "notes": "clear"},
        {"ok": False, "changes": {"2": 0, "1": 2}, "palette": [{"index": 1, "hex": "#d01020"}], "comment": "the head is pink"},
        {"ok": True, "changes": {}, "comment": "matches"},
    ])
    client.svc.llm.ask = ask
    client.svc.llm.status = lambda: {"available": True, "model": "stand-in"}
    job = call(client, "color_start", project_id=pid, mode="ai", colors=4, wish="botas rojas")
    assert job["state"] == "done", job
    assert job["result"]["method"] == "ai" and job["result"]["character"] == "Amy Rose" and job["result"]["colors"] == 3
    p = call(client, "project_get", project_id=pid)
    assert [c["name"] for c in p["palette"]] == ["rosa", "rojo", "carne"] and p["palette"][1]["hex"] == "#d01020"
    assert p["colored_share"] == 1
    rows = call(client, "regions_set", project_id=pid)["regions"]
    assert [r["color"] for r in rows] == [1, 2, 0]                             # the review moved the head to pink and the chest to skin
    steps = [e["step"] for e in p["director"]["log"]]
    assert steps[:3] == ["palette", "assign", "split"] and steps.count("review") == 2
    assert p["director"]["log"][2]["region"] == 0 and "pincel" in p["director"]["log"][2]["result"]      # nothing finer to cut it by
    assert "botas rojas" in asked[0]["user"] and asked[0]["images"] == 2 and asked[1]["images"] == 3
    assert p["director"]["log"][0]["chart"] == [{"part": "dress", "color": 1}, {"part": "face", "color": 2}] and "1: face -> 2 (carne)" in asked[1]["user"]
    assert asked[2]["images"] == 3                                             # the review sees the references, the colouring and the numbered colouring
    assert "0, 1, 2" in asked[1]["user"] and "2: head -> carne (2)" in asked[2]["user"]
    assert p["director"]["log"][1]["parts"] == {"0": "dress and boots", "1": "chest", "2": "head"}
    assert p["director"]["model"] == "stand-in"


def test_a_colour_nobody_got_is_looked_for_and_dropped_if_it_stays_unused(client):
    pid = open_amy(client)["id"]
    ask, asked = fake_model([
        {"character": "Amy", "palette": [{"name": "rosa", "hex": "#f08cb4"}, {"name": "rojo", "hex": "#c81e23"}, {"name": "verde", "hex": "#2e9e4f", "where": "eyes"}]},
        {"parts": {"0": {"is": "dress", "color": 1}, "1": {"is": "body", "color": 0}, "2": {"is": "head", "color": 0}}},
        {"inside": [2, 7]},
        {"ok": True},
    ])
    client.svc.llm.ask = ask
    client.svc.llm.status = lambda: {"available": True, "model": "stand-in"}
    job = call(client, "color_start", project_id=pid, mode="ai", colors=3, rounds=1)
    assert job["state"] == "done", job
    p = call(client, "project_get", project_id=pid)
    steps = [e["step"] for e in p["director"]["log"]]
    assert steps == ["palette", "assign", "idle", "split", "review", "unused"]
    assert p["director"]["log"][2] == {"step": "idle", "colors": ["verde"], "inside": [2]} and "verde (eyes)" in asked[2]["user"]
    assert [c["name"] for c in p["palette"]] == ["rosa", "rojo"] and p["colored_share"] == 1
    assert [c["share"] > 0 for c in p["colors"]] == [True, True]


def test_ai_needs_a_model_and_model_needs_a_source(client):
    pid = open_amy(client)["id"]
    client.svc.llm.status = lambda: {"available": False, "reason": "apagado"}
    assert fails(client, "color_start", project_id=pid, mode="ai")["code"] == "unavailable"
    assert fails(client, "color_start", project_id=pid, mode="model")["code"] == "invalid"


def test_palette_and_paint_by_hand(client):
    pid = open_amy(client)["id"]
    assert fails(client, "paint", project_id=pid, color=0, everything=True)["code"] == "invalid"
    p = call(client, "palette_set", project_id=pid, colors=[{"hex": "#f5f5f5"}, {"hex": "#c12e1f", "name": "rojo vivo"}, {"hex": "#161616"}])
    assert [c["name"] for c in p["palette"]] == ["blanco", "rojo vivo", "negro"]
    assert call(client, "paint", project_id=pid, color=0, everything=True)["changed_faces"] > 0
    assert call(client, "paint", project_id=pid, color=1, regions=[1])["changed_faces"] > 0
    brush = call(client, "paint", project_id=pid, color=2, brush={"points": [[0, -6, 43]], "radius_mm": 2.5, "normals": [[0, -1, 0]]})
    assert 0 < brush["changed_faces"] < 400
    p = call(client, "project_get", project_id=pid)
    assert p["method"] == "manual" and p["colored_share"] == 1 and p["colors"][2]["patches"] == 1
    face = int(np.frombuffer(client.get(f"/api/projects/{pid}/faces/labels.bin").content, np.uint8).tolist().index(2))
    assert client.get(f"/api/projects/{pid}/pick", params={"face": face}).json() == {"region": 2, "color": 2}
    assert call(client, "paint", project_id=pid, color=0, faces=[face])["changed_faces"] == brush["changed_faces"]     # bucket fill of that patch
    assert call(client, "paint_undo", project_id=pid)["can_undo"] == 3
    assert call(client, "clean", project_id=pid, min_patch=100 * 6.0 / client.svc._figure(pid).mesh.diagonal)["islands_removed"] == 1                             # the brush spot is under 36 mm²
    merged = call(client, "palette_set", project_id=pid, merge=[0, 2])
    assert len(merged["palette"]) == 2 and merged["colors"][1]["share"] > 0.2
    based = call(client, "palette_set", project_id=pid, base=1)
    assert based["palette"][0]["name"] == "rojo vivo" and based["colors"][0]["share"] < 0.4
    finer = call(client, "regions_set", project_id=pid, detail=5)
    assert finer["detail"] == 5 and len(finer["regions"]) >= 3
    assert call(client, "project_get", project_id=pid)["colored_share"] == 1    # recutting keeps the paint


def test_pictures(client):
    from io import BytesIO

    from PIL import Image
    pid = open_amy(client)["id"]
    call(client, "palette_set", project_id=pid, colors=[{"hex": "#c12e1f"}, {"hex": "#1f5fc4"}])
    call(client, "paint", project_id=pid, color=0, everything=True)
    call(client, "paint", project_id=pid, color=1, regions=[2])
    token = {"Authorization": f"Bearer {client.svc.token}"}
    r = client.post("/api/agent/call", json={"name": "figure_look", "arguments": {"project_id": pid, "what": "compare", "view": "front", "size": 300}},
                    headers=token).json()
    img = Image.open(BytesIO(__import__("base64").b64decode(r["_image"]["data"])))
    assert img.size == (606, 300)
    px = np.asarray(img)
    right = px[:, 306:]
    assert ((right[..., 2] > 110) & (right[..., 0] < 80)).sum() > 300 and ((right[..., 0] > 110) & (right[..., 2] < 80)).sum() > 2000
    left = px[:, :300]
    assert not ((left[..., 2] > 110) & (left[..., 0] < 80)).any()                # the clay side has no blue
    four = client.post("/api/agent/call", json={"name": "figure_look", "arguments": {"project_id": pid, "what": "regions", "views": ["front", "left", "back", "right"],
                                                                                 "size": 240}}, headers=token).json()
    assert Image.open(BytesIO(__import__("base64").b64decode(four["_image"]["data"]))).size == (2 * 240 + 6, 2 * 240 + 6)
    assert client.get(f"/api/projects/{pid}/look.png", params={"what": "clay", "yaw": 120, "pitch": 30, "size": 220}).status_code == 200
    assert fails(client, "figure_look", project_id=pid, what="sources")["code"] == "invalid"
    assert fails(client, "figure_look", project_id=pid, view="nowhere")["code"] == "invalid"
    assert "_image" not in call(client, "figure_look", project_id=pid, size=220)   # the page takes pictures by URL


def test_export_writes_the_figure_and_its_variants(client):
    pid = open_amy(client)["id"]
    assert fails(client, "result_export", project_id=pid)["code"] == "invalid"
    call(client, "palette_set", project_id=pid, colors=[{"hex": "#c12e1f"}, {"hex": "#1f5fc4"}])
    call(client, "paint", project_id=pid, color=1, regions=[2])
    out = call(client, "result_export", project_id=pid)
    names = sorted(f.replace("\\", "/").rsplit("/", 1)[-1] for f in out["files"])
    assert out["folder"].replace("\\", "/").endswith("Sonic/Multicolor") and out["uncolored_faces"] > 0 and "color 1" in out["note"]
    assert names == sorted([f"{stem} {kind}.3mf" for stem in ("Amy", "Amy flat", "Amy pivot")
                            for kind in ("Bambu-Orca", "Prusa", "color", "partes", "partes Bambu-Orca", "partes Prusa")] + ["Amy colores.txt"])
    assert [p["name"] for p in out["printability"]] == ["Amy", "Amy flat", "Amy pivot"] and all(len(p["solids"]) == 2 for p in out["printability"])
    assert [v["name"] for v in out["variants"]] == ["flat", "pivot"]
    assert all(v["fit"] < 0.005 and v["new_faces_share"] < 0.01 for v in out["variants"])
    with zipfile.ZipFile(client.lib / "Multicolor" / "Amy flat Bambu-Orca.3mf") as z:
        xml = z.read("3D/Objects/object_1.model").decode()
    region2 = int((np.frombuffer(client.get(f"/api/projects/{pid}/faces/regions.bin").content, "<u2") == 2).sum())
    assert abs(xml.count('paint_color="8"') - region2) < 0.02 * region2        # laid back over the figure before taking its colours
    legend = (client.lib / "Multicolor" / "Amy colores.txt").read_text(encoding="utf-8")
    assert "Color 1: rojo #c12e1f" in legend and "Color 2: azul" in legend
    assert call(client, "library_scan", refresh=True)["count"] == 2                 # the Multicolor folder is not read as figures
    one = call(client, "result_export", project_id=pid, formats=["obj", "stl"], folder=str(client.lib.parent / "out"), variants=False)
    assert len(one["files"]) == 2 + 2 + 1
    r = client.get(f"/api/projects/{pid}/download/prusa")
    assert r.status_code == 200 and "Amy%20Prusa.3mf" in r.headers["content-disposition"] and r.content[:2] == b"PK"


def two_pieces_with_a_hole(client):
    """A figure and a ball floating beside it, with eight faces missing and some turned over."""
    fig = shapes.figure(90, 56)
    ball = shapes.figure(24, 16)
    P = np.vstack([fig.P, ball.P * 0.5 + [40.0, 0, 30.0]])
    F = np.vstack([fig.F, ball.F + len(fig.P)])
    F[::11] = F[::11][:, [0, 2, 1]]
    F = np.delete(F, np.arange(300, 308), axis=0)
    meshes.write_stl(client.lib / "Duo.stl", P, F)
    return call(client, "project_create", source=str(client.lib / "Duo.stl"))["id"]


def test_print_check_reports_and_repairs_without_touching_the_project(client):
    pid = two_pieces_with_a_hole(client)
    c = call(client, "print_check", project_id=pid, repair=True)
    assert c["check"]["open_edges"] > 0 and not c["check"]["watertight"] and c["check"]["piece_count"] == 2 and len(c["check"]["floating_pieces"]) == 1
    assert any("aristas abiertas" in a for a in c["advice"]) and any("en el aire" in a for a in c["advice"])
    assert c["after"]["watertight"] and c["repair"]["holes_closed"] >= 1 and c["advice_after"] != c["advice"]
    assert call(client, "print_check", project_id=pid)["check"]["open_edges"] == c["check"]["open_edges"]       # nothing was written
    assert "repair" not in call(client, "print_check", project_id=pid)


def test_export_writes_one_closed_solid_per_colour_and_the_loose_pieces(client):
    pid = two_pieces_with_a_hole(client)
    call(client, "palette_set", project_id=pid, colors=[{"hex": "#c12e1f"}, {"hex": "#1f5fc4"}])
    regions = call(client, "regions_set", project_id=pid)["regions"]
    call(client, "paint", project_id=pid, color=1, regions=[regions[-1]["id"]])
    out = call(client, "result_export", project_id=pid, formats=["parts", "parts-bambu", "parts-prusa"], variants=False, split=True)
    names = sorted(f.replace("\\", "/").rsplit("/", 1)[-1] for f in out["files"])
    assert names == sorted(["Duo partes Bambu-Orca.3mf", "Duo partes Prusa.3mf", "Duo partes.3mf", "Duo pieza 1.stl", "Duo pieza 2.stl", "Duo colores.txt"])
    report = out["printability"][0]
    assert report["repair"]["holes_closed"] >= 1 and report["before"]["open_edges"] > 0 and all(s["closed"] for s in report["solids"])
    assert len(report["solids"]) == 2 and out["pieces"] == ["Duo pieza 1.stl", "Duo pieza 2.stl"]
    for piece in out["pieces"]:
        m = meshes.load_stl(client.lib / "Multicolor" / piece)
        assert abs(float(m.P[:, 2].min())) < 1e-4
    raw = call(client, "result_export", project_id=pid, formats=["parts"], variants=False, repair=False, folder=str(client.lib.parent / "raw"))
    assert raw["printability"][0]["repair"] == {} and raw["pieces"] == []
    r = client.get(f"/api/projects/{pid}/download/parts-bambu")
    assert r.status_code == 200 and r.content[:2] == b"PK" and "partes%20Bambu-Orca.3mf" in r.headers["content-disposition"]


def test_settings_and_delete(client):
    s = call(client, "settings_set", changes={"colors": 3, "min_patch": 2, "wikis": {"Sonic": "sonic.fandom.com"}})
    assert s["colors"] == 3 and s["min_patch"] == 2 and s["wikis"] == {"sonic": "sonic.fandom.com"} and "filaments" not in s
    assert fails(client, "settings_set", changes={"export_formats": ["gcode"]})["code"] == "invalid"
    assert fails(client, "settings_set", changes={"nope": 1})["code"] == "invalid"
    pid = open_amy(client)["id"]
    src = call(client, "source_add", project_id=pid, path=str(client.refs / "amy art.png"))
    assert call(client, "source_remove", project_id=pid, source_id=src["id"]) == {"removed": src["id"]}
    assert call(client, "project_delete", project_id=pid) == {"deleted": pid}
    assert call(client, "project_list")["projects"] == []
    assert (client.lib / "Amy.stl").is_file()


def test_a_figure_without_references_gets_the_character_looked_up(client, monkeypatch):
    import io

    from PIL import Image

    from nikias_hoard import references
    pid = open_amy(client)["id"]
    buf = io.BytesIO()
    Image.new("RGB", (90, 120), (240, 140, 180)).save(buf, "PNG")
    asked = []
    monkeypatch.setattr(references, "find", lambda name, group, wikis=None: asked.append((name, group)) or
                        {"data": buf.getvalue(), "title": "Amy Rose", "page": "https://wiki.example/Amy_Rose", "image": "x"})
    got = call(client, "source_fetch", project_id=pid)
    assert got["kind"] == "image" and got["name"] == "Amy Rose" and got["web"] and got["path"] == "https://wiki.example/Amy_Rose" and asked == [("Amy", "Sonic")]
    assert client.get(f"/api/projects/{pid}/sources/{got['id']}.png").status_code == 200
    call(client, "source_remove", project_id=pid, source_id=got["id"])
    client.svc.settings.update({"web_references": True})                        # and the model's colouring fetches it by itself
    ask, asked_model = fake_model([
        {"character": "Amy Rose", "palette": [{"name": "rosa", "hex": "#f08cb4"}, {"name": "rojo", "hex": "#c81e23"}]},
        {"parts": {"0": {"is": "dress", "color": 1}, "1": {"is": "body", "color": 0}, "2": {"is": "head", "color": 0}}},
        {"ok": True},
    ])
    client.svc.llm.ask = ask
    client.svc.llm.status = lambda: {"available": True, "model": "stand-in"}
    assert call(client, "color_start", project_id=pid, mode="ai", colors=2, rounds=1)["state"] == "done"
    p = call(client, "project_get", project_id=pid)
    assert p["director"]["log"][0] == {"step": "reference", "name": "Amy Rose", "page": "https://wiki.example/Amy_Rose"}
    assert [s_["name"] for s_ in p["sources"]] == ["Amy Rose"] and asked_model[0]["images"] == 2      # the reference went to the model
    monkeypatch.setattr(references, "find", lambda name, group, wikis=None: None)
    assert fails(client, "source_fetch", project_id=pid, query="Nadie")["code"] == "not_found"


def test_the_smallest_patch_is_a_share_of_the_figure_whatever_its_size(client):
    pid = open_amy(client)["id"]
    fig = client.svc._figure(pid)
    call(client, "palette_set", project_id=pid, colors=[{"hex": "#f5f5f5"}, {"hex": "#161616"}])
    call(client, "paint", project_id=pid, color=0, everything=True)
    call(client, "paint", project_id=pid, color=1, brush={"points": [[0, -6, 43]], "radius_mm": 2.5, "normals": [[0, -1, 0]]})
    spot = float(fig.mesh.areas[fig.labels == 1].sum()) ** 0.5 / fig.mesh.diagonal * 100       # the spot's side, in % of the figure
    assert call(client, "clean", project_id=pid, min_patch=spot * 0.5, smooth=0)["islands_removed"] == 0
    assert call(client, "clean", project_id=pid, min_patch=spot * 1.5, smooth=0)["islands_removed"] == 1


def test_a_small_part_that_clashes_with_its_surroundings_gets_a_second_look(client):
    from nikias_hoard import director
    pid = open_amy(client)["id"]
    call(client, "palette_set", project_id=pid, colors=[{"hex": "#f5f5f5", "name": "blanco"}, {"hex": "#f08cb4", "name": "rosa"}, {"hex": "#2e9e4f", "name": "verde"}])
    call(client, "paint", project_id=pid, color=0, everything=True)
    fig = client.svc._figure(pid)
    c = fig.mesh.centroids
    finger = np.linalg.norm(c - [0, -12, 12], axis=1) < 2.0                    # a small patch in the middle of the white body: its own region
    eye = np.linalg.norm(c - [0, 12, 12], axis=1) < 2.0
    fig.regions = fig.regions.copy()
    fig.regions[finger], fig.regions[eye] = 3, 4
    fig.labels[finger], fig.labels[eye] = 1, 2
    odd = director.odd_ones(fig)
    assert sorted((o["region"], o["color"], o["around"]) for o in odd) == [(3, 1, 0), (4, 2, 0)]
    ask, asked = fake_model([{"parts": {"3": {"is": "finger of the glove", "color": 0}, "4": {"is": "eye", "color": 2}, "0": {"color": 1}}}])
    client.svc.llm.ask = ask
    parts = {3: "skin", 4: "eye"}
    fixed = director.second_look(client.svc, client.svc.store.get(pid), fig, [], client.svc.store.get(pid)["palette"], parts, odd)
    assert fixed == {3: 0} and parts[3] == "finger of the glove"               # the finger joins the glove; the eye keeps its colour; nobody else is touched
    assert "is now rosa (1); everything around it is blanco (0)" in asked[0]["user"] and asked[0]["images"] == 1


def test_references_go_to_the_model_one_by_one_with_the_users_own_first(client):
    import io

    from PIL import Image
    pid = open_amy(client)["id"]

    def picture(rgb):
        buf = io.BytesIO()
        Image.new("RGB", (60, 40), rgb).save(buf, "PNG")
        return buf.getvalue()
    web = client.svc.add_image(pid, "de internet", picture((0, 0, 255)), "https://wiki.example/x")
    project = client.svc.store.get(pid)
    project["sources"][0]["web"] = True
    client.svc.store.save(project)
    client.svc.add_image(pid, "mia", picture((255, 0, 0)))
    client.svc.add_image(pid, "mia la ultima", picture((0, 255, 0)))
    got = [Image.open(io.BytesIO(b)).convert("RGB").getpixel((5, 5)) for b in client.svc.reference_pictures(pid)]
    assert got == [(0, 255, 0), (255, 0, 0), (0, 0, 255)] and web["id"]        # newest of the user's first, the looked-up one last


def test_reference_colour_without_output_faces_is_reported():
    import numpy as np
    from nikias_hoard.engine.palette import reference_coverage

    ochre, slate = [190, 135, 45], [52, 64, 80]
    source = np.array([ochre, ochre, slate])
    palette = np.array([ochre, slate])
    report = reference_coverage(source, palette, np.array([1, 1]), np.array([1, 1]), 0.6)
    assert report[0]["source"] == "#be872d"
    assert not report[0]["retained"] and set(report[0]["reason"]) == {"en", "es"}
    assert report[1]["retained"] and report[1]["output_share"] == 1.0
