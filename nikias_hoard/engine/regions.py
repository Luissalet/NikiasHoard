"""Cutting a figure into the regions a painter would fill one by one.

A fused print figure has no materials, but where two parts meet (a sleeve against an arm, an ear against the head, an
engraved line) the surface folds inwards. Normals are averaged over a small distance, faces where they turn inwards
faster than a threshold are the folds, what the folds separate are the regions, and each fold face joins the region it
touches. ``detail`` 1-5 goes from big parts to every engraved line."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse import csgraph

from .mesh import Mesh

# detail -> (distance over which normals are averaged, as a fraction of the diagonal; inward turn in radians that makes a
# fold; smallest region kept, as a share of the surface)
DETAIL = {1: (0.0080, 0.55, 0.0100), 2: (0.0055, 0.42, 0.0040), 3: (0.0040, 0.32, 0.0015), 4: (0.0028, 0.24, 0.0006),
          5: (0.0020, 0.18, 0.0002)}


def smoothed_normals(mesh: Mesh, distance: float) -> np.ndarray:
    """Face normals averaged with their surroundings up to about ``distance`` millimetres."""
    edge = max(mesh.mean_edge, 1e-9)
    rounds = int(np.clip(round((distance / edge) ** 2 / 0.75), 1, 80))
    N = mesh.neighbours
    ok = N >= 0
    safe = np.where(ok, N, 0)
    n = mesh.normals * mesh.areas[:, None]
    scale = float(np.abs(n).max()) or 1.0
    n = n / scale
    for _ in range(rounds):
        acc = n.copy()
        for k in range(3):
            acc += n[safe[:, k]] * ok[:, k, None]
        n = acc / (1.0 + ok.sum(1, keepdims=True))
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)


def fold_strength(mesh: Mesh, distance: float) -> np.ndarray:
    """Per face, how fast the surface turns inwards across it: radians over ``distance`` (0 where flat or convex)."""
    n = smoothed_normals(mesh, distance)
    i, j, _ = mesh.pairs
    d = mesh.centroids[j] - mesh.centroids[i]
    dist2 = np.maximum((d * d).sum(1), 1e-18)
    kappa = ((n[j] - n[i]) * d).sum(1) / dist2            # < 0: the two sides lean towards each other (concave)
    turn = np.maximum(-kappa, 0.0) * distance
    out = np.zeros(len(mesh.F))
    np.maximum.at(out, i, turn)
    np.maximum.at(out, j, turn)
    return out


def _components(mesh: Mesh, keep_pair: np.ndarray) -> np.ndarray:
    i, j, _ = mesh.pairs
    m = len(mesh.F)
    g = sparse.coo_matrix((np.ones(int(keep_pair.sum()), np.int8), (i[keep_pair], j[keep_pair])), shape=(m, m))
    return csgraph.connected_components(g, directed=False)[1].astype(np.int64)


def _spread(labels: np.ndarray, mesh: Mesh) -> np.ndarray:
    """Faces without a label (-1) take the label of a neighbour, ring by ring."""
    N = mesh.neighbours
    for _ in range(4000):
        todo = np.flatnonzero(labels < 0)
        if not len(todo):
            break
        nb = N[todo]
        lab = np.where(nb >= 0, labels[np.where(nb >= 0, nb, 0)], -1)
        best = lab.max(1)
        got = best >= 0
        if not got.any():
            break
        labels[todo[got]] = best[got]
    left = labels < 0
    if left.any():                                         # folds that touch no region: islands of their own
        i, j, _ = mesh.pairs
        both = left[i] & left[j]
        comp = _components(mesh, both)
        labels[left] = labels.max() + 1 + np.unique(comp[left], return_inverse=True)[1]
    return labels


def merge_small(labels: np.ndarray, mesh: Mesh, min_area: float, max_regions: int = 0) -> np.ndarray:
    """Regions smaller than ``min_area`` join the neighbour they share the longest border with."""
    i, j, length = mesh.pairs
    labels = np.unique(labels, return_inverse=True)[1].astype(np.int64)
    for _ in range(60):
        R = int(labels.max()) + 1
        area = np.bincount(labels, mesh.areas, R)
        limit = min_area
        if max_regions and R > max_regions:
            limit = max(limit, float(np.sort(area)[R - max_regions]) * 1.0000001)
        small = area < limit
        if R <= 1 or not small.any():
            break
        a, b = labels[i], labels[j]
        d = a != b
        if not d.any():
            break
        border = sparse.coo_matrix((length[d], (a[d], b[d])), shape=(R, R))
        border = (border + border.T).tocoo()
        order = np.lexsort((border.data, border.row))
        row, col = border.row[order], border.col[order]
        last = np.r_[row[1:] != row[:-1], True]              # per region: the neighbour with the longest border
        best = np.arange(R)
        best[row[last]] = col[last]
        target = np.where(small, best, np.arange(R))
        mutual = (target[target] == np.arange(R)) & (target != np.arange(R))
        keep_root = mutual & (np.arange(R) < target)          # two small regions pointing at each other: one stays
        target[keep_root] = np.flatnonzero(keep_root)
        if (target == np.arange(R)).all():
            break
        for _ in range(32):
            nxt = target[target]
            if (nxt == target).all():
                break
            target = nxt
        labels = np.unique(target[labels], return_inverse=True)[1].astype(np.int64)
    return labels


def segment(mesh: Mesh, detail: int = 3, max_regions: int = 60) -> np.ndarray:
    """A region index per face, regions numbered from the largest."""
    scale, turn, share = DETAIL[int(np.clip(detail, 1, 5))]
    distance = max(mesh.diagonal * scale, mesh.mean_edge * 1.5)
    fold = fold_strength(mesh, distance) > turn
    i, j, _ = mesh.pairs
    labels = _components(mesh, ~fold[i] & ~fold[j])
    labels[fold] = -1
    labels = np.where(labels >= 0, np.unique(labels, return_inverse=True)[1], -1) if (labels >= 0).any() else labels
    labels = _spread(labels.astype(np.int64), mesh)
    labels = merge_small(labels, mesh, float(mesh.areas.sum()) * share, max_regions)
    area = np.bincount(labels, mesh.areas)
    rank = np.empty(len(area), np.int64)
    rank[np.argsort(-area, kind="stable")] = np.arange(len(area))
    return rank[labels].astype(np.int32)


def describe(mesh: Mesh, regions: np.ndarray) -> list[dict[str, Any]]:
    R = int(regions.max()) + 1 if len(regions) else 0
    area = np.bincount(regions, mesh.areas, R)
    total = float(area.sum()) or 1.0
    w = mesh.areas
    c = mesh.centroids
    cen = np.stack([np.bincount(regions, w * c[:, k], R) for k in range(3)], 1) / np.maximum(area, 1e-30)[:, None]
    lo, ext = mesh.P.min(0), np.maximum(mesh.extents, 1e-9)
    out = []
    for r in range(R):
        rel = (cen[r] - lo) / ext
        out.append({"id": r, "share": round(float(area[r] / total), 4), "area_mm2": round(float(area[r]), 1),
                    "center_mm": [round(float(x), 2) for x in cen[r]], "height": round(float(rel[2]), 2)})
    return out
