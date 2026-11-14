"""Pictures of a figure without a GPU. The surface is sampled into an image that remembers which face is in front
at each pixel; shading comes from the depth itself, so engraved lines and folds read as they will print, and each face
shows its colour: a palette colour, its region, or plain clay."""

from __future__ import annotations

import io
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

VIEWS = {"front": (0, 10), "back": (180, 10), "left": (90, 10), "right": (-90, 10), "top": (0, 89), "iso": (35, 25),
         "iso_back": (215, 25), "front_left": (45, 12), "front_right": (-45, 12), "back_left": (135, 12), "back_right": (-135, 12)}
BACKGROUND = (24, 22, 28)
CLAY = (206, 196, 184)
UNSET_RGB = (150, 146, 142)


def camera(yaw: float, pitch: float) -> np.ndarray:
    y, p = np.radians(yaw), np.radians(pitch)
    forward = -np.array([np.sin(y) * np.cos(p), -np.cos(y) * np.cos(p), np.sin(p)])
    right = np.cross(forward, [0, 0, 1.0])
    right /= max(np.linalg.norm(right), 1e-9)
    up = np.cross(right, forward)
    return np.stack([right, up, -forward])


def region_colors(count: int) -> np.ndarray:
    """Well separated colours for telling regions apart (golden-angle hues, three lightness bands)."""
    i = np.arange(max(count, 1))
    h = (i * 0.61803398875) % 1.0
    s = np.array([0.85, 0.6, 0.95])[i % 3]
    v = np.array([0.95, 0.8, 0.65])[(i // 3) % 3]
    k = (h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r = np.choose(k, [v, q, p, p, t, v])
    g = np.choose(k, [t, v, v, q, p, p])
    b = np.choose(k, [p, p, t, v, v, q])
    return (np.stack([r, g, b], 1) * 255).astype(np.uint8)


class Frame:
    """One view: which face shows at each pixel (-1 none), its depth, and how to place a 3-D point in the picture."""

    def __init__(self, face: np.ndarray, depth: np.ndarray, R: np.ndarray, lo: np.ndarray, px: float, size: int):
        self.face, self.depth, self.R, self.lo, self.px, self.size = face, depth, R, lo, px, size

    def project(self, points: np.ndarray) -> np.ndarray:
        q = np.asarray(points, float) @ self.R.T
        x = (q[:, 0] - self.lo[0]) / self.px
        y = self.size - 1 - (q[:, 1] - self.lo[1]) / self.px
        return np.stack([x, y, q[:, 2]], 1)


def rasterize(P: np.ndarray, F: np.ndarray, *, yaw: float = 35, pitch: float = 25, size: int = 900, region=None, seed: int = 3,
              cull: bool = True) -> Frame:
    """``region`` = (cx, cy, cz, radius) in millimetres zooms on a detail. ``cull`` leaves out faces that look away
    from the camera (right for closed figures; switch it off for open sheets)."""
    R = camera(yaw, pitch)
    ids = np.arange(len(F))
    if region is not None:
        c, rad = np.asarray(region[:3], float), float(region[3])
        near = np.linalg.norm(P[F].mean(axis=1) - c, axis=1) < rad * 1.6
        if near.any():
            F, ids = F[near], ids[near]
    Q = P @ R.T
    a, b, c3 = Q[F[:, 0]], Q[F[:, 1]], Q[F[:, 2]]
    if cull and len(F):
        facing = ((b[:, 0] - a[:, 0]) * (c3[:, 1] - a[:, 1]) - (c3[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])) > 0
        if 0.15 < float(facing.mean()) < 0.85:             # outside that the normals are not to be trusted
            F, ids = F[facing], ids[facing]
            a, b, c3 = a[facing], b[facing], c3[facing]
    if region is not None:
        cq = np.asarray(region[:3], float) @ R.T
        lo = np.array([cq[0] - region[3], cq[1] - region[3]])
        span = 2 * float(region[3])
    else:
        used = Q[np.unique(F)] if len(F) else Q
        low, high = used[:, :2].min(0), used[:, :2].max(0)
        span = float((high - low).max()) * 1.06 or 1.0
        lo = 0.5 * (low + high) - 0.5 * span
    px = span / size
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c3[:, 1] - a[:, 1]) - (c3[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    count = np.clip(np.ceil(area / (px * px) * 4.0).astype(np.int64), 1, 400_000)
    total = int(count.sum())
    limit = 24_000_000
    if total > limit:
        count = np.maximum((count * (limit / total)).astype(np.int64), 1)
    owner = np.repeat(np.arange(len(F)), count)
    rng = np.random.default_rng(seed)
    r1, r2 = np.sqrt(rng.random(len(owner), dtype=np.float32)), rng.random(len(owner), dtype=np.float32)
    w0, w1, w2 = 1 - r1, r1 * (1 - r2), r1 * r2
    x = a[owner, 0] * w0 + b[owner, 0] * w1 + c3[owner, 0] * w2
    y = a[owner, 1] * w0 + b[owner, 1] * w1 + c3[owner, 1] * w2
    z = (a[owner, 2] * w0 + b[owner, 2] * w1 + c3[owner, 2] * w2).astype(np.float32)
    ix = np.floor((x - lo[0]) / px).astype(np.int64)
    iy = size - 1 - np.floor((y - lo[1]) / px).astype(np.int64)
    ok = (ix >= 0) & (ix < size) & (iy >= 0) & (iy < size)
    lin, z, owner = (iy[ok] * size + ix[ok]), z[ok], owner[ok]
    order = np.argsort(z, kind="stable")                    # nearest to the camera (largest z) written last
    depth = np.full(size * size, -np.inf, np.float32)
    face = np.full(size * size, -1, np.int64)
    depth[lin[order]] = z[order]
    face[lin[order]] = ids[owner[order]]
    depth, face = depth.reshape(size, size), face.reshape(size, size)
    # a pixel no sample reached takes its nearest filled neighbour when it is surrounded by surface
    hit = face >= 0
    if hit.any():
        # a pixel whose only sample came from far behind its neighbours (a gap between samples) takes the front one
        raw = np.where(hit, depth, -1e30)
        front = ndimage.minimum_filter(ndimage.maximum_filter(raw, 3), 3)
        sunk = hit & (raw < front - px * 3.0)
        face[sunk], depth[sunk], hit = -1, -np.inf, hit & ~sunk
        closed = ndimage.binary_closing(hit, np.ones((3, 3), bool)) | sunk
        hole = closed & ~hit
        if hole.any():
            _, (jy, jx) = ndimage.distance_transform_edt(~hit, return_indices=True)
            face[hole] = face[jy[hole], jx[hole]]
            depth[hole] = depth[jy[hole], jx[hole]]
    return Frame(face, depth, R, lo, px, size)


def shade(frame: Frame, face_rgb: Optional[np.ndarray] = None, flat: float = 0.0) -> Image.Image:
    """The picture of a frame. ``face_rgb`` (m, 3) colours each face (None = clay); ``flat`` 0-1 fades the lighting
    towards plain colour (1 = pure colour map)."""
    size, d0, face = frame.size, frame.depth, frame.face
    hit = face >= 0
    if not hit.any():
        return Image.new("RGB", (size, size), BACKGROUND)
    d = np.where(hit, d0, d0[hit].min())
    soft = ndimage.gaussian_filter(np.where(hit, d, 0.0), 0.7) / np.maximum(ndimage.gaussian_filter(hit.astype(np.float32), 0.7), 1e-6)
    d = np.where(hit, soft, d)
    gy, gx = np.gradient(d / frame.px)
    normal = np.stack([-gx, gy, np.ones_like(d)], -1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    light = np.array([-0.45, 0.55, 0.7]); light /= np.linalg.norm(light)
    fill = np.array([0.6, -0.2, 0.5]); fill /= np.linalg.norm(fill)
    lit = 0.34 + 0.6 * np.clip(normal @ light, 0, 1) + 0.16 * np.clip(normal @ fill, 0, 1)
    cavity = d - ndimage.gaussian_filter(d, 2.5)
    lit *= np.clip(1.0 + cavity / (frame.px * 6.0), 0.6, 1.12)
    lit = lit * (1 - flat) + flat
    if face_rgb is None:
        base = np.broadcast_to(np.array(CLAY, np.float32), (size, size, 3))
    else:
        base = np.asarray(face_rgb, np.float32)[np.where(hit, face, 0)]
    img = np.clip(lit[..., None] * base, 0, 255)
    img[~hit] = BACKGROUND
    return Image.fromarray(img.astype(np.uint8))


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def number_regions(img: Image.Image, frame: Frame, face_region: np.ndarray, min_pixels: int = 60) -> tuple[Image.Image, list[int]]:
    """Writes each region's number over the place where it shows widest. Returns the picture and the regions labelled."""
    reg = np.where(frame.face >= 0, face_region[np.where(frame.face >= 0, frame.face, 0)], -1)
    draw = ImageDraw.Draw(img)
    font = _font(max(frame.size // 52, 12))
    shown = []
    ids, counts = np.unique(reg[reg >= 0], return_counts=True)
    for r, n in sorted(zip(ids.tolist(), counts.tolist()), key=lambda t: -t[1]):
        if n < min_pixels:
            continue
        mask = reg == r
        dist = ndimage.distance_transform_edt(mask)
        y, x = np.unravel_index(int(dist.argmax()), dist.shape)
        text = str(r)
        box = draw.textbbox((0, 0), text, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        x0, y0 = int(x - w / 2), int(y - h / 2)
        draw.rectangle([x0 - 3, y0 - 2, x0 + w + 3, y0 + h + 4], fill=(0, 0, 0))
        draw.text((x0, y0 - box[1]), text, fill=(255, 255, 255), font=font)
        shown.append(int(r))
    return img, shown


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()


def sheet(images: list[Image.Image], labels: Optional[list[str]] = None, columns: int = 0, gap: int = 6) -> Image.Image:
    """Pictures side by side (or in a grid with ``columns``), each with an optional caption."""
    columns = columns or len(images)
    rows = -(-len(images) // columns)
    w, h = max(i.width for i in images), max(i.height for i in images)
    out = Image.new("RGB", (columns * w + gap * (columns - 1), rows * h + gap * (rows - 1)), BACKGROUND)
    draw = ImageDraw.Draw(out)
    font = _font(max(h // 40, 12))
    for k, im in enumerate(images):
        x, y = (k % columns) * (w + gap), (k // columns) * (h + gap)
        out.paste(im, (x + (w - im.width) // 2, y + (h - im.height) // 2))
        if labels and k < len(labels) and labels[k]:
            draw.text((x + 8, y + 6), labels[k], fill=(240, 232, 215), font=font)
    return out


def swatches(palette: list[tuple[int, int, int]], names: list[str], width: int = 900, height: int = 90) -> Image.Image:
    """A strip with the palette: one block per colour with its index and name."""
    img = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(img)
    font = _font(15)
    n = max(len(palette), 1)
    for k, rgb in enumerate(palette):
        x0, x1 = int(k * width / n), int((k + 1) * width / n) - 4
        draw.rectangle([x0, 0, x1, height - 26], fill=tuple(int(c) for c in rgb))
        draw.text((x0 + 4, height - 22), f"{k}: {names[k] if k < len(names) else ''}", fill=(240, 232, 215), font=font)
    return img


def fit_image(data: bytes, size: int) -> Image.Image:
    """A reference picture brought to at most ``size`` pixels a side on the studio's background."""
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (245, 245, 245, 255))
        img = Image.alpha_composite(bg, rgba)
    img = img.convert("RGB")
    img.thumbnail((size, size), Image.LANCZOS)
    return img


def png_file(img: Image.Image, path) -> None:
    img.save(path, "PNG")


def points_image(points: np.ndarray, rgb: np.ndarray, *, yaw: float = 0, pitch: float = 10, size: int = 520) -> Image.Image:
    """A textured model shown from its coloured samples (nearest sample per pixel, gaps closed)."""
    R = camera(yaw, pitch)
    q = np.asarray(points, np.float64) @ R.T
    low, high = q[:, :2].min(0), q[:, :2].max(0)
    span = float((high - low).max()) * 1.06 or 1.0
    lo = 0.5 * (low + high) - 0.5 * span
    px = span / size
    ix = np.clip(np.floor((q[:, 0] - lo[0]) / px).astype(np.int64), 0, size - 1)
    iy = np.clip(size - 1 - np.floor((q[:, 1] - lo[1]) / px).astype(np.int64), 0, size - 1)
    order = np.argsort(q[:, 2], kind="stable")
    owner = np.full(size * size, -1, np.int64)
    owner[iy[order] * size + ix[order]] = order
    owner = owner.reshape(size, size)
    hit = owner >= 0
    if hit.any():
        closed = ndimage.binary_closing(hit, np.ones((5, 5), bool))
        hole = closed & ~hit
        if hole.any():
            _, (jy, jx) = ndimage.distance_transform_edt(~hit, return_indices=True)
            owner[hole] = owner[jy[hole], jx[hole]]
        hit = owner >= 0
    img = np.empty((size, size, 3), np.uint8)
    img[:] = BACKGROUND
    img[hit] = np.asarray(rgb)[owner[hit]]
    return Image.fromarray(img)
