"""A print figure as one welded triangle mesh, with what colouring needs: areas, normals and which faces touch.

``P`` (n, 3) float64 in millimetres, ``F`` (m, 3) int32. ``neighbours`` is (m, 3): the face across each edge, -1 where
the surface is open."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


def weld(points: np.ndarray, tol: float) -> tuple[np.ndarray, np.ndarray]:
    """Unique positions within ``tol`` and the index of each input point in them."""
    if len(points) == 0:
        return points.reshape(0, 3).astype(np.float64), np.zeros(0, np.int64)
    q = np.round(points / tol).astype(np.int64)
    q -= q.min(0)
    span = q.max(0) + 1
    if float(span[0]) * float(span[1]) * float(span[2]) < 9e18:           # one integer per point sorts much faster than rows
        key = (q[:, 0] * span[1] + q[:, 1]) * span[2] + q[:, 2]
        _, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    else:
        _, first, inverse = np.unique(q, axis=0, return_index=True, return_inverse=True)
    return points[first].astype(np.float64), inverse.reshape(-1)


@dataclass
class Mesh:
    P: np.ndarray
    F: np.ndarray
    name: str = ""
    _cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ basics
    @property
    def extents(self) -> np.ndarray:
        return self.P.max(0) - self.P.min(0) if len(self.P) else np.zeros(3)

    @property
    def diagonal(self) -> float:
        return float(np.linalg.norm(self.extents))

    def _corners(self):
        return self.P[self.F[:, 0]], self.P[self.F[:, 1]], self.P[self.F[:, 2]]

    @property
    def cross(self) -> np.ndarray:
        if "cross" not in self._cache:
            a, b, c = self._corners()
            self._cache["cross"] = np.cross(b - a, c - a)
        return self._cache["cross"]

    @property
    def areas(self) -> np.ndarray:
        if "areas" not in self._cache:
            self._cache["areas"] = 0.5 * np.linalg.norm(self.cross, axis=1)
        return self._cache["areas"]

    @property
    def normals(self) -> np.ndarray:
        if "normals" not in self._cache:
            n = self.cross
            self._cache["normals"] = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
        return self._cache["normals"]

    @property
    def centroids(self) -> np.ndarray:
        if "centroids" not in self._cache:
            a, b, c = self._corners()
            self._cache["centroids"] = (a + b + c) / 3.0
        return self._cache["centroids"]

    @property
    def mean_edge(self) -> float:
        if "mean_edge" not in self._cache:
            a, b, c = self._corners()
            n = min(len(self.F), 200_000)
            sel = np.linspace(0, len(self.F) - 1, n).astype(np.int64) if n else np.zeros(0, np.int64)
            e = (np.linalg.norm(b[sel] - a[sel], axis=1) + np.linalg.norm(c[sel] - b[sel], axis=1) + np.linalg.norm(a[sel] - c[sel], axis=1)) / 3
            self._cache["mean_edge"] = float(e.mean()) if n else 0.0
        return self._cache["mean_edge"]

    # ------------------------------------------------------------------ which faces touch
    @property
    def pairs(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Faces sharing an edge: ``(i, j, length)`` with i < j once per shared edge."""
        if "pairs" not in self._cache:
            F = self.F.astype(np.int64)
            m = len(F)
            n = int(F.max()) + 1 if m else 1
            lo = np.minimum(F, F[:, [1, 2, 0]]).reshape(-1)
            hi = np.maximum(F, F[:, [1, 2, 0]]).reshape(-1)
            key = lo * n + hi
            face = np.repeat(np.arange(m), 3)
            order = np.argsort(key, kind="stable")
            key_s, face_s = key[order], face[order]
            same = key_s[1:] == key_s[:-1]
            # in a run of 3+ faces on one edge (non-manifold) consecutive ones are paired
            i, j = face_s[:-1][same], face_s[1:][same]
            k = key_s[:-1][same]
            keep = i != j
            i, j, k = i[keep], j[keep], k[keep]
            length = np.linalg.norm(self.P[k // n] - self.P[k % n], axis=1)
            a, b = np.minimum(i, j), np.maximum(i, j)
            self._cache["pairs"] = (a, b, length)
        return self._cache["pairs"]

    @property
    def neighbours(self) -> np.ndarray:
        if "neighbours" not in self._cache:
            i, j, _ = self.pairs
            m = len(self.F)
            src = np.concatenate([i, j])
            dst = np.concatenate([j, i])
            order = np.argsort(src, kind="stable")
            src, dst = src[order], dst[order]
            start = np.searchsorted(src, np.arange(m))
            slot = np.arange(len(src)) - start[src]
            N = np.full((m, 3), -1, np.int64)
            ok = slot < 3
            N[src[ok], slot[ok]] = dst[ok]
            self._cache["neighbours"] = N
        return self._cache["neighbours"]

    def stats(self) -> dict:
        i, _, _ = self.pairs
        open_edges = int(3 * len(self.F) - 2 * len(i))
        ext = self.extents
        return {"faces": int(len(self.F)), "points": int(len(self.P)), "size_mm": [round(float(x), 2) for x in ext],
                "area_mm2": round(float(self.areas.sum()), 1), "mean_edge_mm": round(self.mean_edge, 4), "open_edges": max(open_edges, 0)}


# ---------------------------------------------------------------------------------------------------------------- STL
def _is_binary_stl(path: Path, size: int) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(84)
    if len(head) < 84:
        return False
    count = struct.unpack("<I", head[80:84])[0]
    return 84 + 50 * count == size or not head[:5].lower().startswith(b"solid")


def load_stl(path: Path, weld_tol: Optional[float] = None) -> Mesh:
    """Binary or ASCII STL. Corners are welded so faces know their neighbours; needle-thin and repeated faces go."""
    path = Path(path)
    size = path.stat().st_size
    if _is_binary_stl(path, size):
        count = (size - 84) // 50
        raw = np.fromfile(path, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=count, offset=84)
        tri = raw["v"].astype(np.float64)
    else:
        pts = []
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                s = line.split()
                if len(s) == 4 and s[0] == "vertex":
                    pts.append((float(s[1]), float(s[2]), float(s[3])))
        tri = np.asarray(pts, np.float64).reshape(-1, 3, 3)
    if len(tri) == 0:
        raise ValueError("the STL has no triangles")
    return from_triangles(tri, path.stem, weld_tol)


def from_triangles(tri: np.ndarray, name: str = "", weld_tol: Optional[float] = None) -> Mesh:
    tri = np.asarray(tri, np.float64).reshape(-1, 3, 3)
    tri = tri[np.isfinite(tri).all(axis=(1, 2))]
    flat = tri.reshape(-1, 3)
    ext = float((flat.max(0) - flat.min(0)).max()) if len(flat) else 1.0
    P, inv = weld(flat, weld_tol or max(ext, 1e-9) * 2e-6)
    F = inv.reshape(-1, 3).astype(np.int32)
    keep = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])
    F = F[keep]
    used = np.zeros(len(P), bool)
    used[F.reshape(-1)] = True
    if not used.all():
        remap = np.cumsum(used) - 1
        P, F = P[used], remap[F].astype(np.int32)
    return Mesh(P, F, name)


def save_npz(mesh: Mesh, path: Path) -> None:
    np.savez(path, P=mesh.P.astype(np.float32), F=mesh.F)


def load_npz(path: Path, name: str = "") -> Mesh:
    with np.load(path) as z:
        return Mesh(z["P"].astype(np.float64), z["F"].astype(np.int32), name)


def write_stl(path: Path, P: np.ndarray, F: np.ndarray) -> None:
    tri = P[F].astype("<f4")
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
    rec = np.zeros(len(F), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as fh:
        fh.write(b"Nikias's Hoard".ljust(80, b" "))
        fh.write(struct.pack("<I", len(F)))
        rec.tofile(fh)
