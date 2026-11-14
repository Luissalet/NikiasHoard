"""Projects on disk: ``<data>/projects/<id>/project.json`` plus what each one makes (welded mesh, regions, colours,
reference pictures). A project points at its figure where it is; nothing is copied or changed there."""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .errors import NikiasError
from .hoard_link.atomic import read_json, write_json_atomic


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def new_id(self) -> str:
        return uuid.uuid4().hex[:10]

    def dir(self, pid: str) -> Path:
        if not pid or not pid.isalnum():
            raise NikiasError("not_found", "No hay ningún proyecto con ese id.")
        return self.root / pid

    def get(self, pid: str) -> dict[str, Any]:
        data = read_json(self.dir(pid) / "project.json", default=None)
        if not isinstance(data, dict):
            raise NikiasError("not_found", "No hay ningún proyecto con ese id.", "Usa project_list para ver los que hay.")
        return data

    def save(self, project: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            project["updated"] = time.time()
            d = self.dir(project["id"])
            d.mkdir(parents=True, exist_ok=True)
            write_json_atomic(d / "project.json", project)
        return project

    def list(self) -> list[dict[str, Any]]:
        out = []
        for d in self.root.iterdir() if self.root.is_dir() else []:
            data = read_json(d / "project.json", default=None)
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
        return sorted(out, key=lambda p: -float(p.get("updated", 0)))

    def delete(self, pid: str) -> None:
        d = self.dir(pid)
        self.get(pid)
        with self._lock:
            shutil.rmtree(d, ignore_errors=True)
