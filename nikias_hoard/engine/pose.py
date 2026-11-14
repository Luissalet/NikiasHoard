"""Conservative, part-wise colour transfer for references in a different pose.

Only points and colours survive ``source.sample_scene``, so bones and materials
are unavailable here. Spatially connected colour patches propose rigid parts;
a spectral cut of the spatial graph also proposes parts when textures cross
their boundaries. Geometry, relative size and a bidirectional local registration
decide the matches, never colour similarity to the destination.

``transfer`` returns a dict keyed by the original region ids. Each value has
``rgb`` (dominant RGB list or None), ``confidence``, ``part`` (or None), and a
``reason``. A part identifies its sampled component, normalized source centre
and share of source samples. Confidence measures geometric evidence, not a
calibrated probability. Repeated indistinguishable parts are left uncoloured.
This cannot recover semantic identities, bones, or small printed patterns.
All geometric distances and thresholds are relative to shape size.
"""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.cluster.vq import kmeans2
from scipy.optimize import linear_sum_assignment
from scipy.sparse import csgraph
from scipy.sparse.linalg import ArpackNoConvergence, eigsh
from scipy.spatial import cKDTree

from . import source
from .mesh import Mesh


def _normalize(points):
    lo, hi = points.min(0), points.max(0)
    diagonal = float(np.linalg.norm(hi - lo))
    if diagonal <= 0:
        return None
    return (points - (lo + hi) / 2) / diagonal


def _sample(points, limit=5000):
    # One representative per relative voxel avoids dense duplicate UV seams.
    _, idx = np.unique(np.floor(points * 100).astype(np.int64), axis=0, return_index=True)
    if len(idx) > limit:
        idx = idx[np.random.default_rng(17).choice(len(idx), limit, replace=False)]
    return idx


def _partitions(points, rgb, count):
    n = len(points)
    tree = cKDTree(points)
    d, near = tree.query(points, k=min(13, n))
    spacing = float(np.median(d[:, min(6, d.shape[1] - 1)]))
    radius = min(max(2.5 * spacing, 0.012), 0.065)
    rows = np.repeat(np.arange(n), near.shape[1] - 1)
    cols = near[:, 1:].reshape(-1)
    gaps = d[:, 1:].reshape(-1)
    keep = gaps < radius
    rows, cols, gaps = rows[keep], cols[keep], gaps[keep]
    weights = np.exp(-(gaps / max(spacing, 1e-12)) ** 2)
    graph = sparse.coo_matrix((weights, (rows, cols)), shape=(n, n)).tocsr()
    graph = graph.maximum(graph.T)
    delta = np.linalg.norm(rgb[rows].astype(float) - rgb[cols], axis=1)
    colour_graph = sparse.coo_matrix((weights * (delta < 55), (rows, cols)), shape=(n, n)).tocsr()
    colour_graph.eliminate_zeros()
    _, labels = csgraph.connected_components(colour_graph, directed=False)
    yield "colour", labels
    components, labels = csgraph.connected_components(graph, directed=False)
    if components > 1:
        yield "spatial", labels
    # A geometric fallback when a material spans more than one rigid part.
    if 1 < count < min(n - 1, 65) and components == 1:
        # Estimate consistently oriented normals from local point neighbourhoods.
        # Inward folds weaken graph edges; a plain normalized cut otherwise
        # tends to split the biggest convex part merely to balance its areas.
        _, neighbours = tree.query(points, k=min(25, n))
        offsets = points[neighbours] - points[neighbours].mean(axis=1, keepdims=True)
        covariance = np.einsum("nki,nkj->nij", offsets, offsets)
        normals = np.linalg.eigh(covariance)[1][:, :, 0]
        dot = np.einsum("ij,ij->i", normals[rows], normals[cols])
        links = sparse.coo_matrix((1.001 - np.abs(dot), (rows, cols)), shape=(n, n)).tocsr()
        spanning = csgraph.minimum_spanning_tree(links.maximum(links.T))
        order, parent = csgraph.breadth_first_order(spanning, 0, directed=False)
        for child in order[1:]:
            if normals[child] @ normals[parent[child]] < 0:
                normals[child] *= -1
        if np.sum(normals * (points - points.mean(0))) < 0:
            normals *= -1
        direction = (points[cols] - points[rows]) / np.maximum(gaps[:, None], 1e-12)
        inward = np.maximum(-np.sum((normals[cols] - normals[rows]) * direction, axis=1), 0)
        folded = weights * np.exp(-(inward / 0.12) ** 2)
        graph = sparse.coo_matrix((folded, (rows, cols)), shape=(n, n)).tocsr()
        graph = graph.maximum(graph.T)
        lap = csgraph.laplacian(graph, normed=True)
        try:
            _, vectors = eigsh(lap, k=count, sigma=-1e-5, which="LM", v0=np.linspace(1, 2, n), tol=1e-4)
        except ArpackNoConvergence:
            return  # already yielded colour/spatial candidates; abstain on this fallback
        vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
        _, labels = kmeans2(vectors, count, minit="++", seed=17, iter=40)
        yield "geometry", labels


def _descriptor(points):
    center = points.mean(0)
    p = points - center
    radius = float(np.sqrt(np.mean(np.sum(p * p, axis=1))))
    eigen = np.linalg.eigvalsh(p.T @ p / len(p))
    signature = np.r_[np.sqrt(np.maximum(eigen, 0)) / max(radius, 1e-12),
                      np.quantile(np.linalg.norm(p, axis=1), [0.1, 0.5, 0.9]) / max(radius, 1e-12)]
    return radius, signature


def _dominant(rgb):
    bins = rgb.astype(np.int64) >> 4
    keys = (bins[:, 0] << 8) | (bins[:, 1] << 4) | bins[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    return np.median(rgb[keys == values[counts.argmax()]], axis=0).astype(int).tolist()


def transfer(points: np.ndarray, rgb: np.ndarray, mesh: Mesh, regions: np.ndarray) -> dict:
    """Match source parts to face regions, returning only reliable dominant colours.

    ``rgb`` uses 0..255 channels; ``regions`` is one nonnegative integer per
    mesh face (ids need not be contiguous). Inputs are never modified. Sparse,
    flat, unmatched and ambiguous parts return None rather than guessed paint.
    Local fitting uses ``source.register`` restricted to one destination region.
    """
    points, rgb, regions = np.asarray(points, float), np.asarray(rgb), np.asarray(regions)
    if points.ndim != 2 or points.shape[1] != 3 or rgb.shape != points.shape:
        raise ValueError("points and rgb must have shape (n, 3)")
    if not np.isfinite(points).all() or not np.isfinite(rgb).all() or ((rgb < 0) | (rgb > 255)).any():
        raise ValueError("points must be finite and rgb must be in 0..255")
    if regions.shape != (len(mesh.F),) or regions.dtype.kind not in "iu" or (regions < 0).any():
        raise ValueError("regions must contain one nonnegative integer per face")
    ids = np.unique(regions)
    out = {int(r): {"rgb": None, "confidence": 0.0, "part": None, "reason": "no reliable match"} for r in ids}
    if not len(ids) or len(points) < 30 or not len(mesh.P):
        return out
    normalized = _normalize(points)
    target = _normalize(mesh.P)
    if normalized is None or target is None:
        return out
    idx = _sample(normalized)
    cloud, colours = normalized[idx], rgb[idx].astype(np.uint8)
    if len(cloud) < 30:
        return out
    rng = np.random.default_rng(23)
    targets, target_samples, target_desc = [], [], []
    for r in ids:
        faces = mesh.F[regions == r]
        used, remap = np.unique(faces, return_inverse=True)
        local = Mesh(target[used], remap.reshape(-1, 3).astype(np.int32))
        weights = local.areas
        if weights.sum() <= 0:
            return out
        take = rng.choice(len(faces), min(2400, len(faces)), replace=True, p=weights / weights.sum())
        # Uniform area weighting prevents dense sculpted details biasing shape descriptors.
        samples = local.centroids[take]
        targets.append(local)
        target_samples.append(samples)
        target_desc.append(_descriptor(samples))
    empty = out
    best_score = -1.0
    for kind, labels in _partitions(cloud, colours, len(ids)):
        groups = [np.flatnonzero(labels == k) for k in np.unique(labels)]
        groups = [g for g in groups if len(g) >= max(30, len(cloud) * 0.004)]
        if not groups or len(groups) > 128:
            continue
        full_labels = labels[cKDTree(cloud).query(normalized)[1]]
        source_groups = [np.flatnonzero(full_labels == labels[g[0]]) for g in groups]
        # Fit actual samples, not the sparse graph's voxel representatives.
        parts = [normalized[g[rng.choice(len(g), min(len(g), 6000), replace=False)]] for g in source_groups]
        desc = [_descriptor(p) for p in parts]
        # A common scale preserves the relative sizes that distinguish similar limbs.
        if len(parts) == len(ids):
            scale = float(np.median(np.sort([d[0] for d in target_desc]) / np.sort([d[0] for d in desc])))
        else:
            scale = 1.0  # both full shapes have unit diagonal
        quality = np.zeros((len(ids), len(parts)))
        for i, (tr, ts) in enumerate(target_desc):
            for j, (sr, ss) in enumerate(desc):
                size_error = abs(np.log(max(sr * scale, 1e-12) / max(tr, 1e-12)))
                shape_error = float(np.linalg.norm(ts - ss))
                if size_error > 0.28 or shape_error > 0.32 or min(ss[:3]) < 0.08:
                    continue
                fit = source.register(parts[j], targets[i])
                moved = source.apply(fit["matrix"], parts[j])
                fitted_scale = np.cbrt(np.linalg.det(np.asarray(fit["matrix"])[:3, :3]))
                if fitted_scale <= 0 or abs(np.log(fitted_scale / scale)) > 0.28:
                    continue
                # A small patch can land on any surface: demand coverage in both directions.
                forward = cKDTree(target_samples[i]).query(moved)[0]
                backward = cKDTree(moved).query(target_samples[i])[0]
                gap = max(float(np.quantile(forward, 0.9)), float(np.quantile(backward, 0.9))) / tr
                quality[i, j] = np.exp(-((gap / 0.27) ** 2 + (size_error / 0.35) ** 2 + (shape_error / 0.4) ** 2))
        # Dummy columns allow every region to abstain independently.
        row, col = linear_sum_assignment(-np.c_[quality, np.full((len(ids), len(ids)), 0.6)])
        result = {r: dict(value) for r, value in empty.items()}
        total = 0.0
        for i, j in zip(row, col):
            if j >= len(parts):
                continue
            confidence = float(quality[i, j])
            alternatives = np.delete(quality[i], j)
            other_regions = np.delete(quality[:, j], i)
            if ((len(alternatives) and confidence - alternatives.max() < 0.12)
                    or (len(other_regions) and confidence - other_regions.max() < 0.12)):
                result[int(ids[i])]["reason"] = "ambiguous source parts"
                continue
            if confidence < 0.6:
                continue
            label = int(labels[groups[j][0]])
            selected = rgb[source_groups[j]].astype(np.uint8)
            result[int(ids[i])] = {
                "rgb": _dominant(selected), "confidence": round(confidence, 4),
                "part": {"id": f"{kind}:{label}", "center": parts[j].mean(0).tolist(),
                         "share": round(float(len(selected) / len(points)), 5)},
                "reason": "matched",
            }
            total += confidence
        if total > best_score:
            best_score, out = total, result
    return out
