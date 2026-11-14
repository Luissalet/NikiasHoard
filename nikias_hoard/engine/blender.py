"""Models only Blender reads (FBX, .blend): Blender runs in the background and hands back a GLB with the pose and the
modifiers the file was saved with already applied, so a figure posed in a .blend comes out in that pose."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

SCRIPT = Path(__file__).with_name("blender_export.py")


def find_blender(configured: str = "") -> Optional[str]:
    if configured and Path(configured).is_file():
        return configured
    env = os.environ.get("NIKIAS_BLENDER") or os.environ.get("BLENDER")
    if env and Path(env).is_file():
        return env
    found = shutil.which("blender")
    if found:
        return found
    if sys.platform == "win32":
        root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
        best: list[tuple[tuple[int, ...], str]] = []
        if root.is_dir():
            for d in root.iterdir():
                exe = d / "blender.exe"
                if exe.is_file():
                    nums = tuple(int(x) for x in "".join(c if c.isdigit() else " " for c in d.name).split())
                    best.append((nums, str(exe)))
        if best:
            return sorted(best)[-1][1]
    for cand in ("/Applications/Blender.app/Contents/MacOS/Blender", "/usr/bin/blender", "/snap/bin/blender"):
        if Path(cand).is_file():
            return cand
    return None


def to_glb(blender: str, source: Path, glb: Path, timeout_s: float = 600) -> None:
    cmd = [blender, "-b", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1", "--python", str(SCRIPT), "--",
           str(Path(source).resolve()), str(Path(glb).resolve())]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    run = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout_s, creationflags=flags)
    if run.returncode or not Path(glb).is_file():
        tail = "\n".join((run.stdout + "\n" + run.stderr).strip().splitlines()[-10:])
        raise RuntimeError(f"Blender did not export the model.\n{tail}")
