"""Colour from a textured model of the same figure.

The model (a game rip, the .blend the figure was posed in) is sampled into coloured points, laid over the print figure
(any units, any of the 24 upright turns, then refined point to point) and each face of the figure takes the colour of
the nearest sample. ``fit`` says how well the two shapes agree: when the model is in another pose it is poor and the
colours are not to be trusted."""

from __future__ import annotations

import itertools
from typing import Any, Optional

import numpy as np
from scipy.spatial import cKDTree

from .mesh import Mesh


def sample_scene(scene: Any, count: int = 600_000, seed: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Points spread evenly over a textured scene with the colour the texture has there: ``(points, rgb uint8)``.
    Faces whose texture is see-through at the sample (alpha cards) are left out."""
    P, F = scene.P, scene.F
    a, b, c = P[F[:, 0]], P[F[:, 1]], P[F[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = float(area.sum())
    if total <= 0:
        raise ValueError("the model has no surface")
    rng = np.random.default_rng(seed)
    n = np.maximum(rng.poisson(area / total * count), 0)
    n[(n == 0) & (area > 0)] = 1                           # every face gets at least one sample
    owner = np.repeat(np.arange(len(F)), n)
    r1, r2 = np.sqrt(rng.random(len(owner))), rng.random(len(owner))
    w = np.stack([1 - r1, r1 * (1 - r2), r1 * r2], 1)
    pts = a[owner] * w[:, :1] + b[owner] * w[:, 1:2] + c[owner] * w[:, 2:3]
    uv = (scene.UV[owner] * w[:, :, None]).sum(1)
    rgb = np.full((len(owner), 3), 200, np.uint8)
    alive = np.ones(len(owner), bool)
    mats = scene.M[owner]
    for mi, mat in enumerate(scene.materials):
        sel = np.flatnonzero(mats == mi)
        if not len(sel):
            continue
        tex = mat.albedo
        if tex is None:
            rgb[sel] = np.asarray(mat.color, np.uint8)
            continue
        h, wd = tex.shape[:2]
        u = np.mod(uv[sel, 0], 1.0)
        v = np.mod(uv[sel, 1], 1.0)
        x = np.clip((u * wd).astype(np.int64), 0, wd - 1)
        y = np.clip(((1.0 - v) * h).astype(np.int64), 0, h - 1)
        px = tex[y, x]
        rgb[sel] = px[:, :3]
        if tex.shape[2] == 4 and float((tex[..., 3] < 128).mean()) > 0.02:
            alive[sel] = px[:, 3] >= 128
    return pts[alive], rgb[alive]


def _rotations() -> list[np.ndarray]:
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            R = np.zeros((3, 3))
            for r, (p, s) in enumerate(zip(perm, signs)):
                R[r, p] = s
            if np.linalg.det(R) > 0:
                out.append(R)
    return out


def _similarity(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Scale, rotation and shift that best take ``src`` onto ``dst`` (matched rows)."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    var = (a * a).sum() / len(src)
    s = float(np.trace(np.diag(S) @ D) / max(var, 1e-30))
    return s, R, md - s * (R @ ms)


def _icp(src: np.ndarray, tree: cKDTree, dst: np.ndarray, s: float, R: np.ndarray, t: np.ndarray, rounds: int, keep: float = 0.85):
    err = np.inf
    for _ in range(rounds):
        moved = s * (src @ R.T) + t
        d, idx = tree.query(moved)
        cut = np.quantile(d, keep)
        ok = d <= cut
        s2, R2, t2 = _similarity(moved[ok], dst[idx[ok]])
        s, R, t = s2 * s, R2 @ R, s2 * (R2 @ t) + t2
        new = float(d[ok].mean())
        if abs(err - new) < 1e-7 * max(new, 1e-9):
            err = new
            break
        err = new
    return s, R, t, err


def register(src_points: np.ndarray, mesh: Mesh, seed: int = 11) -> dict[str, Any]:
    """Where the model's points go to lie on the figure. ``matrix`` 4x4, ``fit`` = typical gap as a share of the
    figure's diagonal (under 0.01 the shapes agree; over 0.03 it is another pose or another figure)."""
    rng = np.random.default_rng(seed)
    dst_all = mesh.centroids
    dst = dst_all[rng.choice(len(dst_all), min(len(dst_all), 20_000), replace=False)]
    src = src_points[rng.choice(len(src_points), min(len(src_points), 20_000), replace=False)]
    cs, cd = src.mean(0), dst.mean(0)
    rs = float(np.sqrt(((src - cs) ** 2).sum(1).mean()))
    rd = float(np.sqrt(((dst - cd) ** 2).sum(1).mean()))
    tree = cKDTree(dst)
    coarse_src, coarse_dst = src[:3000], dst[:3000]
    coarse_tree = cKDTree(coarse_dst)
    src_tree_pts = None
    best = None
    for R0 in _rotations():
        s0 = rd / max(rs, 1e-30)
        t0 = cd - s0 * (R0 @ cs)
        s, R, t, _ = _icp(coarse_src, coarse_tree, coarse_dst, s0, R0, t0, 12)
        moved = s * (coarse_src @ R.T) + t
        fwd = coarse_tree.query(moved)[0]
        back = cKDTree(moved).query(coarse_dst)[0]
        score = float(np.quantile(fwd, 0.8) + np.quantile(back, 0.8))
        if best is None or score < best[0]:
            best = (score, s, R, t)
    _, s, R, t = best
    s, R, t, _ = _icp(src, tree, dst, s, R, t, 40)
    moved = s * (src @ R.T) + t
    gap = cKDTree(moved).query(dst)[0]                      # how far the figure's surface is from the model
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = s * R, t
    diag = max(mesh.diagonal, 1e-9)
    return {"matrix": M.tolist(), "scale": round(s, 6), "fit": round(float(np.median(gap)) / diag, 5),
            "fit_p90": round(float(np.quantile(gap, 0.9)) / diag, 5)}


def apply(matrix, points: np.ndarray) -> np.ndarray:
    M = np.asarray(matrix, np.float64)
    return points @ M[:3, :3].T + M[:3, 3]


def transfer(mesh: Mesh, points: np.ndarray, rgb: np.ndarray, neighbours: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Per face of the figure: the colour of the nearest samples (the commonest among ``neighbours``) and the distance
    to the nearest one in millimetres."""
    tree = cKDTree(points)
    c = mesh.centroids
    m = len(c)
    out = np.empty((m, 3), np.uint8)
    gap = np.empty(m, np.float32)
    k = max(int(neighbours), 1)
    for s in range(0, m, 250_000):
        d, idx = tree.query(c[s:s + 250_000], k=k, workers=-1)
        if k == 1:
            out[s:s + 250_000] = rgb[idx]
            gap[s:s + 250_000] = d
            continue
        cols = rgb[idx].astype(np.int64)                    # (n, k, 3)
        key = (cols[..., 0] >> 3 << 10) | (cols[..., 1] >> 3 << 5) | (cols[..., 2] >> 3)
        votes = (key[:, :, None] == key[:, None, :]).sum(2)  # how many of the k share each one's colour bin
        pick = votes.argmax(1)
        out[s:s + 250_000] = rgb[idx[np.arange(len(idx)), pick]]
        gap[s:s + 250_000] = d[:, 0]
    return out, gap
