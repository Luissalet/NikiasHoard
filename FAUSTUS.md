# Nikias's Hoard — what it can and cannot do

App id `nikias`, service `nikias-hoard`, port 5252, MCP stdio bridge `mcp_server.py` (`figure_look` and `source_look`
return MCP images). Connector preset: its own `faustus-plugin.json`.

## Can

- Skills shelf (`hoard_link/skills.py`, wired in `api/agent.py`): the `skills` block of `faustus-plugin.json` names the
  library skills this app wants (`blender-asset-production`, `web-3d-asset-pipeline`, `verification-loop`, plus the
  closest by the tags `3d` and `print`, 4 at most). They are copied to `data/skills/`, appended to the agent
  instructions, served by the skills tools of the agent router and shown on the page behind the "Skills" button.
- Library (`library_scan`): walks `library_dirs` for STL files, grouped by folder (`group` is the path inside the
  library folder). `<name> <suffix>.stl` with a suffix of `variant_suffixes` (flat, pivot) is a variant of `<name>`;
  with one of `part_suffixes` (base), or any other `<name> <words>.stl` that has no variants of its own, a loose
  piece. `photo` says whether a picture of the same name exists under `photo_dirs` (`GET /api/library/photo`).
  Filters: `query`, `group` (prefix), `limit`, `offset`. A `Multicolor` folder is never read as figures.
- Project (`project_create`, `project_list`, `project_get`, `project_rename`, `project_delete`): opening an STL
  (binary or ASCII) welds its corners, drops degenerate faces, stores `mesh.npz`, cuts regions and writes
  `figure.bin` for the 3-D view. Opening the same file again returns its project. `project_get` adds per colour
  the area, share, patches and smallest patch, the coloured share and how many undo steps exist. Up to 6 million
  triangles.
- Regions (`regions_set`): face normals are averaged over a distance tied to the figure's size, faces where they turn
  inwards faster than a threshold are folds, connected components of the rest are regions, fold faces join the region
  they touch, regions under a share of the surface join the neighbour they share the longest border with, and the
  count is capped by `max_regions`. `detail` 1-5 sets distance, threshold and smallest share. Without arguments it
  lists the regions with their share, centre, height and current colour. Recutting keeps the paint.
- References (`source_find`, `source_add`, `source_remove`, `source_look`): `source_add` takes a picture (kept as a
  PNG of at most 1400 px), a textured model or a folder (its best model, else up to 8 pictures); the page also takes
  dropped, pasted and uploaded pictures (`POST /api/projects/<id>/sources/upload`). A model (OBJ+MTL, GLB/glTF, DAE;
  FBX and `.blend` converted with Blender to a GLB with modifiers and the saved pose applied) is sampled into
  `samples` coloured points, laid over the figure (24 upright turns, any units, then point-to-point refinement with
  scale) and gets `fit` = median gap as a share of the figure's diagonal, `good` when under `fit_good`. Its picture is
  the model from four sides in its colours. `source_find` lists files under `reference_dirs` named like the figure.
- Colouring (`color_start`, `job_status`, `job_cancel`), one job per project in a background worker:
  - `mode: model`: each face takes the commonest colour among its 4 nearest samples of the model, the colours are
    reduced to `colors` (CIELAB k-means on colour bins with dampened weights, `accent`), borders are smoothed and
    small patches absorbed. The result reports the fit, how many colours the texture asks for and the share of
    faces far from the model. `palette_set count` chooses the colours again from the stored face colours.
  - `mode: ai`: the vision model (the family's, or `llm_url`/`llm_model`) chooses the palette from the references
    and the clay figure; is shown every region as a tile (figure in clay, that region in a marker colour, from the
    best of nine views, close-up for small ones), twelve per question, and answers what each is and its colour;
    regions flagged mixed, and the parts it says hold colours nobody received, are cut at a finer detail and asked
    again in close-up; then it reviews the coloured figure (`rounds`, default `director_rounds`) and names the parts
    to change; colours left on no face are dropped. `wish` is passed to every question. The log is `project.director`.
  - `mode: auto`: model when a source is `good`, else ai.
- Palette (`palette_set`): edit colours by position (hex, name, filament by name), add colours, `count` (merge the
  most alike or add spare filaments; from a model, re-reduce), `merge [keep, remove]`, `base` (swap with index 0),
  `snap` (every colour becomes its filament's colour). Up to 16 colours. Filaments come from the setting `filaments`.
- Paint (`paint`, `paint_undo`, `clean`): fill regions, the same-colour patch around faces, brush spots (points,
  radius in mm, optional normals so the back of a thin part is not painted) or everything; `only_unset` limits it to
  faces without colour. 20 undo steps in memory per open project. `clean` smooths borders and absorbs patches under
  `min_detail_mm` squared.
- Pictures (`figure_look`): `what` color, filament, regions (numbered), clay, model (colours before reducing),
  compare (clay next to colour), sources; a named view, several views in a grid, `yaw`/`pitch`, or a close-up
  `region` [x, y, z, radius]. Rendered on the CPU by sampling the surface; back faces are culled on closed figures.
- Export (`result_export`): `bambu` (3MF laid out as Bambu Studio and OrcaSlicer save a project: the object as a
  component, its mesh in `3D/Objects/object_1.model` with `paint_color` per triangle, `Metadata/model_settings.config`;
  opened in OrcaSlicer 2.3.1, where the painting shows once the project has that many filaments), `prusa` (3MF with
  `slic3rpe:mmu_segmentation`), `color` (3MF materials extension colour group), `obj` (OBJ + MTL), `stl` (one STL of
  open surfaces per colour), plus `<figure> colores.txt`. Palette index 0 is filament 1 and carries no tag. The figure
  stands on the plate. Variants are read, each face takes the colour of the nearest face of
  the figure, faces the cut created take the colour around them. Default folder: `Multicolor` next to the figure, or
  `export_dir/<group>`. `GET /api/projects/<id>/download/<bambu|prusa|color>` sends one file to the browser.
- Studio (`static/`): library with photos and collection filter, projects, 3-D view with one colour per face, view
  modes, tools (orbit, region, patch, brush with size, Alt + click picker, undo), palette card, references card
  (drop, paste, upload, by path, find by name), region detail, what the model decided, export dialog, settings
  (folders, colours per figure, smallest patch, review rounds, export folder, vision model, filaments).

## Cannot

- Pass colours from a textured model in a different pose than the figure: such a model is only a reference picture.
- Find a colour border that no fold marks (a pattern on a smooth surface): that needs the brush or a fitting model.
- Split triangles along a colour border: borders follow the mesh's own triangles.
- Put the filament colours inside the 3MF: OrcaSlicer takes a settings file that only lists filaments as unknown
  presets and loads nothing, so it is not written (`export.write_3mf(project=True)` keeps the attempt). The PrusaSlicer
  and standard-colour files have not been opened in their programs.
- Cut the figure into closed solids per colour, or add a figure's loose pieces to the same file.
- Look for references on the web, or read reference models inside archives.
- Keep undo steps after the app restarts.
- Open figures that are not STL.

## Where things are

- `nikias_hoard/engine/`: `mesh.py`, `regions.py`, `source.py`, `palette.py`, `labels.py`, `render.py`, `export.py`,
  `modelio.py`, `blender.py`, `blender_export.py`.
- `nikias_hoard/director.py`, `llm.py`, `services.py`, `agent_tools.py`, `api/`, `static/`, `store.py`, `config.py`.
- Data: `data/settings.json`, `data/projects/<id>/` (`project.json`, `mesh.npz`, `regions.npy`, `labels.npy`,
  `rgb.npy`, `figure.bin`, `src_<id>.png|npz|glb`), `data/thumbs/`, `data/skills/`, `data/mcp-token`.
- Tests: `tests/test_engine.py` (engine on a figure built by code), `tests/test_app.py` (the app over HTTP with a
  stand-in for the vision model).
