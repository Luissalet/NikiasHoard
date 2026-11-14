"""Colouring directed by a vision model.

The model never paints triangles. It looks at the references (pictures, or a textured model shown from four sides) and
chooses the few print colours. Then it is shown the figure one region at a time: the whole figure in clay with a
single region painted in a marker colour, from the side where it shows most, and says what that part is and which
colour it takes. Regions it finds mixed are cut finer and asked again the same way; afterwards it looks at its own
colouring next to the references and names the parts to correct. Everything it decided, and why, stays in the
project."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .engine import labels as lab, palette as pal, regions as reg, render
from .errors import NikiasError

SYSTEM = ("You prepare 3D-printed figures for multi-material printing. A figure is one fused, single-colour sculpt; you decide which of a "
          "few filament colours each part of it is printed in, so that the printed figure reads as the character in the references. "
          "Think like a figure painter: flat, representative colours (no shading, highlights or gradients), as few as asked, each one "
          "placed on whole parts. Answer with one JSON object and nothing else.")

YAWS = (0, 45, 90, 135, 180, 225, 270, 315)
MARK = (255, 0, 190)             # the colour a part is painted in when it is the one being asked about
TILE = 420
PER_SHEET = 6
SHEETS_PER_QUESTION = 2


def _hex_ok(text: Any) -> str:
    try:
        return pal.to_hex(pal.from_hex(str(text)))
    except ValueError:
        return ""


def _refs(svc, pid: str) -> list[bytes]:
    try:
        return [render.png(svc.reference_sheet(pid, 1000, 6))]
    except NikiasError:
        return []


def _cull(fig) -> bool:
    return fig.mesh.stats()["open_edges"] < 0.02 * len(fig.mesh.F)


def _frames(fig, size: int = TILE, zoom=None) -> list:
    """The figure from eight sides and from above: enough for every part to show somewhere."""
    views = [(yaw, 12) for yaw in YAWS] + [(0, 70)]
    return [render.rasterize(fig.mesh.P, fig.mesh.F, yaw=y, pitch=p, size=size, region=zoom, cull=_cull(fig)) for y, p in views]


def _caption(img, text: str):
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    font = render._font(max(img.width // 14, 16))
    box = draw.textbbox((0, 0), text, font=font)
    draw.rectangle([0, 0, box[2] + 16, box[3] + 14], fill=(0, 0, 0))
    draw.text((8, 5), text, fill=(255, 255, 255), font=font)
    return img


def highlight_tiles(fig, face_region: np.ndarray, frames: list, zoom=None) -> dict[int, Any]:
    """One picture per region: the whole figure in clay with only that region painted, from the side where it shows
    most, its number in the corner. A region too small to read gets a close-up in the lower right corner."""
    count = int(face_region.max()) + 1
    seen = np.zeros((len(frames), count), np.int64)
    for k, frame in enumerate(frames):
        hit = frame.face >= 0
        reg = face_region[frame.face[hit]]
        reg = reg[reg >= 0]
        seen[k] = np.bincount(reg, minlength=count)[:count]
    clay = np.array(render.CLAY, np.uint8)
    tiles: dict[int, Any] = {}
    centroids = fig.mesh.centroids
    for r in range(count):
        if seen[:, r].max() == 0:
            continue                                        # hidden from every side (inside a fold)
        frame = frames[int(seen[:, r].argmax())]
        mine = face_region == r
        face_rgb = np.where(mine[:, None], np.array(MARK, np.uint8), clay)
        img = render.shade(frame, face_rgb)
        if zoom is None and seen[:, r].max() < 0.008 * frame.size * frame.size:
            c = centroids[mine]
            centre = 0.5 * (c.min(0) + c.max(0))
            radius = max(float(np.linalg.norm(c.max(0) - c.min(0))) * 0.9, fig.mesh.diagonal * 0.08)
            yaw_pitch = [(y, 12) for y in YAWS] + [(0, 70)]
            y, p = yaw_pitch[int(seen[:, r].argmax())]
            close = render.rasterize(fig.mesh.P, fig.mesh.F, yaw=y, pitch=p, size=frame.size // 2, region=(*centre, radius), cull=_cull(fig))
            inset = render.shade(close, face_rgb)
            img.paste(inset, (frame.size - inset.width, frame.size - inset.height))
            from PIL import ImageDraw
            ImageDraw.Draw(img).rectangle([frame.size - inset.width, frame.size - inset.height, frame.size - 1, frame.size - 1], outline=(255, 255, 255), width=2)
        tiles[r] = _caption(img, str(r))
    return tiles


def _tile_sheets(tiles: dict[int, Any], ids: list[int]) -> list[bytes]:
    out = []
    for s in range(0, len(ids), PER_SHEET):
        out.append(render.png(render.sheet([tiles[r] for r in ids[s:s + PER_SHEET]], columns=3)))
    return out


def _colored_sheet(svc, fig, palette: list[dict[str, Any]], size: int = 760) -> bytes:
    table = np.full((256, 3), render.UNSET_RGB, np.uint8)
    for k, c in enumerate(palette):
        table[k] = pal.from_hex(c["hex"])
    imgs = [render.shade(render.rasterize(fig.mesh.P, fig.mesh.F, yaw=yaw, pitch=12, size=size, cull=_cull(fig)), table[fig.labels]) for yaw in (0, 90, 180, 270)]
    return render.png(render.sheet(imgs, columns=2))


def _swatch(palette: list[dict[str, Any]]) -> bytes:
    return render.png(render.swatches([pal.from_hex(c["hex"]) for c in palette], [c.get("name", "") for c in palette]))


def _int_map(data: Any, hi_key: int, hi_val: int) -> dict[int, int]:
    out: dict[int, int] = {}
    if isinstance(data, dict):
        items = data.items()
    elif isinstance(data, list):
        items = [(d.get("region"), d.get("color")) for d in data if isinstance(d, dict)]
    else:
        items = []
    for k, v in items:
        try:
            k, v = int(k), int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= k < hi_key and 0 <= v < hi_val:
            out[k] = v
    return out


def choose_palette(svc, project: dict[str, Any], fig, refs: list[bytes], count: int, wish: str) -> dict[str, Any]:
    clay = render.png(render.sheet([render.shade(render.rasterize(fig.mesh.P, fig.mesh.F, yaw=y, pitch=12, size=560, cull=_cull(fig)))
                                    for y in (0, 90, 180, 270)], columns=2))
    where = "The first picture(s) are the colour references; the last one is the uncoloured print figure from four sides." if refs else \
        "There are no reference pictures: use what you know of the character. The picture is the uncoloured print figure from four sides."
    user = (f"Figure: {project['name']} (collection: {project['source'].get('group', '')}).\n{where}\n"
            f"Choose the {count} filament colours (fewer if the character has fewer) that best represent it when printed. Merge shades of "
            "one colour; keep a small colour only if the character is not recognisable without it (eyes, a logo). Put first the colour "
            "that covers most of the figure.\n" + (f"The user asks: {wish}\n" if wish else "") +
            'JSON: {"character": "who it is", "palette": [{"name": "short colour name in Spanish", "hex": "#rrggbb", "where": "parts that take it"}], '
            '"left_out": "colours of the references you merged or dropped and why"}')
    answer = svc.llm.ask(SYSTEM, user, refs + [clay], max_tokens=3000)
    colors = []
    for c in answer.get("palette") or []:
        if isinstance(c, dict) and _hex_ok(c.get("hex")):
            colors.append({"hex": _hex_ok(c["hex"]), "name": str(c.get("name") or "").strip()[:40] or pal.name_of(pal.from_hex(_hex_ok(c["hex"]))),
                           "where": str(c.get("where") or "").strip()[:200]})
    if not colors:
        raise NikiasError("failed", "El modelo no propuso ninguna paleta.", "Vuelve a lanzarlo o añade referencias más claras.")
    return {"character": str(answer.get("character") or "")[:120], "palette": colors[:count], "left_out": str(answer.get("left_out") or "")[:400]}


def assign_regions(svc, project, fig, refs, palette, face_region: np.ndarray, wish: str, say=None, zoom=None, what: str = "the figure",
                   progress: tuple[float, float] = (0.3, 0.55)) -> dict[str, Any]:
    """Ask the model, a dozen regions at a time, what each region is and which palette colour it takes."""
    count = int(face_region.max()) + 1
    tiles = highlight_tiles(fig, face_region, _frames(fig, TILE, zoom), zoom)
    ids = sorted(tiles)
    listing = "\n".join(f"{k}: {c['name']} {c['hex']}" + (f" ({c['where']})" if c.get("where") else "") for k, c in enumerate(palette))
    step = PER_SHEET * SHEETS_PER_QUESTION
    mapping: dict[int, int] = {}
    parts: dict[int, str] = {}
    mixed: list[dict[str, Any]] = []
    notes: list[str] = []
    for n, start in enumerate(range(0, len(ids), step)):
        batch = ids[start:start + step]
        if say:
            say(f"el modelo mira las regiones {batch[0]}-{batch[-1]} de {what if zoom is not None else 'la figura'}", progress[0] + (progress[1] - progress[0]) * start / max(len(ids), 1))
        lead = ("Picture 1: the colour references. " if refs else "") + "Then the palette strip. Then tiles: every tile shows " + what + \
            " in plain grey with exactly ONE part painted bright magenta; the number in the tile's corner is that part's number. " \
            "A small part also has a close-up in the tile's lower right corner. Magenta is only a marker, not a colour of the character."
        user = (f"Figure: {project['name']} (collection: {project['source'].get('group', '')}).\n{lead}\nPalette (index: name):\n{listing}\n"
                f"The parts in these tiles are numbers {', '.join(str(r) for r in batch)}. For each one say what the magenta part is (a few words) "
                "and the palette index it is printed in on the real character. If the magenta part clearly covers areas of different colours "
                '(for example hair and face together), set "mixed" to true and give the colour of its larger share.\n'
                + (f"The user asks: {wish}\n" if wish else "") +
                'JSON: {"parts": {"<number>": {"is": "left glove", "color": 2, "mixed": false}}, "notes": "anything unclear"}')
        answer = svc.llm.ask(SYSTEM, user, refs + [_swatch(palette)] + _tile_sheets(tiles, batch), max_tokens=5000)
        got = answer.get("parts") if isinstance(answer.get("parts"), dict) else answer.get("regions") if isinstance(answer.get("regions"), dict) else {}
        for key, val in got.items():
            try:
                r = int(key)
            except (TypeError, ValueError):
                continue
            if r not in batch:
                continue
            if isinstance(val, dict):
                try:
                    c = int(val.get("color"))
                except (TypeError, ValueError):
                    continue
                parts[r] = str(val.get("is") or "")[:80]
                if val.get("mixed") is True:
                    mixed.append({"region": r, "why": parts[r]})
            else:
                try:
                    c = int(val)
                except (TypeError, ValueError):
                    continue
            if 0 <= c < len(palette):
                mapping[r] = c
        if answer.get("notes"):
            notes.append(str(answer["notes"])[:300])
    return {"mapping": mapping, "parts": parts, "mixed": mixed, "notes": " ".join(notes)[:600], "hidden": sorted(set(range(count)) - set(ids))}


def _apply(fig, face_region: np.ndarray, mapping: dict[int, int], only_unset: bool = False) -> int:
    count = int(face_region.max()) + 1
    table = np.full(count + 1, -1, np.int64)
    for r, c in mapping.items():
        table[r] = c
    new = table[np.where(face_region >= 0, face_region, count)]
    sel = new >= 0
    if only_unset:
        sel &= fig.labels == lab.UNSET
    fig.labels[sel] = new[sel].astype(np.uint8)
    return int(sel.sum())


def where_are(svc, project, fig, refs, palette, parts: dict[int, str], idle: list[int]) -> list[int]:
    """Palette colours no part received (eyes inside a head, a logo on a chest): the parts that contain them."""
    rows = "\n".join(f"{r}: {name or 'part'}" for r, name in sorted(parts.items()))
    names = ", ".join(f"{palette[k]['name']} ({palette[k].get('where') or 'no place given'})" for k in idle)
    user = (f"Figure: {project['name']}.\n" + ("Picture 1: the colour references. " if refs else "") + "The last picture is the uncoloured figure from four sides.\n"
            f"These palette colours have not been given to any part: {names}.\nThe parts are (number: what it is):\n{rows}\n"
            "Each of those colours sits inside one of these parts (eyes inside the head, a pattern on the body). Give the numbers of the parts "
            "that contain them, at most three in all.\n"
            'JSON: {"inside": [0, 4]}')
    clay = render.png(render.sheet([render.shade(render.rasterize(fig.mesh.P, fig.mesh.F, yaw=y, pitch=12, size=560, cull=_cull(fig)))
                                    for y in (0, 90, 180, 270)], columns=2))
    answer = svc.llm.ask(SYSTEM, user, refs + [clay], max_tokens=1500)
    out = []
    for v in answer.get("inside") or []:
        try:
            r = int(v)
        except (TypeError, ValueError):
            continue
        if r in parts and r not in out:
            out.append(r)
    return out[:3]


def split_mixed(svc, project, fig, refs, palette, mixed: list[dict[str, Any]], wish: str, say, log: list) -> None:
    """Regions the model found mixed are cut finer and asked again, one by one."""
    detail = int(project.get("detail", 3))
    if not mixed or detail >= 5:
        return
    fine = reg.segment(fig.mesh, min(5, detail + 2), 250)
    for n, mx in enumerate(mixed[:4]):
        r = mx["region"]
        inside = fig.regions == r
        sub_ids, inv = np.unique(fine[inside], return_inverse=True)
        area = np.bincount(inv, fig.mesh.areas[inside])
        keep = np.flatnonzero(area >= 0.004 * area.sum())
        keep = keep[np.argsort(-area[keep])][:24]
        if len(keep) < 2:
            log.append({"step": "split", "region": r, "result": "no se pudo partir más: queda para el pincel", "why": mx["why"]})
            continue
        rank = np.full(len(sub_ids), -1, np.int64)
        rank[keep[np.argsort(-area[keep])]] = np.arange(len(keep))
        face_sub = np.full(len(fig.regions), -1, np.int64)
        face_sub[inside] = rank[inv]
        c = fig.mesh.centroids[inside]
        centre = 0.5 * (c.min(0) + c.max(0))
        radius = float(np.linalg.norm(c.max(0) - c.min(0))) * 0.55
        say(f"partiendo la región {r} que mezcla colores", 0.55 + 0.05 * n)
        got = assign_regions(svc, project, fig, refs, palette, face_sub, wish, zoom=(*centre, max(radius, 1e-3)), what=f"a close-up of the part \"{mx['why'] or r}\"")
        painted = _apply(fig, face_sub, got["mapping"])
        log.append({"step": "split", "region": r, "pieces": int(len(keep)), "assigned": {str(k): v for k, v in got["mapping"].items()},
                    "parts": {str(k): v for k, v in got["parts"].items()}, "faces": painted, "why": mx["why"]})


def review(svc, project, fig, refs, palette, parts: dict[int, str], wish: str) -> dict[str, Any]:
    """The model looks at its colouring next to the references and names the parts whose colour is wrong."""
    count = int(fig.regions.max()) + 1
    current = {}
    for r in range(count):
        vals = fig.labels[fig.regions == r]
        vals = vals[vals != lab.UNSET]
        if len(vals):
            current[r] = int(np.bincount(vals, minlength=len(palette)).argmax())
    listing = "\n".join(f"{k}: {c['name']} {c['hex']}" for k, c in enumerate(palette))
    rows = "\n".join(f"{r}: {parts.get(r) or 'part'} -> {palette[c]['name']} ({c})" for r, c in sorted(current.items()))
    user = (f"Figure: {project['name']}.\n" + ("Picture 1: the colour references. " if refs else "") +
            "The last picture is the figure exactly as it would print with the current colouring, from four sides.\n"
            f"Palette:\n{listing}\nParts and the colour each has now (number: part -> colour):\n{rows}\n"
            "Compare the printed look with the character part by part. List ONLY the parts whose colour is clearly wrong, with the palette "
            "index they should have; if it already reads as the character, change nothing. You may also correct a palette colour's hex.\n"
            + (f"The user asks: {wish}\n" if wish else "") +
            'JSON: {"ok": true or false, "changes": {"7": 2}, "palette": [{"index": 1, "hex": "#rrggbb"}], "comment": "what you saw"}')
    answer = svc.llm.ask(SYSTEM, user, refs + [_colored_sheet(svc, fig, palette)], max_tokens=4000)
    tweaks = []
    for t in answer.get("palette") or []:
        if isinstance(t, dict) and _hex_ok(t.get("hex")):
            try:
                k = int(t.get("index"))
            except (TypeError, ValueError):
                continue
            if 0 <= k < len(palette):
                tweaks.append((k, _hex_ok(t["hex"])))
    changes = {r: c for r, c in _int_map(answer.get("changes"), count, len(palette)).items() if current.get(r) != c}
    return {"ok": bool(answer.get("ok")), "changes": changes, "tweaks": tweaks, "comment": str(answer.get("comment") or "")[:500]}


def run(svc, pid: str, say, params: dict[str, Any]) -> dict[str, Any]:
    project = svc.store.get(pid)
    fig = svc._figure(pid)
    s = svc.settings.get()
    count = int(params.get("colors") or s["colors"])
    rounds = int(params["rounds"]) if int(params.get("rounds", -1)) >= 0 else int(s["director_rounds"])
    wish = str(params.get("wish") or "")
    refs = _refs(svc, pid)
    log: list[dict[str, Any]] = []
    started = time.time()

    say("el modelo elige la paleta", 0.08)
    chosen = choose_palette(svc, project, fig, refs, count, wish)
    palette = chosen["palette"]
    log.append({"step": "palette", "character": chosen["character"], "palette": palette, "left_out": chosen["left_out"]})

    say("el modelo reparte los colores por regiones", 0.3)
    got = assign_regions(svc, project, fig, refs, palette, fig.regions.astype(np.int64), wish, say)
    svc._push_undo(fig)
    fig.labels[:] = lab.UNSET
    _apply(fig, fig.regions.astype(np.int64), got["mapping"])
    missing = sorted(set(range(int(fig.regions.max()) + 1)) - set(got["mapping"]))
    log.append({"step": "assign", "assigned": len(got["mapping"]), "not_seen": missing, "mixed": got["mixed"], "notes": got["notes"],
                "parts": {str(k): v for k, v in got["parts"].items()}, "colors": {str(k): v for k, v in got["mapping"].items()}})

    mixed = list(got["mixed"])
    used = set(got["mapping"].values())
    idle = [k for k in range(len(palette)) if k not in used]
    if idle:                                               # a colour of the palette nobody got: it lives inside a bigger part
        say("el modelo busca dónde van los colores sin repartir", 0.52)
        for r in where_are(svc, project, fig, refs, palette, got["parts"], idle):
            if all(m["region"] != r for m in mixed):
                mixed.append({"region": r, "why": got["parts"].get(r, "")})
        log.append({"step": "idle", "colors": [palette[k]["name"] for k in idle], "inside": [m["region"] for m in mixed]})
    split_mixed(svc, project, fig, refs, palette, mixed, wish, say, log)
    fig.labels = svc._fill_unset(fig.labels, fig.mesh) if (fig.labels != lab.UNSET).any() else np.zeros_like(fig.labels)

    for n in range(rounds):
        say(f"el modelo revisa su coloreado ({n + 1} de {rounds})", 0.7 + 0.2 * n / max(rounds, 1))
        seen = review(svc, project, fig, refs, palette, got["parts"], wish)
        for k, hexv in seen["tweaks"]:
            palette[k]["hex"] = hexv
        changed = _apply(fig, fig.regions.astype(np.int64), seen["changes"]) if seen["changes"] else 0
        log.append({"step": "review", "round": n + 1, "ok": seen["ok"], "changes": {str(k): v for k, v in seen["changes"].items()},
                    "palette": [list(t) for t in seen["tweaks"]], "faces": changed, "comment": seen["comment"]})
        if seen["ok"] or not (seen["changes"] or seen["tweaks"]):
            break

    say("limpiando bordes e islas", 0.94)
    cleaned = svc._clean(fig, s["min_detail_mm"], s["smooth"])
    counts = np.bincount(fig.labels[fig.labels != lab.UNSET], minlength=len(palette))
    dropped = [palette[k]["name"] for k in range(len(palette)) if counts[k] == 0]
    if dropped and len(dropped) < len(palette):            # a colour that ended on no face is a spool nobody needs to load
        keep = [k for k in range(len(palette)) if counts[k] > 0]
        remap = np.full(256, lab.UNSET, np.uint8)
        remap[keep] = np.arange(len(keep), dtype=np.uint8)
        fig.labels = remap[fig.labels]
        palette = [palette[k] for k in keep]
        log.append({"step": "unused", "colors": dropped})
    spools = s["filaments"]
    entries = []
    for c in palette:
        entry = {"hex": c["hex"], "name": c["name"], "custom_name": True, "where": c.get("where", "")}
        near = pal.nearest_filament(pal.from_hex(c["hex"]), spools)
        if near:
            entry["filament"] = near
        entries.append(entry)
    project = svc.store.get(pid)
    project["palette"] = entries
    project["method"] = "ai"
    project["director"] = {"model": svc.llm.status().get("model", ""), "character": chosen["character"], "references": len(project["sources"]),
                           "wish": wish, "seconds": round(time.time() - started, 1), "log": log}
    svc.store.save(project)
    svc._save_labels(pid, fig)
    return {"method": "ai", "character": chosen["character"], "colors": len(entries), "regions_assigned": len(got["mapping"]),
            "regions_mixed": len(got["mixed"]), "review_rounds": sum(1 for e in log if e["step"] == "review"), **cleaned}
