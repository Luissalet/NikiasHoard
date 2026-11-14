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
- Regions (`regions_set`): face normals are averaged over a distance tied to the figure's size and faces where they
  turn inwards faster than a threshold are folds. The folds are widened by as many face rings as close a gap of a set
  share of the figure's diagonal (an engraved outline left open still separates), the connected components of the rest
  are the seeds (specks dropped), the seeds grow back ring by ring until they meet, two regions whose common border
  has under half its length on a fold become one when together they stay under 1.5 % of the surface, regions under a share of the
  surface join the neighbour they share the longest border with, and the count is capped by `max_regions` (150).
  `detail` 1-5 (default 4) sets distance, threshold, smallest share and gap. Every size is a share of the figure, so
  the same figure at another scale cuts identically. Without arguments it
  lists the regions with their share, centre, height and current colour. Recutting keeps the paint.
- References (`source_find`, `source_fetch`, `source_add`, `source_remove`, `source_look`): `source_add` takes a picture (kept as a
  PNG of at most 1400 px), a textured model or a folder (its best model, else up to 8 pictures); the page also takes
  dropped, pasted and uploaded pictures (`POST /api/projects/<id>/sources/upload`). A model (OBJ+MTL, GLB/glTF, DAE;
  FBX and `.blend` converted with Blender to a GLB with modifiers and the saved pose applied) is sampled into
  `samples` coloured points, laid over the figure (24 upright turns, any units, then point-to-point refinement with
  scale) and gets `fit` = median gap as a share of the figure's diagonal, `good` when under `fit_good`. Its picture is
  the model from four sides in its colours. `source_find` lists files under `reference_dirs` named like the figure. `source_fetch` (`references.py`) looks the
  character up on the web by the figure's name (catalogue number stripped) and collection: a collection named Pokémon
  asks the Pokémon artwork set (slug with -alola, -galar, -hisui, -paldea, -gmax, -mega from the name or folder, then
  the plain form); a collection with a wiki (built-in table plus the setting `wikis`) asks that wiki for the page of
  that title, following redirects, then its search; anything else asks the encyclopedia's search. The lead picture is
  downloaded (with the wiki as referer) and kept as an image source with `web: true` and the page as `path`.
- Colouring (`color_start`, `job_status`, `job_cancel`), one job per project in a background worker:
  - `mode: model`: each face takes the commonest colour among its 4 nearest samples of the model, the colours are
    reduced to `colors` (CIELAB k-means on colour bins with dampened weights, `accent`), borders are smoothed and
    small patches absorbed. The result reports the fit, how many colours the texture asks for and the share of
    faces far from the model. `palette_set count` chooses the colours again from the stored face colours.
  - `mode: ai`: a project with no sources first runs `source_fetch` (setting `web_references`). References go to the model one picture
    each (up to three, the user's own first, the looked-up one last). The vision model (the family's, or
    `llm_url`/`llm_model`) chooses the palette from the references and the clay figure and lists the
    character's parts with the palette index of each (the chart); is shown every region as a tile (figure in clay,
    that region in a marker colour, from the best of nine views, close-up for parts under 3 % of the tile), twelve
    per question in three pictures, and answers which part each is and its colour, with the chart in the question;
    regions flagged mixed, and the parts it says hold colours nobody received, are cut at a finer detail and asked
    again in close-up; small regions (under 1.2 % of the surface) whose border is 80 % or more with one other colour
    get a second look in close-up (feature of its own, or part of what surrounds it); then it reviews the coloured figure, plain and with region numbers (`rounds`, default
    `director_rounds`) and names the parts to change; colours left on no face are dropped. An answer that is not JSON
    is asked for once more; a failed review, idle-colour question or close-up is logged and skipped. `wish` is passed to every question. The log is `project.director`.
  - `mode: auto`: model when a source is `good`, else ai.
- Palette (`palette_set`): edit colours by position (hex, name), add colours, `count` (merge the most alike or add
  spare colours; from a model, re-reduce), `merge [keep, remove]`, `base` (swap with index 0). Up to 16 colours.
  Colours are not matched to materials: the app writes the digital file only.
- Paint (`paint`, `paint_undo`, `clean`): fill regions, the same-colour patch around faces, brush spots (points,
  radius in the figure's units, optional normals so the back of a thin part is not painted) or everything; `only_unset` limits it to
  faces without colour. 20 undo steps in memory per open project. `clean` smooths borders and absorbs patches whose area is under the
  square of `min_patch` % of the figure's diagonal (1.2 by default).
- Pictures (`figure_look`): `what` color, regions (numbered), clay, model (colours before reducing),
  compare (clay next to colour), sources; a named view, several views in a grid, `yaw`/`pitch`, or a close-up
  `region` [x, y, z, radius]. Rendered on the CPU by sampling the surface; back faces are culled on closed figures.
- Export (`result_export`): `bambu` (3MF laid out as Bambu Studio and OrcaSlicer save a project: the object as a
  component, its mesh in `3D/Objects/object_1.model` with `paint_color` per triangle, `Metadata/model_settings.config`;
  opened in OrcaSlicer 2.3.1), `prusa` (3MF with
  `slic3rpe:mmu_segmentation`), `color` (3MF materials extension colour group), `obj` (OBJ + MTL), `stl` (one STL of
  open surfaces per colour), plus `<figure> colores.txt`. Palette index 0 is colour 1 and carries no tag. The figure
  stands on the plate. Variants are read, each face takes the colour of the nearest face of
  the figure, faces the cut created take the colour around them. Default folder: `Multicolor` next to the figure, or
  `export_dir/<group>`. `GET /api/projects/<id>/download/<bambu|prusa|color>` sends one file to the browser.
- Studio (`static/`): library with photos and collection filter, projects, 3-D view with one colour per face, view
  modes, tools (orbit; piece bucket with the piece under the cursor lit, click fills and drag still orbits, Shift +
  click for the patch; patch bucket; brush sized in % of the figure; keys 1-9 choose the colour; Alt + click picker; undo), palette card,
  references card (drop, paste, upload, by path, find by name, look the character up on the web), region detail, what
  the model decided, export dialog, settings (folders, colours per figure, smallest patch in %, web references, review
  rounds, export folder, vision model).

## Cannot

- Pass colours from a textured model in a different pose than the figure: such a model is only a reference picture.
  `engine/pose.py` (`transfer`: parts of the model matched to regions by shape and size, each fitted on its own,
  abstaining when a match is not clear) exists and is tested on built shapes, but nothing calls it yet: it cannot
  tell two alike parts apart and has not been tried on real figures.
- Find a colour border that no fold marks (a pattern on a smooth surface): that needs the brush or a fitting model.
- Split triangles along a colour border: borders follow the mesh's own triangles.
- Carry material presets inside the 3MF (`export.write_3mf(project=True)` keeps an attempt OrcaSlicer rejects). The
  PrusaSlicer and standard-colour files have not been opened in their programs.
- Cut the figure into closed solids per colour, or add a figure's loose pieces to the same file.
- Choose between several web pictures of a character (it takes the lead one of the page), or read reference models
  inside archives.
- Keep undo steps after the app restarts.
- Open figures that are not STL.

## Where things are

- `nikias_hoard/engine/`: `mesh.py`, `regions.py`, `source.py`, `palette.py`, `labels.py`, `render.py`, `export.py`,
  `modelio.py`, `blender.py`, `blender_export.py`.
- `nikias_hoard/director.py`, `references.py`, `llm.py`, `services.py`, `agent_tools.py`, `api/`, `static/`, `store.py`, `config.py`.
- Data: `data/settings.json`, `data/projects/<id>/` (`project.json`, `mesh.npz`, `regions.npy`, `labels.npy`,
  `rgb.npy`, `figure.bin`, `src_<id>.png|npz|glb`), `data/thumbs/`, `data/skills/`, `data/mcp-token`.
- Tests: `tests/test_engine.py` (engine on a figure built by code), `tests/test_app.py` (the app over HTTP with a
  stand-in for the vision model).
