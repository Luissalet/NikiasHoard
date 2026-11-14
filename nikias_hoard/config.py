"""Process configuration (environment) and the editable settings (``<data>/settings.json``)."""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import APP_ID, DEFAULT_PORT
from .hoard_link.appconfig import AppPaths, env_int, env_str
from .hoard_link.atomic import read_json, write_json_atomic
from .hoard_link.guard import parse_allowed_hosts

REPO_ROOT = Path(__file__).resolve().parent.parent

# The spools on the shelf: what a palette colour can be matched to. Plain colours every brand sells; edit them in Ajustes.
DEFAULT_FILAMENTS = [
    {"name": "Blanco", "hex": "#f5f5f5", "material": "PLA"}, {"name": "Negro", "hex": "#161616", "material": "PLA"},
    {"name": "Gris", "hex": "#8e9089", "material": "PLA"}, {"name": "Plata", "hex": "#a6a9aa", "material": "PLA"},
    {"name": "Rojo", "hex": "#c12e1f", "material": "PLA"}, {"name": "Naranja", "hex": "#ff6a13", "material": "PLA"},
    {"name": "Amarillo", "hex": "#f4ee2a", "material": "PLA"}, {"name": "Dorado", "hex": "#e4bd68", "material": "PLA"},
    {"name": "Verde", "hex": "#00ae42", "material": "PLA"}, {"name": "Verde claro", "hex": "#becf00", "material": "PLA"},
    {"name": "Turquesa", "hex": "#00b1b7", "material": "PLA"}, {"name": "Azul claro", "hex": "#56b7e6", "material": "PLA"},
    {"name": "Azul", "hex": "#1f5fc4", "material": "PLA"}, {"name": "Azul marino", "hex": "#0a2989", "material": "PLA"}, {"name": "Morado", "hex": "#5e43b7", "material": "PLA"},
    {"name": "Rosa", "hex": "#f55a74", "material": "PLA"}, {"name": "Magenta", "hex": "#ec008c", "material": "PLA"},
    {"name": "Marrón", "hex": "#9d432c", "material": "PLA"}, {"name": "Beige", "hex": "#f7e6de", "material": "PLA"},
    {"name": "Carne", "hex": "#f1c6a5", "material": "PLA"}, {"name": "Crema", "hex": "#f2e3b5", "material": "PLA"},
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "library_dirs": [],          # folders with finished print figures (STL)
    "reference_dirs": [],        # folders where textured models and pictures of the characters are looked for by name
    "photo_dirs": [],            # folders with a picture per figure (same file name), shown in the library
    "variant_suffixes": ["flat", "pivot"],   # "<name> <suffix>.stl" is the same figure cut another way: it takes the same colours
    "part_suffixes": ["base"],   # "<name> <suffix>.stl" is a separate piece listed with its figure
    "colors": 4,                 # print colours of a new figure (what a four-spool changer loads)
    "accent": 0.6,               # 1 = colours chosen by area alone; lower lets small distinct colours (eyes) keep their own
    "detail": 3,                 # 1-5: how finely a figure is cut into regions
    "max_regions": 60,
    "min_detail_mm": 0.8,        # a patch of colour narrower than this cannot be printed: it joins its surroundings
    "smooth": 2,                 # passes that straighten colour borders
    "fit_good": 0.012,           # a textured model whose typical gap to the figure is under this share of its size is trusted
    "samples": 700_000,          # coloured points taken from a textured model
    "blender": "",               # path of blender.exe; empty: found by itself
    "llm_url": "",               # empty: the family's vision model through Hoard Link; else an OpenAI-compatible URL
    "llm_model": "",
    "llm_timeout_s": 1800,       # per question to the model
    "director_rounds": 2,        # times the model may look at its colouring and correct it
    "export_dir": "",            # empty: a "Multicolor" folder next to each figure
    "export_formats": ["bambu", "prusa", "color"],
    "filaments": DEFAULT_FILAMENTS,
}
FORMATS = ("bambu", "prusa", "color", "obj", "stl")


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    port: int = DEFAULT_PORT
    allowed_hosts: tuple[str, ...] = ()
    data_dir_configured: bool = False
    background: bool = True          # the worker thread; tests switch it off and run jobs inline

    @property
    def paths(self) -> AppPaths:
        return AppPaths(APP_ID, REPO_ROOT, self.data_dir, self.data_dir_configured)

    @property
    def token_path(self) -> Path:
        return self.paths.token_path

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @classmethod
    def from_env(cls) -> "Config":
        data_env = env_str("NIKIAS_DATA_DIR")
        return cls(
            data_dir=Path(data_env).expanduser() if data_env else REPO_ROOT / "data",
            port=env_int("NIKIAS_PORT", default=DEFAULT_PORT) or DEFAULT_PORT,
            allowed_hosts=tuple(parse_allowed_hosts(os.environ.get("NIKIAS_ALLOWED_HOSTS", ""))),
            data_dir_configured=bool(data_env),
        )


class Settings:
    """Thread-safe JSON settings with defaults; ``update`` validates and writes atomically."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        raw = read_json(self.path, default={}) or {}
        data = copy.deepcopy(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
        try:
            return normalize(data)
        except (ValueError, TypeError):
            return copy.deepcopy(DEFAULT_SETTINGS)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return copy.deepcopy(self._data[key])

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = copy.deepcopy(self._data)
            for key, value in dict(changes).items():
                if key not in DEFAULT_SETTINGS:
                    raise ValueError(f"Unknown setting: {key}")
                if value is None:
                    continue
                data[key] = value
            data = normalize(data)
            self._data = data
            write_json_atomic(self.path, data)
        return self.get()


def _clamp(v: Any, lo: float, hi: float, kind=float):
    return kind(min(max(kind(v), lo), hi))


def _dirs(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(d).strip() for d in (value or []) if str(d).strip()][:32]


def _words(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(w).strip().lower() for w in (value or []) if str(w).strip()][:12]


def _hex(text: Any) -> str:
    s = str(text or "").strip().lstrip("#").lower()
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6 or any(ch not in "0123456789abcdef" for ch in s):
        raise ValueError(f"not a colour: {text!r} (use #rrggbb)")
    return "#" + s


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("library_dirs", "reference_dirs", "photo_dirs"):
        data[key] = _dirs(data.get(key))
    data["variant_suffixes"] = _words(data.get("variant_suffixes"))
    data["part_suffixes"] = _words(data.get("part_suffixes"))
    data["colors"] = _clamp(data["colors"], 2, 16, int)
    data["accent"] = _clamp(data["accent"], 0.2, 1.0)
    data["detail"] = _clamp(data["detail"], 1, 5, int)
    data["max_regions"] = _clamp(data["max_regions"], 4, 250, int)
    data["min_detail_mm"] = _clamp(data["min_detail_mm"], 0, 20)
    data["smooth"] = _clamp(data["smooth"], 0, 12, int)
    data["fit_good"] = _clamp(data["fit_good"], 0.001, 0.2)
    data["samples"] = _clamp(data["samples"], 20_000, 4_000_000, int)
    data["blender"] = str(data.get("blender") or "").strip()
    data["llm_url"] = str(data.get("llm_url") or "").strip()
    data["llm_model"] = str(data.get("llm_model") or "").strip()
    data["llm_timeout_s"] = _clamp(data["llm_timeout_s"], 30, 14400, int)
    data["director_rounds"] = _clamp(data["director_rounds"], 0, 6, int)
    data["export_dir"] = str(data.get("export_dir") or "").strip()
    formats = data.get("export_formats") or []
    if isinstance(formats, str):
        formats = formats.split(",")
    formats = [str(f).strip().lower() for f in formats if str(f).strip()]
    bad = [f for f in formats if f not in FORMATS]
    if bad:
        raise ValueError(f"export_formats: unknown {bad[0]} (use {', '.join(FORMATS)})")
    data["export_formats"] = list(dict.fromkeys(formats)) or ["bambu"]
    spools = []
    for f in data.get("filaments") or []:
        if not isinstance(f, dict) or not str(f.get("name") or "").strip():
            raise ValueError("each filament needs a name and a colour")
        spools.append({"name": str(f["name"]).strip()[:60], "hex": _hex(f.get("hex")), "material": str(f.get("material") or "PLA").strip()[:20]})
    data["filaments"] = spools[:200]
    return data
