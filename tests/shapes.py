"""Small figures built by code for the tests: a three-ball figure (a body with inward folds at the necks) as one closed
surface, and the same figure as a textured model."""

from __future__ import annotations

import numpy as np
from types import SimpleNamespace

from nikias_hoard.engine.mesh import Mesh, from_triangles


def profile(z: np.ndarray) -> np.ndarray:
    """Radius of the figure at height z (0..60 mm): three balls of radius 12, 9 and 6 that overlap."""
    balls = [(12.0, 12.0), (30.0, 9.0), (43.0, 6.0)]
    r = np.zeros_like(z)
    for c, rad in balls:
        r = np.maximum(r, np.sqrt(np.maximum(rad * rad - (z - c) ** 2, 0.0)))
    return r


def lathe(rows: int = 160, cols: int = 96) -> tuple[np.ndarray, np.ndarray]:
    z = np.linspace(0.0, 49.0, rows)
    r = np.maximum(profile(z), 1e-3)
    a = np.linspace(0, 2 * np.pi, cols, endpoint=False)
    P = np.stack([np.outer(r, np.cos(a)), np.outer(r, np.sin(a)), np.repeat(z[:, None], cols, 1)], -1).reshape(-1, 3)
    tris = []
    for i in range(rows - 1):
        for j in range(cols):
            p, q = i * cols + j, i * cols + (j + 1) % cols
            tris.append((p, q, p + cols))
            tris.append((q, q + cols, p + cols))
    bottom, top = len(P), len(P) + 1
    P = np.vstack([P, [[0, 0, 0.0]], [[0, 0, 49.0]]])
    for j in range(cols):
        tris.append((bottom, (j + 1) % cols, j))
        tris.append((top, (rows - 1) * cols + j, (rows - 1) * cols + (j + 1) % cols))
    return P, np.asarray(tris, np.int32)


def figure(rows: int = 160, cols: int = 96) -> Mesh:
    P, F = lathe(rows, cols)
    return from_triangles(P[F], "figure")


def ball_of(mesh: Mesh) -> np.ndarray:
    """Which ball each face belongs to (0 body, 1 chest, 2 head): the truth the colouring should find."""
    z = mesh.centroids[:, 2]
    return np.where(z < 23.6, 0, np.where(z < 38.6, 1, 2))


COLORS = np.array([[200, 30, 35], [245, 215, 40], [35, 90, 200]], np.uint8)


def textured(rows: int = 60, cols: int = 40, turn=None, scale: float = 1.0, shift=(0, 0, 0)):
    """The figure as a textured model: one plain material per ball, optionally turned, scaled and moved."""
    P, F = lathe(rows, cols)
    z = P[F].mean(1)[:, 2]
    M = np.where(z < 23.6, 0, np.where(z < 38.6, 1, 2)).astype(np.int16)
    if turn is not None:
        P = P @ np.asarray(turn, float).T
    P = P * scale + np.asarray(shift, float)
    mats = [SimpleNamespace(name=f"m{k}", albedo=None, color=tuple(int(x) for x in COLORS[k])) for k in range(3)]
    return SimpleNamespace(P=P, F=F, UV=np.zeros((len(F), 3, 2), np.float32), M=M, materials=mats, name="model")


def engraved_plate(n: int = 240, gap_deg: float = 3.0) -> Mesh:
    """A square plate with a circle engraved on it whose line is left open over ``gap_deg`` degrees, as hand-drawn outlines are."""
    x = np.linspace(-20.0, 20.0, n)
    X, Y = np.meshgrid(x, x, indexing="ij")
    r, a = np.hypot(X, Y), np.degrees(np.arctan2(Y, X))
    groove = np.exp(-((r - 10.0) / 0.35) ** 2) * (np.abs(a) > gap_deg / 2)
    P = np.stack([X, Y, -0.6 * groove], -1).reshape(-1, 3)
    i, j = np.meshgrid(np.arange(n - 1), np.arange(n - 1), indexing="ij")
    p = (i * n + j).ravel()
    F = np.concatenate([np.stack([p, p + n, p + 1], 1), np.stack([p + 1, p + n, p + n + 1], 1)]).astype(np.int32)
    return Mesh(P, F)

