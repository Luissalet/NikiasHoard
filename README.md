# Nikias's Hoard

Colouring studio for multi-material 3D printing: gives a finished print figure a few filament colours and writes the files a slicer opens already painted. [Español](README.es.md)

![Nikias's Hoard](app-icon.png)

## What it is

Colouring studio of the Hoard family. It opens a finished, single-colour print figure (an STL sculpt) and decides which
of a few filament colours (3-4 by default) every triangle is printed in, from a textured model of the same figure,
from reference pictures, or from what the family's vision model knows of the character. The result is a painted 3MF
for the slicer, with the figure itself untouched.

## What it does

- **Library**: point it at the folders where you keep your figures. It lists every STL grouped by folder, with its
  picture when a photo folder has one of the same name. Files named `<figure> flat.stl` and `<figure> pivot.stl` are
  shown inside their figure as variants, and `<figure> base.stl` or `<figure> <something>.stl` as its loose pieces.
- **Project**: opening a figure welds its triangles, measures it and cuts it into regions. Nothing is copied or
  changed in the figure's folder.
- **Regions**: a fused figure has no materials, but where two parts meet (a sleeve against an arm, a glove against
  its cuff, an engraved line) the surface folds inwards. Those folds separate the regions; detail 1-5 goes from big
  parts to every engraved line. Regions are what gets a colour, by the model or by a click.
- **Colour from a textured model**: add the game model or the `.blend` the figure was posed in (OBJ, GLB, glTF, DAE;
  FBX and `.blend` through Blender, with the saved pose applied). It is laid over the figure by itself, whatever its
  units and orientation, and the fit is measured. When the shapes agree, each face takes the colour the texture has
  at the nearest point, and the colours are reduced to the print palette. When they do not (another pose), the model
  stays as a reference picture for the vision model.
- **Colour directed by the vision model**: the model looks at the references, chooses the palette, and is then shown
  the figure one region at a time (the figure in clay with a single region marked, from the side where it shows
  most, with a close-up for small ones) and says what each part is and which colour it takes. Regions it finds mixed,
  or that hold a colour nobody received (eyes inside a head), are cut finer and asked again. It then looks at its own
  colouring next to the references and corrects the parts it finds wrong. What it decided and why is kept in the
  project. With no references it works from what it knows of the character.
- **Palette**: colours are compared in CIELAB and reduced with a weighted clustering that lets a small distinct
  colour keep its own. Each colour is matched to the nearest filament on your shelf (name, colour, material); you can
  change a colour, its name or its filament, add or merge colours, choose which one is the object's own (filament 1)
  and snap the whole palette to your filaments. A colour that ends on no face is dropped.
- **Painting by hand**: click a region to fill it, fill the single-colour patch under the cursor, or drag a brush of
  a given size in millimetres; Alt + click picks a colour; the last 20 changes can be undone.
- **Printable colours**: borders between colours are straightened and patches narrower than a set size join their
  surroundings, so no speckle reaches the slicer. Each colour shows its share of the surface and its patches.
- **Views**: colour, filament colour, regions and clay, in the 3-D view and as pictures from any side or zoomed on
  a spot (shaded from the geometry, so engraved lines read as they will print).
- **Export**: a painted 3MF for Bambu Studio / OrcaSlicer, one for PrusaSlicer, a standard-colour 3MF, an OBJ with
  one material per colour, one STL per colour, and a text file saying which filament is which. The figure's flat and
  pivot variants take the colours from the figure and are written with it. Files go to a `Multicolor` folder next
  to the figure, or to the folder you set.

## Connection with Faustus and Hoard Hub

`faustus-plugin.json` (id `nikias`) registers the app as a Faustus plugin: Hoard Hub starts it on demand on port 5252
and checks `/api/health`. The same operations the buttons use are available to assistants through MCP
(`mcp_server.py`, stdio); `figure_look` and `source_look` return pictures.

| Group | Tools |
| --- | --- |
| Library and projects | `status`, `library_scan`, `project_create`, `project_list`, `project_get`, `project_rename`, `project_delete` |
| References | `source_find`, `source_add`, `source_remove`, `source_look` |
| Colouring | `regions_set`, `color_start`, `job_status`, `job_cancel` |
| Palette and paint | `palette_set`, `paint`, `paint_undo`, `clean` |
| Views and files | `figure_look`, `result_export` |
| Settings | `settings_get`, `settings_set` |

Nikias also takes from the family's skill library the procedures its work needs and hands them to connecting agents
(button "Skills").

## Run

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m nikias_hoard
```

It listens on http://127.0.0.1:5252. Data lives in `data/` (`NIKIAS_DATA_DIR` to move it). Set the folders with your
figures, your references and your photos in Ajustes. Blender is found by itself (or set its path); it is only needed
for FBX and `.blend` references. The vision model is the one the family resolves through Hoard Link, or the
OpenAI-compatible server set in Ajustes. Tests: `python -m pytest`.

## Limits

- Colours are per triangle: a border between two colours follows the figure's own triangles, so its sharpness is
  that of the mesh. On dense sculpts that is finer than a nozzle.
- A textured model only passes its colours directly when it is in the same pose as the figure. For another pose it
  is a reference for the vision model.
- The vision model decides by regions. A colour change that no fold or engraved line marks (a pattern on a smooth
  surface) is not a region: it is painted with the brush or comes from a model that fits.
- The painted 3MF carries which filament each triangle uses, not the slicer project: filament colours are assigned
  in the slicer, following the text file written next to it. The Bambu/Orca file has been opened in OrcaSlicer 2.3.1
  (it shows the painting as soon as the project has that many filaments); the PrusaSlicer and standard-colour files
  are checked by structure only.
- Colours are on the surface, as slicers paint them. It does not cut the figure into one closed solid per colour.

## Project layout

- `nikias_hoard/engine/`: `mesh.py` (STL, welding, neighbours), `regions.py` (folds and regions), `source.py`
  (sampling a textured model, laying it over the figure, passing colours), `palette.py` (CIELAB, reduction, filaments),
  `labels.py` (smoothing, islands, brush), `render.py` (pictures), `export.py` (3MF, OBJ, STL), `modelio.py` (textured
  model readers), `blender.py` + `blender_export.py` (FBX and `.blend`).
- `nikias_hoard/director.py` (the vision model's direction), `llm.py` (its client).
- `nikias_hoard/services.py` (library, projects, references, jobs, palette, paint, export), `agent_tools.py` (the
  catalogue), `api/` (routes), `static/` (the studio), `store.py` (projects on disk).
- Data: `data/settings.json`, `data/projects/<id>/` (`project.json`, welded mesh, regions, colours, reference
  pictures), `data/mcp-token`.
- Tests: `tests/test_engine.py`, `tests/test_app.py`.

## Licence

MIT. Third-party notices in `THIRD_PARTY_NOTICES.md`.
