"""Reducing many colours to the few a printer loads.

Colours are compared in CIELAB (distances there match what the eye sees). ``reduce`` is a weighted k-means whose
weights are dampened so a small but distinct colour (eyes, a logo) is not swallowed by the big ones."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

# name, sRGB: what a colour is called in the interface and in the file names of an export
NAMES = [("blanco", (245, 245, 245)), ("negro", (20, 20, 20)), ("gris", (128, 128, 128)), ("gris claro", (190, 190, 190)),
         ("gris oscuro", (70, 70, 70)), ("rojo", (200, 30, 35)), ("granate", (120, 20, 35)), ("naranja", (240, 130, 30)),
         ("amarillo", (245, 215, 40)), ("crema", (240, 225, 180)), ("beige", (215, 190, 150)), ("marrón", (115, 75, 45)),
         ("marrón claro", (170, 120, 75)), ("verde", (50, 150, 60)), ("verde claro", (140, 205, 110)), ("verde oscuro", (25, 85, 45)),
         ("turquesa", (40, 175, 170)), ("azul claro", (120, 185, 235)), ("azul", (35, 90, 200)), ("azul oscuro", (25, 40, 110)),
         ("morado", (115, 55, 160)), ("lila", (185, 150, 220)), ("rosa", (240, 140, 180)), ("magenta", (205, 40, 130)),
         ("carne", (240, 195, 165)), ("dorado", (205, 165, 60))]


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, np.float64).reshape(-1, 3) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]]).T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    lab = np.asarray(lab, np.float64).reshape(-1, 3)
    fy = (lab[:, 0] + 16) / 116
    fx, fz = fy + lab[:, 1] / 500, fy - lab[:, 2] / 200
    f = np.stack([fx, fy, fz], 1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) / (24389 / 27)) * np.array([0.95047, 1.0, 1.08883])
    c = xyz @ np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]]).T
    c = np.clip(c, 0, 1)
    c = np.where(c > 0.0031308, 1.055 * c ** (1 / 2.4) - 0.055, 12.92 * c)
    return np.clip(np.round(c * 255), 0, 255).astype(np.uint8)


def to_hex(rgb) -> str:
    r, g, b = (int(x) for x in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def from_hex(text: str) -> tuple[int, int, int]:
    s = str(text).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"not a colour: {text!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


_NAME_LAB = rgb_to_lab(np.array([c for _, c in NAMES]))


def name_of(rgb) -> str:
    d = ((rgb_to_lab(np.array([rgb])) - _NAME_LAB) ** 2).sum(1)
    return NAMES[int(d.argmin())][0]


def distance(a, b) -> float:
    """How different two colours look (CIE76 ΔE: under 2 nobody tells them apart, over 20 clearly different)."""
    return float(np.linalg.norm(rgb_to_lab(np.array([a])) - rgb_to_lab(np.array([b]))))


def _bins(rgb: np.ndarray, weight: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Colours gathered into 32-step bins: their mean Lab and total weight."""
    q = (np.asarray(rgb, np.int64) >> 3)
    key = (q[:, 0] << 10) | (q[:, 1] << 5) | q[:, 2]
    uniq, inv = np.unique(key, return_inverse=True)
    w = np.bincount(inv, weight, len(uniq))
    lab = rgb_to_lab(rgb)
    mean = np.stack([np.bincount(inv, weight * lab[:, k], len(uniq)) for k in range(3)], 1) / np.maximum(w, 1e-30)[:, None]
    return mean, w


def reduce(rgb: np.ndarray, weight: Optional[np.ndarray] = None, k: int = 4, accent: float = 0.6, seed: int = 7) -> np.ndarray:
    """``k`` representative colours (k, 3) uint8 of the weighted set, biggest first. ``accent`` 1 = by area alone,
    lower gives small distinct colours more say."""
    rgb = np.asarray(rgb).reshape(-1, 3)
    if len(rgb) == 0:
        return np.zeros((0, 3), np.uint8)
    weight = np.ones(len(rgb)) if weight is None else np.asarray(weight, np.float64)
    lab, w_area = _bins(rgb, weight)
    k = int(min(max(k, 1), len(lab)))
    w = np.maximum(w_area, 0) ** float(np.clip(accent, 0.2, 1.0))
    rng = np.random.default_rng(seed)
    centers = [lab[int(np.argmax(w))]]
    for _ in range(1, k):                                   # farthest-point start: stable and spreads over the hues
        d = np.min([((lab - c) ** 2).sum(1) for c in centers], axis=0)
        centers.append(lab[int(np.argmax(d * w))])
    C = np.array(centers)
    for _ in range(60):
        d = ((lab[:, None, :] - C[None]) ** 2).sum(2)
        a = d.argmin(1)
        new = C.copy()
        for c in range(k):
            sel = a == c
            if sel.any():
                new[c] = (lab[sel] * w[sel, None]).sum(0) / w[sel].sum()
            else:
                new[c] = lab[int(rng.integers(len(lab)))]
        if np.abs(new - C).max() < 0.05:
            C = new
            break
        C = new
    a = ((lab[:, None, :] - C[None]) ** 2).sum(2).argmin(1)
    area = np.bincount(a, w_area, k)
    order = np.argsort(-area)
    return lab_to_rgb(C[order])


def assign(rgb: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """Index of the palette colour closest to each colour (uint8)."""
    rgb = np.asarray(rgb).reshape(-1, 3)
    pal = rgb_to_lab(np.asarray(palette).reshape(-1, 3))
    out = np.empty(len(rgb), np.uint8)
    for s in range(0, len(rgb), 400_000):
        lab = rgb_to_lab(rgb[s:s + 400_000])
        out[s:s + 400_000] = ((lab[:, None, :] - pal[None]) ** 2).sum(2).argmin(1)
    return out


def error(rgb: np.ndarray, weight: np.ndarray, palette: np.ndarray) -> float:
    """Mean ΔE between the colours and the palette colour that stands for them."""
    lab, w = _bins(np.asarray(rgb).reshape(-1, 3), np.asarray(weight, np.float64))
    pal = rgb_to_lab(palette)
    d = np.sqrt(((lab[:, None, :] - pal[None]) ** 2).sum(2).min(1))
    return float((d * w).sum() / max(w.sum(), 1e-30))


def suggest_count(rgb: np.ndarray, weight: np.ndarray, lo: int = 2, hi: int = 8, accent: float = 0.6, far: float = 22.0,
                  share: float = 0.01) -> dict[str, Any]:
    """How many colours the figure asks for: the fewest with which less than ``share`` of the surface is left with a
    colour clearly different (ΔE over ``far``) from the one that stands for it."""
    lab, w = _bins(np.asarray(rgb).reshape(-1, 3), np.asarray(weight, np.float64))
    total = max(float(w.sum()), 1e-30)
    errs, left, best = {}, {}, hi
    for k in range(lo, hi + 1):
        colors = reduce(rgb, weight, k, accent)
        d = np.sqrt(((lab[:, None, :] - rgb_to_lab(colors)[None]) ** 2).sum(2).min(1))
        errs[k] = round(float((d * w).sum() / total), 2)
        left[k] = round(float(w[d > far].sum() / total), 4)
        if left[k] < share:
            best = k
            break
    return {"count": best, "errors": errs, "left_out_share": left}

