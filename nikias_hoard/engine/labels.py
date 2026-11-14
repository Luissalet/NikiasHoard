"""The colour of each face as an index into the palette, and the edits that keep it printable: speckle removed,
islands too small for a nozzle absorbed, a brush, a fill."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph

from .mesh import Mesh

UNSET = 255          # a face nobody has coloured yet


def smooth(labels: np.ndarray, mesh: Mesh, rounds: int = 2) -> np.ndarray:
    """A face whose neighbours agree on another colour takes it (removes salt-and-pepper along borders)."""
    N = mesh.neighbours
    ok = N >= 0
    safe = np.where(ok, N, 0)
    labels = labels.copy()
    for _ in range(max(int(rounds), 0)):
        nb = np.where(ok, labels[safe], labels[:, None])
        a, b, c = nb[:, 0], nb[:, 1], nb[:, 2]
        maj = np.where((a == b) | (a == c), a, np.where(b == c, b, labels))
        change = (maj != labels) & (maj != UNSET)
        if not change.any():
            break
        labels[change] = maj[change]
    return labels


def islands(labels: np.ndarray, mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """Connected patches of one colour: a patch index per face and each patch's area."""
    i, j, _ = mesh.pairs
    same = labels[i] == labels[j]
    m = len(labels)
    g = sparse.coo_matrix((np.ones(int(same.sum()), np.int8), (i[same], j[same])), shape=(m, m))
    comp = csgraph.connected_components(g, directed=False)[1]
    return comp, np.bincount(comp, mesh.areas)


def remove_islands(labels: np.ndarray, mesh: Mesh, min_area: float, rounds: int = 6) -> tuple[np.ndarray, int]:
    """Patches smaller than ``min_area`` mm² take the colour they share the longest border with."""
    labels = labels.copy()
    removed = 0
    i, j, length = mesh.pairs
    for _ in range(rounds):
        comp, area = islands(labels, mesh)
        small = area < min_area
        if not small.any():
            break
        a, b = comp[i], comp[j]
        d = (a != b) & small[a]
        d2 = (a != b) & small[b]
        rows = np.concatenate([a[d], b[d2]])
        cols = np.concatenate([labels[j][d], labels[i][d2]]).astype(np.int64)
        w = np.concatenate([length[d], length[d2]])
        if not len(rows):
            break
        k = int(cols.max()) + 1
        border = sparse.coo_matrix((w, (rows, cols)), shape=(len(area), k)).tocsr()
        best = np.asarray(border.argmax(axis=1)).reshape(-1)
        has = np.asarray(border.max(axis=1).todense()).reshape(-1) > 0
        move = small & has
        if not move.any():
            break
        sel = move[comp]
        labels[sel] = best[comp[sel]].astype(labels.dtype)
        removed += int(move.sum())
    return labels, removed


def brush(labels: np.ndarray, mesh: Mesh, center, radius: float, value: int, normal=None, only: Optional[np.ndarray] = None) -> int:
    """Paint every face within ``radius`` of ``center``. With ``normal`` only faces looking that way (not the back of a
    thin part); with ``only`` a mask of faces that may change."""
    c = mesh.centroids
    center = np.asarray(center, float)
    box = (np.abs(c - center) <= radius).all(1)
    idx = np.flatnonzero(box)
    hit = idx[((c[idx] - center) ** 2).sum(1) <= radius * radius]
    if normal is not None and len(hit):
        nrm = np.asarray(normal, float)
        nrm = nrm / max(np.linalg.norm(nrm), 1e-12)
        hit = hit[mesh.normals[hit] @ nrm > -0.2]
    if only is not None and len(hit):
        hit = hit[only[hit]]
    changed = int((labels[hit] != value).sum())
    labels[hit] = value
    return changed


def nearest_face(mesh: Mesh, point) -> int:
    d = ((mesh.centroids - np.asarray(point, float)) ** 2).sum(1)
    return int(d.argmin())


def stats(labels: np.ndarray, mesh: Mesh, count: int) -> list[dict[str, Any]]:
    """Per palette colour: area, share of the surface, how many separate patches and the smallest one."""
    comp, comp_area = islands(labels, mesh)
    total = float(mesh.areas.sum()) or 1.0
    first = np.unique(comp, return_index=True)[1]
    comp_label = labels[first]
    out = []
    for k in range(count):
        area = float(mesh.areas[labels == k].sum())
        mine = comp_area[comp_label == k]
        out.append({"index": k, "area_mm2": round(area, 1), "share": round(area / total, 4), "patches": int(len(mine)),
                    "smallest_patch_mm2": round(float(mine.min()), 3) if len(mine) else 0.0})
    return out
