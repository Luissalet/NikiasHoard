"""A coloured figure as one closed solid per colour.

A colour on the surface is only a skin: its faces. To print each colour with its own filament every colour needs a body.
The figure is split into a core of one colour and a skin of every other colour, each skin as deep as ``depth`` (a share of
the figure's diagonal, shallower wherever the figure is thin so the skin never reaches the other side):

- the skin of a colour is its surface faces, the same faces moved inwards along the surface normal, and a wall along
  every edge where its patch ends;
- the core is the faces of its own colour, the moved faces of all the skins and the walls the skins do not share with
  each other.

The pieces share their walls (the wall between two of them is the same quadrilateral, split on the same diagonal, walked
in opposite directions), so together they fill exactly the volume of the figure, nothing overlaps and each piece is
closed if the figure is.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.spatial import cKDTree

DEPTH_STEPS = (1.0, 0.7, 0.5, 0.35, 0.25)       # the skin tries these shares of its depth, deepest first, at each point
SHALLOWEST = 0.15


def vertex_normals(P: np.ndarray, F: np.ndarray) -> np.ndarray:
    cross = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    out = np.zeros_like(P)
    for k in range(3):
        for c in range(3):
            out[:, c] += np.bincount(F[:, k], cross[:, c], minlength=len(P))
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-30)


def _depths(P: np.ndarray, F: np.ndarray, N: np.ndarray, verts: np.ndarray, depth: float) -> np.ndarray:
    """How far in each skin point may go: the deepest step whose spot is inside the figure and nearer to this surface
    than to any other one."""
    centroids = P[F].mean(1)
    face_n = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    face_n /= np.maximum(np.linalg.norm(face_n, axis=1, keepdims=True), 1e-30)
    tree = cKDTree(centroids)
    out = np.full(len(verts), SHALLOWEST * depth)
    todo = np.ones(len(verts), bool)
    for step in DEPTH_STEPS:
        d = depth * step
        idx = np.flatnonzero(todo)
        if not len(idx):
            break
        q = P[verts[idx]] - N[verts[idx]] * d
        dist, near = tree.query(q, workers=-1)
        inside = np.einsum("ij,ij->i", q - centroids[near], face_n[near]) < 0
        good = inside & (dist >= 0.6 * d)
        out[idx[good]] = d
        todo[idx[good]] = False
    return out


def _half_edges(F: np.ndarray, n: int):
    """Every directed edge with its face and the face across it (-1 where the surface ends or three faces meet)."""
    F = F.astype(np.int64)
    a = F.reshape(-1)
    b = F[:, [1, 2, 0]].reshape(-1)
    face = np.repeat(np.arange(len(F)), 3)
    key = np.minimum(a, b) * n + np.maximum(a, b)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    start = np.concatenate([[True], ks[1:] != ks[:-1]])
    run = np.cumsum(start) - 1
    size = np.bincount(run)
    twin = np.full(len(a), -1, np.int64)
    pair = size[run] == 2
    first = np.flatnonzero(start)[run]
    pos = np.arange(len(ks))
    other = np.where(pos == first, pos + 1, pos - 1)
    sel = order[pair]
    twin[sel] = face[order[other[pair]]]
    return a, b, face, twin


def _wall(a: np.ndarray, b: np.ndarray, down: np.ndarray) -> np.ndarray:
    """The two triangles of the wall under the edge a -> b of a skin face, facing away from the skin. ``down`` maps a
    point to its moved copy. Split on the diagonal of the lower-numbered end so both sides of a wall agree."""
    ad, bd = down[a], down[b]
    fwd = a < b
    t1 = np.where(fwd[:, None], np.stack([a, ad, bd], 1), np.stack([b, a, ad], 1))
    t2 = np.where(fwd[:, None], np.stack([a, bd, b], 1), np.stack([b, ad, bd], 1))
    return np.concatenate([t1, t2])


def split(P: np.ndarray, F: np.ndarray, labels: np.ndarray, depth: float = 0.02, core: Optional[int] = None):
    """``{colour: (P, F)}``: one solid for each colour on the surface. ``depth`` is a share of the figure's diagonal.
    ``core`` is the colour of the inside (default: the one with most surface). Returns ``(solids, info)``."""
    P = np.asarray(P, np.float64)
    F = np.asarray(F, np.int64)
    lab = np.asarray(labels).astype(np.int64)
    diag = float(np.linalg.norm(P.max(0) - P.min(0)))
    areas = 0.5 * np.linalg.norm(np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]]), axis=1)
    present = np.unique(lab)
    if core is None or core not in present:
        core = int(present[np.argmax([areas[lab == c].sum() for c in present])])
    info = {"core": core, "depth": depth * diag, "colours": [int(c) for c in present]}
    skin = lab != core
    if not skin.any():
        return {core: _compact(P, F)}, info
    n = len(P)
    N = vertex_normals(P, F)
    verts = np.unique(F[skin])
    t = _depths(P, F, N, verts, depth * diag)
    shift = np.zeros(n)
    shift[verts] = t
    for _ in range(4):                                  # a moved face that turned over or collapsed means the skin is too deep there
        Pd = P - N * shift[:, None]
        old = np.cross(P[F[skin, 1]] - P[F[skin, 0]], P[F[skin, 2]] - P[F[skin, 0]])
        new = np.cross(Pd[F[skin, 1]] - Pd[F[skin, 0]], Pd[F[skin, 2]] - Pd[F[skin, 0]])
        on, nn = np.linalg.norm(old, axis=1), np.linalg.norm(new, axis=1)
        bad = (np.einsum("ij,ij->i", old, new) < 0.2 * on * nn) | (nn < 0.2 * on)
        if not bad.any():
            break
        shift[np.unique(F[skin][bad])] *= 0.5
    down = np.arange(n)
    down[verts] = n + np.arange(len(verts))
    Pall = np.vstack([P, (P - N * shift[:, None])[verts]])
    a, b, face, twin = _half_edges(F, n)
    f_lab = lab[face]
    t_lab = np.where(twin >= 0, lab[np.where(twin >= 0, twin, 0)], -1)
    edge_skin = f_lab != core
    Fd = down[F]
    out = {}
    for c in present.tolist():
        if c == core:
            continue
        mine = lab == c
        wall = edge_skin & (f_lab == c) & (t_lab != c)
        tris = [F[mine], Fd[mine][:, [0, 2, 1]], _wall(a[wall], b[wall], down)]
        out[c] = _compact(Pall, np.concatenate(tris))
    wall = edge_skin & (t_lab == core)
    w = _wall(a[wall], b[wall], down)[:, [0, 2, 1]]
    out[core] = _compact(Pall, np.concatenate([F[~skin], Fd[skin], w]))
    return out, info


def _compact(P: np.ndarray, F: np.ndarray):
    used = np.zeros(len(P), bool)
    used[F.reshape(-1)] = True
    remap = np.cumsum(used) - 1
    return P[used], remap[F].astype(np.int32)
