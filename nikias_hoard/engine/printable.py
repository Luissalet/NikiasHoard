"""Is a figure a solid a slicer can print, and make it one when it is not.

``inspect`` counts what stops a surface from being a solid: edges with one face (open), edges with more than two
(non-manifold), neighbouring faces that disagree about which way is out (winding), degenerate and repeated faces, and
shells that point inwards. It also lists the loose pieces (shells) and says which would hang in the air. ``repair``
fixes what can be fixed without asking: it drops repeated, degenerate and dust faces, makes every shell agree about
its outside, turns inside-out shells round (a shell inside another is a cavity and stays as it is) and closes open
rims with a fan. Every function carries one value per face (``labels``, the colour) through.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

SPECK_FACES = 20            # a shell with fewer faces than this and a tiny share of the surface is dust
SPECK_SHARE = 2e-4
FILL_LIMIT = 200_000        # boundary edges above which holes are not closed (the surface is not a figure with a few holes)


# ---------------------------------------------------------------------------------------------------------------- edges
def _edges(F: np.ndarray, n: int):
    """Undirected key per half-edge (3 per face, in the order of F's columns), and whether it runs low -> high."""
    F = F.astype(np.int64)
    a = F.reshape(-1)
    b = F[:, [1, 2, 0]].reshape(-1)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    return lo * n + hi, a < b, a, b


def _face_graph(F: np.ndarray, n: int):
    """Pairs of faces sharing an edge, and for each pair whether they walk the shared edge the same way (wrong winding)."""
    key, up, _, _ = _edges(F, n)
    face = np.repeat(np.arange(len(F)), 3)
    order = np.argsort(key, kind="stable")
    ks, fs, us = key[order], face[order], up[order]
    same = ks[1:] == ks[:-1]
    i, j = fs[:-1][same], fs[1:][same]
    wrong = us[:-1][same] == us[1:][same]
    keep = i != j
    return i[keep], j[keep], wrong[keep]


def _first_of_each(F: np.ndarray, n: int) -> np.ndarray:
    """Indices (ascending) of the first face of each set of corners; the repeated ones are left out."""
    tri = np.sort(F, axis=1)
    if n < 2_000_000:
        _, first = np.unique(tri @ np.array([n * n, n, 1], np.int64), return_index=True)
    else:
        _, first = np.unique(tri, axis=0, return_index=True)
    return np.sort(first)


def shells(F: np.ndarray, n: int) -> np.ndarray:
    m = len(F)
    if m == 0:
        return np.zeros(0, np.int64)
    i, j, _ = _face_graph(F, n)
    g = coo_matrix((np.ones(len(i), np.int8), (i, j)), shape=(m, m))
    return connected_components(g, directed=False)[1].astype(np.int64)


def _signed_volume(P: np.ndarray, F: np.ndarray, shell: np.ndarray, count: int) -> np.ndarray:
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    return np.bincount(shell, np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0, minlength=count)


def _inside(point: np.ndarray, P: np.ndarray, F: np.ndarray) -> bool:
    """Is ``point`` inside the closed surface (P, F)? A ray in an odd direction and the parity of its crossings."""
    d = np.array([1.0, 0.0123, 0.0071])
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    e1, e2 = b - a, c - a
    h = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(det) > 1e-14
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = point - a
    u = inv * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    v = inv * (q @ d)
    t = inv * np.einsum("ij,ij->i", e2, q)
    return int((ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)).sum()) % 2 == 1


# ---------------------------------------------------------------------------------------------------------------- inspect
def inspect(P: np.ndarray, F: np.ndarray) -> dict:
    """What a slicer would stumble on, and the loose pieces of the figure."""
    P = np.asarray(P, np.float64)
    F = np.asarray(F, np.int64)
    n = max(len(P), 1)
    m = len(F)
    if m == 0:
        return {"faces": 0, "points": len(P), "watertight": False, "needs_repair": False, "pieces": [], "open_edges": 0}
    key, up, _, _ = _edges(F, n)
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    open_edges = int((cnt == 1).sum())
    non_manifold = int((cnt > 2).sum())
    i, j, wrong = _face_graph(F, n)
    flipped_edges = int(wrong.sum())
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    cross = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(cross, axis=1)
    diag = float(np.linalg.norm(P.max(0) - P.min(0))) or 1.0
    degenerate = int(((area < 1e-12 * diag * diag) | (F[:, 0] == F[:, 1]) | (F[:, 1] == F[:, 2]) | (F[:, 2] == F[:, 0])).sum())
    duplicates = int(m - len(_first_of_each(F, n)))
    shell = shells(F, n)
    count = int(shell.max()) + 1
    volume = _signed_volume(P, F, shell, count)
    faces_in = np.bincount(shell, minlength=count)
    area_in = np.bincount(shell, area, minlength=count)
    lo = np.full((count, 3), np.inf)
    hi = np.full((count, 3), -np.inf)
    for k in range(3):
        np.minimum.at(lo[:, k], shell, np.minimum(np.minimum(a[:, k], b[:, k]), c[:, k]))
        np.maximum.at(hi[:, k], shell, np.maximum(np.maximum(a[:, k], b[:, k]), c[:, k]))
    floor = float(lo[:, 2].min())
    height = float(hi[:, 2].max() - floor) or 1.0
    main = int(np.argmax(np.abs(volume))) if count else 0
    total_area = float(area.sum()) or 1.0
    pieces = []
    for s in np.argsort(-area_in):
        speck = bool(faces_in[s] < SPECK_FACES and area_in[s] / total_area < SPECK_SHARE)
        on_plate = bool(lo[s, 2] - floor <= 0.01 * height)
        pieces.append({"id": int(s), "faces": int(faces_in[s]), "area_share": round(float(area_in[s] / total_area), 5),
                       "volume_share": round(float(abs(volume[s]) / max(float(np.abs(volume).sum()), 1e-30)), 5),
                       "inside_out": bool(volume[s] < 0), "min": [round(float(x), 3) for x in lo[s]], "max": [round(float(x), 3) for x in hi[s]],
                       "on_plate": on_plate, "main": bool(s == main), "speck": speck,
                       "floating": bool(s != main and not on_plate)})
    watertight = open_edges == 0 and non_manifold == 0 and flipped_edges == 0
    report = {"faces": m, "points": int(len(P)), "open_edges": open_edges, "non_manifold_edges": non_manifold, "flipped_edges": flipped_edges,
              "degenerate_faces": degenerate, "duplicate_faces": duplicates, "inside_out_pieces": int((volume < 0).sum()),
              "volume": round(float(volume.sum()), 3), "watertight": bool(watertight), "pieces": pieces,
              "floating_pieces": [p["id"] for p in pieces if p["floating"]], "specks": [p["id"] for p in pieces if p["speck"]]}
    report["needs_repair"] = bool(not watertight or degenerate or duplicates or report["inside_out_pieces"] or report["specks"])
    return report


# ---------------------------------------------------------------------------------------------------------------- repair
def compact(P: np.ndarray, F: np.ndarray):
    used = np.zeros(len(P), bool)
    used[F.reshape(-1)] = True
    remap = np.cumsum(used) - 1
    return P[used], remap[F].astype(np.int64)


def _agree(F: np.ndarray, n: int, shell: np.ndarray) -> np.ndarray:
    """Which faces must be reversed so that neighbouring faces walk their shared edge in opposite directions. The faces
    are doubled (as they are, and reversed): the two copies of a face joined to the copies of its neighbours that
    agree with them split each shell in two halves, and the half that holds the shell's first face as it is wins."""
    m = len(F)
    i, j, wrong = _face_graph(F, n)
    s = wrong.astype(np.int64)
    cols = np.concatenate([j + m * s, j + m * (1 - s), i + m * s, i + m * (1 - s)])
    src = np.concatenate([i, i + m, j, j + m])
    g = coo_matrix((np.ones(len(src), np.int8), (src, cols)), shape=(2 * m, 2 * m))
    comp = connected_components(g, directed=False)[1]
    first = np.full(int(shell.max()) + 1, -1, np.int64)
    first[shell[::-1]] = np.arange(m)[::-1]
    chosen = comp[first]
    return comp[:m] != chosen[shell]


def _loops(F: np.ndarray, n: int):
    """Closed walks along the edges that have a single face, in the direction their face walks them."""
    key, up, a, b = _edges(F, n)
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    edge_face = np.repeat(np.arange(len(F)), 3)
    lone = cnt[inv] == 1
    if not lone.any() or int(lone.sum()) > FILL_LIMIT:
        return [], []
    ea, eb, ef = a[lone], b[lone], edge_face[lone]
    nxt: dict[int, list[int]] = {}
    for k, v in enumerate(ea.tolist()):
        nxt.setdefault(v, []).append(k)
    used = np.zeros(len(ea), bool)
    loops, loop_faces = [], []
    for start in range(len(ea)):
        if used[start]:
            continue
        verts, faces = [], []
        k = start
        while True:
            used[k] = True
            verts.append(int(ea[k]))
            faces.append(int(ef[k]))
            options = [q for q in nxt.get(int(eb[k]), []) if not used[q]]
            if not options:
                break
            k = options[0]
        closed = len(verts) >= 3 and int(eb[k]) == verts[0]
        if closed:
            loops.append(verts)
            loop_faces.append(faces)
    return loops, loop_faces


def repair(P: np.ndarray, F: np.ndarray, labels: Optional[np.ndarray] = None, close_holes: bool = True, drop_specks: bool = True):
    """The figure as a closed, outward-facing surface where that can be done. Returns ``(P, F, labels, actions)``; the
    labels follow the faces (a face the repair adds takes the commonest label around the hole it closes)."""
    P = np.asarray(P, np.float64).copy()
    F = np.asarray(F, np.int64).copy()
    lab = np.zeros(len(F), np.int64) if labels is None else np.asarray(labels).astype(np.int64).copy()
    actions = {"degenerate_removed": 0, "duplicates_removed": 0, "specks_removed": 0, "faces_reversed": 0, "pieces_turned": 0, "holes_closed": 0,
               "faces_added": 0}
    if len(F) == 0:
        return P, F.astype(np.int32), lab, actions
    n = len(P)
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    diag = float(np.linalg.norm(P.max(0) - P.min(0))) or 1.0
    ok = (area >= 1e-12 * diag * diag) & (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])
    actions["degenerate_removed"] = int((~ok).sum())
    F, lab = F[ok], lab[ok]
    first = _first_of_each(F, n)
    if len(first) != len(F):
        actions["duplicates_removed"] = int(len(F) - len(first))
        F, lab = F[first], lab[first]
    if drop_specks:
        shell = shells(F, n)
        count = int(shell.max()) + 1
        area = 0.5 * np.linalg.norm(np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]]), axis=1)
        faces_in = np.bincount(shell, minlength=count)
        share = np.bincount(shell, area, minlength=count) / max(float(area.sum()), 1e-30)
        dust = (faces_in < SPECK_FACES) & (share < SPECK_SHARE) & (count > 1)
        if dust.any():
            keep = ~dust[shell]
            actions["specks_removed"] = int(dust.sum())
            F, lab = F[keep], lab[keep]
    P, F = compact(P, F)
    n = len(P)
    shell = shells(F, n)
    flip = _agree(F, n, shell)
    if flip.any():
        F[flip] = F[flip][:, [0, 2, 1]]
        actions["faces_reversed"] = int(flip.sum())
    count = int(shell.max()) + 1
    volume = _signed_volume(P, F, shell, count)
    inverted = np.flatnonzero(volume < 0)
    if len(inverted):
        bounds_lo = np.full((count, 3), np.inf)
        bounds_hi = np.full((count, 3), -np.inf)
        for k in range(3):
            np.minimum.at(bounds_lo[:, k], shell, P[F[:, 0], k])
            np.maximum.at(bounds_hi[:, k], shell, P[F[:, 0], k])
        for s in inverted.tolist():
            point = P[F[np.flatnonzero(shell == s)[0], 0]]
            cavity = False
            for o in np.flatnonzero(volume > 0).tolist():
                if o != s and (bounds_lo[o] <= point).all() and (point <= bounds_hi[o]).all():
                    if _inside(point, P, F[shell == o]):
                        cavity = True
                        break
            if not cavity:
                F[shell == s] = F[shell == s][:, [0, 2, 1]]
                actions["pieces_turned"] += 1
    if close_holes:
        loops, loop_faces = _loops(F, n)
        extra_P, extra_F, extra_L = [], [], []
        base = n
        for verts, faces in zip(loops, loop_faces):
            ring = P[verts]
            centre = ring.mean(0)
            labels_around = lab[faces]
            label = int(np.bincount(labels_around).argmax())
            centre_id = base + len(extra_P)
            extra_P.append(centre)
            k = len(verts)
            nxt = np.roll(np.arange(k), -1)
            extra_F.append(np.stack([np.asarray(verts)[nxt], np.asarray(verts), np.full(k, centre_id)], 1))
            extra_L.append(np.full(k, label))
            actions["holes_closed"] += 1
            actions["faces_added"] += k
        if extra_P:
            P = np.vstack([P, np.asarray(extra_P)])
            F = np.vstack([F] + extra_F)
            lab = np.concatenate([lab] + extra_L)
    return P, F.astype(np.int32), lab, actions
