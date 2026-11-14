"""Run inside background Blender: write the scene's visible meshes as one self-contained GLB, with modifiers and the
saved pose applied (no skeleton in the file)."""

import sys
from pathlib import Path

import bpy

source, destination = map(Path, sys.argv[sys.argv.index("--") + 1:])
ext = source.suffix.lower()
if ext == ".blend":
    bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
else:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext == ".fbx":
        done = bpy.ops.import_scene.fbx(filepath=str(source))
    elif ext == ".dae" and hasattr(bpy.ops.wm, "collada_import"):
        done = bpy.ops.wm.collada_import(filepath=str(source))
    else:
        raise RuntimeError(f"Unsupported format: {ext}")
    if done != {"FINISHED"}:
        raise RuntimeError("Import cancelled")

if not any(obj.type == "MESH" and len(obj.data.polygons) and obj.visible_get() for obj in bpy.context.scene.objects):
    raise RuntimeError("The scene has no visible mesh faces")

options = dict(filepath=str(destination), export_format="GLB", export_animations=False, export_apply=True, export_skins=False,
               use_visible=True, export_cameras=False, export_lights=False)
try:
    done = bpy.ops.export_scene.gltf(**options)
except TypeError:                                   # an option this Blender does not know
    options.pop("use_visible", None)
    done = bpy.ops.export_scene.gltf(**options)
if done != {"FINISHED"}:
    raise RuntimeError("glTF export cancelled")
