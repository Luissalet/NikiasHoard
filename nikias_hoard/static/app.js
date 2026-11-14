// Nikias's Hoard studio: library, the 3-D view with one colour per face, palette, references and exports.
import * as THREE from 'three';
import { OrbitControls } from '/vendor/three/OrbitControls.js';
import { $, el, api, toast, fail, modal, confirmBox, nf, store, isTyping } from '/ui.js';

const UNSET = 255;
const state = { library: null, project: null, settings: null, status: null, what: 'color', tool: 'orbit', color: 0, job: null,
  labels: null, regions: null, group: store('group') || '' };

const call = (name, args = {}) => api('/api/ui/call', { method: 'POST', body: { name, arguments: args } });
const hexRgb = (hex) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];
const lin = (v) => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };

// ------------------------------------------------------------------ 3-D view
const view = (() => {
  const host = $('viewport');
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  host.append(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 100000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.12;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x3a3640, 1.25));
  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  scene.add(key);
  const material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.78, metalness: 0.0, side: THREE.DoubleSide });
  const ray = new THREE.Raycaster();
  let mesh = null, colors = null, centroids = null, normals = null, faces = 0, radius = 1, dirty = true;

  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    dirty = true;
  }
  new ResizeObserver(resize).observe(host);
  controls.addEventListener('change', () => { dirty = true; });
  (function loop() {
    requestAnimationFrame(loop);
    controls.update();
    if (!dirty) return;
    dirty = false;
    key.position.copy(camera.position).add(new THREE.Vector3(0, 0, camera.position.length() * 0.6));
    renderer.render(scene, camera);
  })();

  // figure.bin: "NKS1", point count, face count, points (float32 xyz), faces (uint32 abc)
  function show(buffer) {
    clear();
    const head = new Uint32Array(buffer, 4, 2);
    const nv = head[0], nf_ = head[1];
    const P = new Float32Array(buffer, 12, nv * 3);
    const F = new Uint32Array(buffer, 12 + nv * 12, nf_ * 3);
    const vn = new Float32Array(nv * 3);
    const pos = new Float32Array(nf_ * 9), nor = new Float32Array(nf_ * 9);
    centroids = new Float32Array(nf_ * 3); normals = new Float32Array(nf_ * 3);
    for (let f = 0; f < nf_; f++) {
      const a = F[3 * f] * 3, b = F[3 * f + 1] * 3, c = F[3 * f + 2] * 3;
      const ux = P[b] - P[a], uy = P[b + 1] - P[a + 1], uz = P[b + 2] - P[a + 2];
      const vx = P[c] - P[a], vy = P[c + 1] - P[a + 1], vz = P[c + 2] - P[a + 2];
      const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      vn[a] += nx; vn[a + 1] += ny; vn[a + 2] += nz; vn[b] += nx; vn[b + 1] += ny; vn[b + 2] += nz; vn[c] += nx; vn[c + 1] += ny; vn[c + 2] += nz;
      const len = Math.hypot(nx, ny, nz) || 1;
      normals[3 * f] = nx / len; normals[3 * f + 1] = ny / len; normals[3 * f + 2] = nz / len;
      for (let k = 0; k < 3; k++) centroids[3 * f + k] = (P[a + k] + P[b + k] + P[c + k]) / 3;
      pos.set(P.subarray(a, a + 3), 9 * f); pos.set(P.subarray(b, b + 3), 9 * f + 3); pos.set(P.subarray(c, c + 3), 9 * f + 6);
    }
    for (let f = 0; f < nf_; f++) {
      for (let k = 0; k < 3; k++) {
        const v = F[3 * f + k] * 3;
        const len = Math.hypot(vn[v], vn[v + 1], vn[v + 2]) || 1;
        nor[9 * f + 3 * k] = vn[v] / len; nor[9 * f + 3 * k + 1] = vn[v + 1] / len; nor[9 * f + 3 * k + 2] = vn[v + 2] / len;
      }
    }
    faces = nf_;
    colors = new Float32Array(nf_ * 9).fill(0.6);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.computeBoundingSphere();
    mesh = new THREE.Mesh(geo, material);
    scene.add(mesh);
    const s = geo.boundingSphere;
    radius = s.radius;
    const d = s.radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05;
    controls.target.copy(s.center);
    camera.position.copy(s.center).add(new THREE.Vector3(0.5, -0.78, 0.36).normalize().multiplyScalar(d));
    camera.near = s.radius / 400; camera.far = s.radius * 60;
    camera.updateProjectionMatrix();
    dirty = true;
  }

  // table: Float32Array of linear rgb per index; value: per-face index array
  function paint(value, table, fallback) {
    if (!mesh) return;
    for (let f = 0; f < faces; f++) {
      const k = value ? value[f] * 3 : -1;
      const r = k >= 0 && k < table.length ? table[k] : fallback[0], g = k >= 0 && k < table.length ? table[k + 1] : fallback[1],
        b = k >= 0 && k < table.length ? table[k + 2] : fallback[2];
      const o = 9 * f;
      colors[o] = colors[o + 3] = colors[o + 6] = r; colors[o + 1] = colors[o + 4] = colors[o + 7] = g; colors[o + 2] = colors[o + 5] = colors[o + 8] = b;
    }
    mesh.geometry.attributes.color.needsUpdate = true;
    dirty = true;
    lit = null;
    if (litFaces) highlight(litFaces);
  }

  // the piece under the cursor, shown lighter so a click's reach is seen before clicking
  let lit = null, litFaces = null;
  function highlight(list) {
    if (!mesh) return;
    if (lit) { for (let n = 0; n < lit.faces.length; n++) colors.set(lit.saved.subarray(9 * n, 9 * n + 9), 9 * lit.faces[n]); lit = null; }
    litFaces = list && list.length ? list : null;
    if (litFaces) {
      const saved = new Float32Array(9 * litFaces.length);
      for (let n = 0; n < litFaces.length; n++) {
        const o = 9 * litFaces[n];
        saved.set(colors.subarray(o, o + 9), 9 * n);
        for (let k = 0; k < 9; k++) colors[o + k] = colors[o + k] * 0.55 + 0.45;
      }
      lit = { faces: litFaces, saved };
    }
    mesh.geometry.attributes.color.needsUpdate = true;
    dirty = true;
  }

  function hit(ev) {
    if (!mesh) return null;
    const box = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((ev.clientX - box.left) / box.width) * 2 - 1, -((ev.clientY - box.top) / box.height) * 2 + 1), camera);
    const got = ray.intersectObject(mesh, false)[0];
    return got ? { face: got.faceIndex, point: [got.point.x, got.point.y, got.point.z],
      normal: [normals[3 * got.faceIndex], normals[3 * got.faceIndex + 1], normals[3 * got.faceIndex + 2]] } : null;
  }

  // faces whose centre is within radius of the point and look the same way: what the server's brush paints
  function near(point, r, normal) {
    const out = [];
    const r2 = r * r;
    for (let f = 0; f < faces; f++) {
      const dx = centroids[3 * f] - point[0], dy = centroids[3 * f + 1] - point[1], dz = centroids[3 * f + 2] - point[2];
      if (dx * dx + dy * dy + dz * dz > r2) continue;
      if (normals[3 * f] * normal[0] + normals[3 * f + 1] * normal[1] + normals[3 * f + 2] * normal[2] <= -0.2) continue;
      out.push(f);
    }
    return out;
  }

  function setTool(tool) {
    // only the brush needs the drag for itself: with the bucket tools a click paints and a drag still turns the figure
    controls.mouseButtons = tool === 'brush' ? { LEFT: null, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE }
      : { LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN };
    host.classList.toggle('painting', tool !== 'orbit');
    if (tool !== 'region') highlight(null);
  }
  function clear() { lit = null; litFaces = null; if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); mesh = null; dirty = true; } }
  return { show, paint, hit, near, highlight, setTool, clear, canvas: renderer.domElement, get radius() { return radius; }, get faces() { return faces; } };
})();

// ------------------------------------------------------------------ colours in the view
function regionTable(count) {
  const t = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const h = (i * 0.61803398875) % 1, s = [0.85, 0.6, 0.95][i % 3], v = [0.95, 0.8, 0.65][Math.floor(i / 3) % 3];
    const k = Math.floor(h * 6) % 6, f = h * 6 - Math.floor(h * 6), p = v * (1 - s), q = v * (1 - f * s), u = v * (1 - (1 - f) * s);
    const rgb = [[v, u, p], [q, v, p], [p, v, u], [p, q, v], [u, p, v], [v, p, q]][k];
    t[3 * i] = lin(rgb[0] * 255); t[3 * i + 1] = lin(rgb[1] * 255); t[3 * i + 2] = lin(rgb[2] * 255);
  }
  return t;
}

function repaint() {
  const p = state.project;
  if (!p) return;
  const clay = [lin(206), lin(196), lin(184)], unset = [lin(150), lin(146), lin(142)];
  if (state.what === 'clay') { view.paint(null, new Float32Array(0), clay); return; }
  if (state.what === 'regions') { view.paint(state.regions, regionTable(p.regions || 1), clay); return; }
  const t = new Float32Array(p.palette.length * 3);
  p.palette.forEach((c, k) => {
    const rgb = hexRgb(c.hex);
    t[3 * k] = lin(rgb[0]); t[3 * k + 1] = lin(rgb[1]); t[3 * k + 2] = lin(rgb[2]);
  });
  view.paint(state.labels, t, unset);
}

async function loadFaces(which = 'both') {
  const pid = state.project.id;
  if (which !== 'regions') state.labels = new Uint8Array(await (await fetch(`/api/projects/${pid}/faces/labels.bin`)).arrayBuffer());
  if (which !== 'labels') state.regions = new Uint16Array(await (await fetch(`/api/projects/${pid}/faces/regions.bin`)).arrayBuffer());
  repaint();
}

// ------------------------------------------------------------------ library
async function loadLibrary(refresh = false) {
  try {
    state.library = await call('library_scan', { refresh, limit: 500, query: $('lib-search').value.trim(), group: state.group });
    drawLibrary();
  } catch (err) { fail(err); }
}

function drawLibrary() {
  const lib = state.library, list = $('lib-list'), groups = $('lib-group');
  const names = Object.keys(lib.groups).sort((a, b) => a.localeCompare(b, 'es'));
  const tops = [...new Set(names.flatMap((g) => { const parts = g.split('/'); return parts.map((_, i) => parts.slice(0, i + 1).join('/')); }))].sort((a, b) => a.localeCompare(b, 'es'));
  groups.replaceChildren(el('option', { value: '' }, `Todas las colecciones (${nf.format(Object.values(lib.groups).reduce((a, b) => a + b, 0))})`),
    ...tops.map((g) => el('option', { value: g, selected: g === state.group }, `${'  '.repeat(g.split('/').length - 1)}${g.split('/').pop()}`)));
  list.replaceChildren();
  if (!lib.dirs.length) {
    list.append(el('div', { class: 'hint' }, 'No hay carpetas de figuras. Añade la carpeta donde guardas los STL en Ajustes.'));
    return;
  }
  for (const m of lib.missing) list.append(el('div', { class: 'hint' }, `No encuentro la carpeta ${m}.`));
  if (!lib.entries.length) list.append(el('div', { class: 'hint' }, 'Ninguna figura coincide.'));
  let group = null;
  for (const e of lib.entries) {
    if (e.group !== group) { group = e.group; list.append(el('div', { class: 'group', text: group })); }
    const extra = [e.variants.length ? `${e.variants.length} variante${e.variants.length > 1 ? 's' : ''}` : '', e.parts.length ? `${e.parts.length} pieza${e.parts.length > 1 ? 's' : ''}` : ''].filter(Boolean).join(' · ');
    list.append(el('button', { class: `item${state.project && state.project.source.path === e.path ? ' active' : ''}`, type: 'button', title: e.path, onclick: () => openEntry(e) },
      e.photo ? el('img', { class: 'thumb', loading: 'lazy', alt: '', src: `/api/library/photo?name=${encodeURIComponent(e.name)}` }) : el('span', { class: 'thumb' }),
      el('span', { class: 'name' }, e.name, el('br'), el('small', { text: extra || `${e.size_mb} MB` })),
      e.project_id ? el('span', { class: 'tag done', text: 'abierta' }) : null));
  }
  if (lib.count > lib.entries.length) list.append(el('div', { class: 'hint' }, `Se muestran ${lib.entries.length} de ${nf.format(lib.count)}: afina la búsqueda.`));
}

async function openEntry(e) {
  if (e.project_id) return openProject(e.project_id);
  $('loading').hidden = false; $('loading').textContent = `Abriendo ${e.name}: soldando la malla y cortándola en regiones…`;
  try {
    const p = await call('project_create', { source: e.path });
    e.project_id = p.id;
    await setProject(p, true);
    loadProjects();
  } catch (err) { fail(err); } finally { $('loading').hidden = true; }
}

async function loadProjects() {
  try {
    const { projects } = await call('project_list');
    $('project-list').replaceChildren(...(projects.length ? projects.map((p) => el('div', { class: `item${state.project && state.project.id === p.id ? ' active' : ''}` },
      el('button', { class: 'item', type: 'button', onclick: () => openProject(p.id) },
        el('span', { class: 'name' }, p.name, el('br'), el('small', { text: `${p.group} · ${p.palette.length ? `${p.palette.length} colores` : 'sin colorear'}` }))),
      el('button', { class: 'btn small danger', type: 'button', title: 'Borrar el proyecto (la figura no se toca)', onclick: () => removeProject(p) }, '×')))
      : [el('div', { class: 'hint' }, 'Todavía no has abierto ninguna figura.')]));
  } catch (err) { fail(err); }
}

async function removeProject(p) {
  if (!await confirmBox('Borrar proyecto', `Se borra el coloreado de «${p.name}». El STL y lo exportado no se tocan.`, 'Borrar', true)) return;
  try {
    await call('project_delete', { project_id: p.id });
    if (state.project && state.project.id === p.id) { state.project = null; view.clear(); $('right').hidden = true; $('empty').hidden = false; $('project-title').textContent = ''; refreshButtons(); }
    loadProjects(); loadLibrary();
  } catch (err) { fail(err); }
}

async function openProject(id) {
  $('loading').hidden = false; $('loading').textContent = 'Cargando la figura…';
  try { await setProject(await call('project_get', { project_id: id }), true); } catch (err) { fail(err); } finally { $('loading').hidden = true; }
}

// ------------------------------------------------------------------ project
async function setProject(p, loadMesh = false) {
  const changed = !state.project || state.project.id !== p.id;
  state.project = p;
  store('last', p.id);
  if (state.color >= p.palette.length) state.color = 0;
  $('project-title').textContent = `${p.name} · ${p.source.group}`;
  $('empty').hidden = true; $('right').hidden = false;
  if (loadMesh || changed) {
    const buffer = await (await fetch(`/api/projects/${p.id}/figure.bin`)).arrayBuffer();
    view.show(buffer);
    await loadFaces();
    state.over = null;
    if (p.palette.length && state.tool === 'orbit') setTool('region');          // a coloured figure opens ready to be corrected by clicking
  } else repaint();
  drawPalette(); drawRefs(); drawDirector(); drawResult(); refreshButtons();
  $('reg-detail').value = String(p.detail || 3);
  $('reg-count').textContent = `${p.regions} regiones`;
  document.querySelectorAll('#lib-list .item').forEach((n) => n.classList.toggle('active', n.title === p.source.path));
}

async function refreshProject(faces = 'labels') {
  if (!state.project) return;
  const p = await call('project_get', { project_id: state.project.id });
  state.project = p;
  if (faces) await loadFaces(faces);
  await setProject(p);
}

function refreshButtons() {
  const p = state.project, busy = !!state.job;
  const model = state.status && state.status.model && state.status.model.available;
  $('btn-color').disabled = !p || busy || !p.sources.length;
  $('btn-color-ai').disabled = !p || busy || !model;
  $('btn-color-ai').title = model ? 'El modelo de visión lee las referencias, elige la paleta, reparte los colores por regiones y revisa su resultado'
    : `No hay modelo de visión disponible${state.status && state.status.model ? `: ${state.status.model.reason || ''}` : ''}`;
  $('btn-clean').disabled = !p || busy || !p.palette.length;
  $('btn-export').disabled = !p || busy || !p.palette.length;
  $('btn-undo').disabled = !p || busy || !p.can_undo;
}

function drawResult() {
  const p = state.project, bar = $('resultbar');
  if (!p) { bar.replaceChildren(); return; }
  const m = p.mesh;
  const bits = [el('span', {}, el('b', { text: nf.format(m.faces) }), ' triángulos'), el('span', {}, 'proporción ', el('b', { text: m.size_mm.map((v) => (v / Math.max(...m.size_mm)).toFixed(2)).join(' × ') }))];
  if (m.open_edges) bits.push(el('span', { class: 'warn', text: `${nf.format(m.open_edges)} bordes abiertos` }));
  if (p.palette.length) {
    bits.push(el('span', { class: p.colored_share >= 0.999 ? 'ok' : 'warn', text: p.colored_share >= 0.999 ? 'Toda la figura tiene color' : `${(100 * (1 - p.colored_share)).toFixed(1)} % sin colorear` }));
    const tiny = p.colors.filter((c) => c.patches > 1).map((c) => `${p.palette[c.index].name}: ${c.patches} manchas`);
    if (tiny.length) bits.push(el('span', { text: tiny.join(' · ') }));
  }
  if (p.method === 'model' && p.transfer) {
    bits.push(el('span', { class: p.transfer.good ? 'ok' : 'warn', text: p.transfer.good ? `Color pasado del modelo (encaje ${(p.transfer.fit * 100).toFixed(2)} %)` : `El modelo no encaja bien (${(p.transfer.fit * 100).toFixed(1)} %): revisa el resultado` }));
    if (p.transfer.suggested_colors && p.transfer.suggested_colors !== p.palette.length) bits.push(el('span', { text: `La textura pide ${p.transfer.suggested_colors} colores` }));
  }
  if (p.exports && p.exports.length) bits.push(el('span', { title: p.exports[0].files.join('\n'), text: `Exportado en ${p.exports[0].folder}` }));
  bar.replaceChildren(...bits);
}

// ------------------------------------------------------------------ palette
function drawPalette() {
  const p = state.project, list = $('pal-list');
  $('pal-count').textContent = p.palette.length ? `${p.palette.length} colores · el 1 es el de la pieza` : '';
  $('pal-n').value = p.palette.length || '';
  if (!p.palette.length) {
    list.replaceChildren(el('div', { class: 'hint' }, 'Sin colores todavía. Añade referencias y pulsa Colorear, o crea la paleta a mano con «Añadir color».'));
    return;
  }
  list.replaceChildren(...p.palette.map((c, k) => {
    const st = p.colors[k] || { share: 0 };
    const name = el('input', { type: 'text', value: c.name || '', maxlength: '40', 'aria-label': `Nombre del color ${k + 1}`,
      onchange: (ev) => savePalette(k, { name: ev.target.value.trim() || c.name }) });
    return el('div', { class: `swatch${k === state.color ? ' active' : ''}`, onclick: (ev) => { if (ev.target.tagName !== 'INPUT' && ev.target.tagName !== 'BUTTON') { state.color = k; drawPalette(); } } },
      el('span', { class: 'num', title: 'Color activo para pintar', text: String(k + 1) }),
      el('input', { type: 'color', value: c.hex, 'aria-label': `Color ${k + 1}`, onchange: (ev) => savePalette(k, { hex: ev.target.value }) }),
      el('span', { class: 'sname' }, name, c.where ? el('span', { class: 'spool', title: c.where, text: c.where }) : null),
      el('span', { class: 'share', text: `${(100 * st.share).toFixed(1)} %` }),
      el('button', { class: 'btn small more', type: 'button', title: 'Fundir con otro color o hacerlo el de la pieza', onclick: () => colorMenu(k) }, '⋯'));
  }));
}

async function savePalette(k, change) {
  const colors = state.project.palette.map((_, i) => (i === k ? change : null));
  try { await setProject(await call('palette_set', { project_id: state.project.id, colors })); } catch (err) { fail(err); }
}

async function colorMenu(k) {
  const p = state.project, c = p.palette[k];
  const others = p.palette.map((o, i) => ({ o, i })).filter((x) => x.i !== k);
  const into = el('select', { class: 'input', name: 'into' }, el('option', { value: '' }, 'No fundir'), ...others.map((x) => el('option', { value: String(x.i) }, `${x.i + 1} · ${x.o.name}`)));
  const base = el('input', { type: 'checkbox', name: 'base', disabled: k === 0, checked: k === 0 });
  const r = await modal({ title: `Color ${k + 1} · ${c.name}`, body: el('div', { class: 'menu' },
    el('label', { class: 'field' }, el('span', { text: 'Fundir con (este desaparece)' }), into),
    el('label', { class: 'check' }, base, el('span', { text: 'Es el color de la pieza (color 1)' }))),
  buttons: [{ label: 'Cancelar', value: null }, { label: 'Aplicar', value: 'ok', kind: 'primary' }] });
  if (!r) return;
  try {
    let out = null;
    if (into.value !== '') out = await call('palette_set', { project_id: p.id, merge: [Number(into.value), k] });
    else if (base.checked && k !== 0) out = await call('palette_set', { project_id: p.id, base: k });
    if (out) { state.project = out; await loadFaces('labels'); await setProject(out); }
  } catch (err) { fail(err); }
}

// ------------------------------------------------------------------ references
function drawRefs() {
  const p = state.project;
  $('ref-count').textContent = p.sources.length ? `${p.sources.length}` : '';
  $('ref-list').replaceChildren(...p.sources.map((s) => el('div', { class: 'ref', title: s.path || s.name },
    el('img', { loading: 'lazy', alt: s.name, src: `/api/projects/${p.id}/sources/${s.id}.png`, onclick: () => showRef(s) }),
    s.kind === 'model' ? el('span', { class: `badge ${s.good ? 'good' : 'poor'}`, text: s.good ? `encaja ${(s.fit * 100).toFixed(1)} %` : `otra pose ${(s.fit * 100).toFixed(1)} %` }) : null,
    el('button', { class: 'x', type: 'button', title: 'Quitar esta referencia', onclick: () => removeRef(s) }, '×'))));
}

function showRef(s) {
  modal({ title: s.name, wide: true, body: [el('img', { class: 'sheet', src: `/api/projects/${state.project.id}/sources/${s.id}.png`, alt: s.name }),
    s.kind === 'model' ? el('p', { text: s.good ? 'El modelo coincide con la figura: su color se puede pasar directamente.' : s.note }) : null, s.path ? el('small', { text: s.path }) : null] });
}

async function removeRef(s) {
  try { await call('source_remove', { project_id: state.project.id, source_id: s.id }); await refreshProject(null); } catch (err) { fail(err); }
}

async function addRefPath(path) {
  $('loading').hidden = false; $('loading').textContent = 'Leyendo la referencia y colocándola sobre la figura…';
  try {
    const s = await call('source_add', { project_id: state.project.id, path });
    if (s.kind === 'model') toast(s.good ? 'El modelo encaja con la figura: Colorear pasará su color.' : 'El modelo está en otra pose: servirá de referencia para la IA.', s.good ? 'ok' : '', 6000);
    await refreshProject(null);
  } catch (err) { fail(err); } finally { $('loading').hidden = true; }
}

async function uploadImages(files) {
  for (const f of files) {
    if (!f.type.startsWith('image/')) continue;
    try { await api(`/api/projects/${state.project.id}/sources/upload?name=${encodeURIComponent(f.name.replace(/\.[^.]+$/, '') || 'referencia')}`, { method: 'POST', raw: f, type: f.type }); }
    catch (err) { fail(err); }
  }
  await refreshProject(null);
}

async function findRefs() {
  try {
    const found = await call('source_find', { project_id: state.project.id });
    if (!found.folders.length) { toast('No hay carpetas de referencias: añádelas en Ajustes.', 'err'); return; }
    if (!found.candidates.length) { toast(`Nada con el nombre «${found.query}» en tus carpetas de referencias.`); return; }
    const boxes = found.candidates.map((c) => el('label', {}, el('input', { type: 'checkbox', value: c.path }), el('b', { text: c.kind === 'model' ? 'modelo' : 'imagen' }), el('span', { title: c.path, text: c.path })));
    const r = await modal({ title: `Referencias para «${found.query}»`, wide: true, body: el('div', { class: 'cands' }, boxes),
      buttons: [{ label: 'Cancelar', value: null }, { label: 'Añadir las marcadas', value: 'ok', kind: 'primary' }] });
    if (!r) return;
    for (const b of boxes) { const input = b.querySelector('input'); if (input.checked) await addRefPath(input.value); }
  } catch (err) { fail(err); }
}

// ------------------------------------------------------------------ what the model decided
function drawDirector() {
  const d = state.project.director || {}, card = $('director-card');
  card.hidden = !(d.log && d.log.length);
  if (card.hidden) return;
  $('director-model').textContent = d.model || '';
  const pal = state.project.palette;
  const rows = d.log.map((e) => {
    if (e.step === 'palette') return el('div', { class: 'dstep' }, el('b', { text: e.character || 'Paleta' }), el('div', { class: 'dchg', text: e.palette.map((c) => `${c.name}${c.where ? `: ${c.where}` : ''}`).join(' · ') }), e.left_out ? el('div', { class: 'dchg', text: e.left_out }) : null);
    if (e.step === 'assign') return el('div', { class: 'dstep' }, el('b', { text: `${e.assigned} regiones repartidas` }), e.mixed.length ? el('div', { class: 'dchg', text: `Mezcladas: ${e.mixed.map((m) => `${m.region} (${m.why})`).join(' · ')}` }) : null, e.notes ? el('div', { class: 'dchg', text: e.notes }) : null);
    if (e.step === 'split') return el('div', { class: 'dstep' }, el('b', { text: `Región ${e.region}` }), el('div', { class: 'dchg', text: e.result || `partida en ${e.pieces} trozos` }));
    return el('div', { class: 'dstep' }, el('b', { text: `Revisión ${e.round}: ${e.ok ? 'conforme' : 'corrige'}` }),
      Object.keys(e.changes || {}).length ? el('div', { class: 'dchg', text: Object.entries(e.changes).map(([r, c]) => `región ${r} → ${pal[c] ? pal[c].name : c}`).join(' · ') }) : null,
      e.comment ? el('div', { class: 'dchg', text: e.comment }) : null);
  });
  $('director-log').replaceChildren(...rows);
}

// ------------------------------------------------------------------ jobs
function watchJob(job) {
  state.job = job.id;
  $('job').hidden = false;
  refreshButtons();
  const tick = async () => {
    let j;
    try { j = await call('job_status', { job_id: job.id }); } catch (err) { state.job = null; $('job').hidden = true; refreshButtons(); fail(err); return; }
    $('job-stage').textContent = j.stage;
    $('job-fill').style.width = `${Math.round(100 * j.progress)}%`;
    if (j.state === 'queued' || j.state === 'running') { setTimeout(tick, 900); return; }
    state.job = null; $('job').hidden = true;
    if (j.state === 'error') toast(j.error, 'err', 12000);
    else if (j.state === 'cancelled') toast('Trabajo cancelado.');
    else {
      const r = j.result || {};
      toast(r.method === 'model' ? `Color pasado del modelo «${r.source}»: ${r.colors} colores.` : `Coloreada por el modelo: ${r.colors} colores${r.character ? ` (${r.character})` : ''}.`, 'ok', 6000);
      if (state.what === 'clay' || state.what === 'regions') setWhat('color');
    }
    if (state.project && state.project.id === j.project_id) await refreshProject('labels');
    refreshButtons();
  };
  tick();
}

async function startColor(mode) {
  const p = state.project;
  const count = el('input', { type: 'number', class: 'input', min: '2', max: '16', step: '1', value: String(p.palette.length || state.settings.colors) });
  const wish = el('textarea', { class: 'input', rows: '2', maxlength: '500', placeholder: 'Opcional: «botas rojas», «sin blanco, no tengo»…' });
  const body = [el('label', { class: 'field' }, el('span', { text: 'Colores de impresión' }), count)];
  if (mode === 'ai') body.push(el('label', { class: 'field wide' }, el('span', { text: p.sources.length ? 'Qué quieres del coloreado' : 'Sin referencias: se buscará una imagen del personaje antes de colorear. Qué quieres del coloreado' }), wish));
  else if (!p.sources.some((s) => s.kind === 'model' && s.good)) body.push(el('p', { text: 'Ningún modelo de las referencias encaja con la figura: lo coloreará el modelo de visión a partir de ellas.' }));
  if (p.palette.length) body.push(el('p', { text: 'Sustituye el coloreado actual (Deshacer lo recupera).' }));
  const r = await modal({ title: mode === 'ai' ? 'Colorear con IA' : 'Colorear', body, buttons: [{ label: 'Cancelar', value: null }, { label: 'Colorear', value: 'ok', kind: 'primary' }] });
  if (!r) return;
  try { watchJob(await call('color_start', { project_id: p.id, mode, colors: Number(count.value) || 0, wish: wish.value.trim() })); } catch (err) { fail(err); }
}

async function exportDialog() {
  const p = state.project, s = state.settings;
  const kinds = [['bambu', '3MF pintado para Bambu Studio / OrcaSlicer'], ['prusa', '3MF pintado para PrusaSlicer'], ['color', '3MF de color estándar'], ['obj', 'OBJ con un material por color'], ['stl', 'Un STL por color (superficies, para guías de pintura)']];
  const boxes = kinds.map(([k, label]) => el('label', { class: 'check' }, el('input', { type: 'checkbox', value: k, checked: s.export_formats.includes(k) }), el('span', { text: label })));
  const variants = el('input', { type: 'checkbox', checked: true });
  const names = p.source.variants.map((v) => v.name).join(', ');
  const r = await modal({ title: 'Exportar', body: [...boxes, p.source.variants.length ? el('label', { class: 'check' }, variants, el('span', { text: `También las variantes con los mismos colores (${names})` })) : null,
    el('p', { text: s.export_dir ? `Se guarda en ${s.export_dir}.` : 'Se guarda en una carpeta «Multicolor» junto a la figura.' })],
  buttons: [{ label: 'Cancelar', value: null }, { label: 'Exportar', value: 'ok', kind: 'primary' }],
  validate: () => (boxes.some((b) => b.querySelector('input').checked) ? '' : 'Marca al menos un formato.') });
  if (!r) return;
  $('loading').hidden = false; $('loading').textContent = 'Escribiendo los archivos…';
  try {
    const out = await call('result_export', { project_id: p.id, formats: boxes.map((b) => b.querySelector('input')).filter((i) => i.checked).map((i) => i.value), variants: variants.checked });
    $('loading').hidden = true;
    await modal({ title: 'Exportado', body: [el('p', { text: out.folder }), el('div', { class: 'files', text: out.files.map((f) => f.split(/[\\/]/).pop()).join('\n') }),
      out.note ? el('p', { text: out.note }) : null, out.variants_failed.length ? el('p', { text: `No se pudieron leer: ${out.variants_failed.map((v) => v.name).join(', ')}` }) : null] });
    await refreshProject(null);
  } catch (err) { fail(err); } finally { $('loading').hidden = true; }
}

// ------------------------------------------------------------------ painting in the view
function setWhat(what) {
  state.what = what;
  document.querySelectorAll('#view-seg button').forEach((b) => b.classList.toggle('active', b.dataset.what === what));
  repaint();
}

function setTool(tool) {
  state.tool = tool;
  document.querySelectorAll('#tool-seg button').forEach((b) => b.classList.toggle('active', b.dataset.tool === tool));
  $('brush-box').hidden = tool !== 'brush';
  $('view-info').textContent = { orbit: '', region: 'Clic: pinta la pieza · arrastrar: girar · 1-9: color · Alt + clic: coger el color',
    patch: 'Clic: pinta la mancha · arrastrar: girar · Alt + clic: coger el color', brush: 'Botón derecho: girar · Alt + clic: coger el color' }[tool];
  view.setTool(tool);
}

function needColor() {
  if (state.project.palette.length) return true;
  toast('Primero hace falta al menos un color en la paleta.', 'err');
  return false;
}

let stroke = null, press = null, hover = 0;

// faces of each piece, built once per cut: what the bucket lights up and fills
function pieceFaces(r) {
  if (!state.pieces || state.pieces.of !== state.regions) {
    const lists = new Map();
    for (let f = 0; f < state.regions.length; f++) { const k = state.regions[f]; if (!lists.has(k)) lists.set(k, []); lists.get(k).push(f); }
    state.pieces = { of: state.regions, lists };
  }
  return state.pieces.lists.get(r) || [];
}

async function bucket(h, tool) {
  if (!needColor()) return;
  const pid = state.project.id, color = state.color;
  if (state.what === 'clay') setWhat('color');
  try {
    if (tool === 'region') {
      const r = state.regions[h.face];
      for (const f of pieceFaces(r)) state.labels[f] = color;
      repaint();
      await call('paint', { project_id: pid, color, regions: [r] });
      await refreshProject(null);
    } else {
      await call('paint', { project_id: pid, color, faces: [h.face] });
      await refreshProject('labels');
    }
  } catch (err) { fail(err); await refreshProject('labels'); }
}

view.canvas.addEventListener('pointerdown', (ev) => {
  press = null;
  if (ev.button !== 0 || state.tool === 'orbit' || !state.project || state.job) return;
  if (state.tool !== 'brush') { press = { x: ev.clientX, y: ev.clientY, alt: ev.altKey, shift: ev.shiftKey }; return; }
  const h = view.hit(ev);
  if (!h) return;
  if (ev.altKey) { const v = state.labels[h.face]; if (v !== UNSET) { state.color = v; drawPalette(); } return; }
  if (!needColor()) return;
  if (state.what === 'clay') setWhat('color');
  stroke = { points: [], normals: [], color: state.color, last: null };
  view.canvas.setPointerCapture(ev.pointerId);
  dab(h);
});

// a click (the pointer went up where it went down) paints; anything longer was turning the figure
view.canvas.addEventListener('pointerup', (ev) => {
  const p = press;
  press = null;
  if (!p || ev.button !== 0 || Math.hypot(ev.clientX - p.x, ev.clientY - p.y) > 5 || !state.project || state.job) return;
  const h = view.hit(ev);
  if (!h) return;
  if (p.alt) { const v = state.labels[h.face]; if (v !== UNSET) { state.color = v; drawPalette(); } return; }
  bucket(h, p.shift ? (state.tool === 'region' ? 'patch' : 'region') : state.tool);   // Shift: the other bucket, without changing tool
});

function showPiece(ev) {
  hover = 0;
  if (state.tool !== 'region' || !state.project || state.job || ev.buttons) { view.highlight(null); return; }
  const h = view.hit(ev);
  if (!h) { view.highlight(null); state.over = null; return; }
  const r = state.regions[h.face];
  if (state.over === r) return;
  state.over = r;
  view.highlight(pieceFaces(r));
}
view.canvas.addEventListener('pointerleave', () => { state.over = null; view.highlight(null); });

function dab(h) {
  const r = Number($('brush-size').value) / 100 * figureSize();
  if (stroke.last && Math.hypot(h.point[0] - stroke.last[0], h.point[1] - stroke.last[1], h.point[2] - stroke.last[2]) < r * 0.3) return;
  stroke.last = h.point;
  stroke.points.push(h.point.map((v) => Math.round(v * 1000) / 1000)); stroke.normals.push(h.normal.map((v) => Math.round(v * 1000) / 1000));
  for (const f of view.near(h.point, r, h.normal)) state.labels[f] = stroke.color;
  repaint();
}

view.canvas.addEventListener('pointermove', (ev) => {
  if (!stroke) { if (state.tool === 'region' && !hover) hover = requestAnimationFrame(() => showPiece(ev)); return; }
  const h = view.hit(ev);
  if (h) dab(h);
});

async function endStroke() {
  if (!stroke) return;
  const s = stroke;
  stroke = null;
  try {
    for (let i = 0; i < s.points.length; i += 300) {
      await call('paint', { project_id: state.project.id, color: s.color, brush: { points: s.points.slice(i, i + 300), normals: s.normals.slice(i, i + 300), radius_mm: Number($('brush-size').value) / 100 * figureSize() } });
    }
    await refreshProject('labels');
  } catch (err) { fail(err); await refreshProject('labels'); }
}
view.canvas.addEventListener('pointerup', endStroke);
view.canvas.addEventListener('pointercancel', endStroke);
view.canvas.addEventListener('contextmenu', (ev) => ev.preventDefault());

const SPARE = ['#f5f5f5', '#161616', '#c12e1f', '#f4ee2a', '#1f5fc4', '#00ae42', '#ff6a13', '#f1c6a5', '#8e9089', '#5e43b7', '#9d432c', '#f55a74'];
// the figure's size (its diagonal, in its own units): what the brush and the smallest patch are a share of
function figureSize() { const m = state.project && state.project.mesh; return m ? Math.hypot(...m.size_mm) : 1; }

// ------------------------------------------------------------------ settings
async function openSettings() {
  const s = state.settings = await call('settings_get');
  const area = (value, rows = 2) => el('textarea', { class: 'input', rows: String(rows) }, value.join('\n'));
  const lib = area(s.library_dirs), refs = area(s.reference_dirs), photos = area(s.photo_dirs);
  const colors = el('input', { type: 'number', class: 'input', min: '2', max: '16', value: String(s.colors) });
  const detail = el('input', { type: 'number', class: 'input', min: '0', max: '20', step: '0.1', value: String(s.min_patch) });
  const rounds = el('input', { type: 'number', class: 'input', min: '0', max: '6', value: String(s.director_rounds) });
  const out = el('input', { type: 'text', class: 'input', value: s.export_dir, placeholder: 'Vacío: carpeta «Multicolor» junto a cada figura' });
  const url = el('input', { type: 'text', class: 'input', value: s.llm_url, placeholder: 'Vacío: el modelo de visión de la familia' });
  const model = el('input', { type: 'text', class: 'input', value: s.llm_model, placeholder: 'Modelo (si el servidor lo pide)' });
  const web = el('input', { type: 'checkbox', checked: s.web_references });
  const lines = (t) => t.value.split('\n').map((v) => v.trim()).filter(Boolean);
  const st = state.status || {};
  const r = await modal({ title: 'Ajustes', wide: true, body: [
    el('label', { class: 'field wide' }, el('span', { text: 'Carpetas con tus figuras (STL), una por línea' }), lib),
    el('label', { class: 'field wide' }, el('span', { text: 'Carpetas donde buscar modelos con texturas e imágenes de referencia' }), refs),
    el('label', { class: 'field wide' }, el('span', { text: 'Carpetas con una foto por figura (mismo nombre)' }), photos),
    el('label', { class: 'field' }, el('span', { text: 'Colores por figura' }), colors),
    el('label', { class: 'field' }, el('span', { text: 'Mancha de color más pequeña (% del tamaño de la figura)' }), detail),
    el('label', { class: 'field' }, el('span', { text: 'Veces que el modelo revisa su coloreado' }), rounds),
    el('label', { class: 'field wide' }, el('span', { text: 'Carpeta de exportación' }), out),
    el('label', { class: 'field wide' }, el('span', { text: `Modelo de visión${st.model ? (st.model.available ? ` · ahora: ${st.model.model}` : ` · no disponible: ${st.model.reason}`) : ''}` }), url, model),
    el('label', { class: 'check wide' }, web, el('span', { text: 'Si una figura no tiene referencias, buscar en internet una imagen del personaje' })),
    el('small', { text: st.blender ? `Blender: ${st.blender}` : 'Blender no encontrado: hace falta para leer FBX y .blend.' })],
  buttons: [{ label: 'Cancelar', value: null }, { label: 'Guardar', value: 'ok', kind: 'primary' }] });
  if (!r) return;
  try {
    state.settings = await call('settings_set', { changes: { library_dirs: lines(lib), reference_dirs: lines(refs), photo_dirs: lines(photos), colors: Number(colors.value),
      min_patch: Number(detail.value), web_references: web.checked, director_rounds: Number(rounds.value), export_dir: out.value.trim(), llm_url: url.value.trim(), llm_model: model.value.trim() } });
    state.status = await api('/api/status');
    toast('Ajustes guardados.', 'ok');
    await loadLibrary(true);
    refreshButtons();
  } catch (err) { fail(err); }
}

// ------------------------------------------------------------------ wiring
document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
  $('pane-library').hidden = t.dataset.tab !== 'library'; $('pane-projects').hidden = t.dataset.tab !== 'projects';
  if (t.dataset.tab === 'projects') loadProjects();
}));
let searchTimer = null;
$('lib-search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadLibrary(), 250); });
$('lib-group').addEventListener('change', (ev) => { state.group = ev.target.value; store('group', state.group); loadLibrary(); });
$('lib-refresh').addEventListener('click', () => loadLibrary(true));
document.querySelectorAll('#view-seg button').forEach((b) => b.addEventListener('click', () => setWhat(b.dataset.what)));
document.querySelectorAll('#tool-seg button').forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));
$('brush-size').addEventListener('input', (ev) => { $('brush-mm').textContent = `${Number(ev.target.value).toFixed(1)} %`; });
$('btn-undo').addEventListener('click', async () => { try { await call('paint_undo', { project_id: state.project.id }); await refreshProject('labels'); } catch (err) { fail(err); } });
$('btn-color').addEventListener('click', () => startColor('auto'));
$('btn-color-ai').addEventListener('click', () => startColor('ai'));
$('btn-clean').addEventListener('click', async () => {
  try { const out = await call('clean', { project_id: state.project.id }); toast(out.islands_removed ? `${out.islands_removed} manchas pequeñas absorbidas.` : 'Bordes enderezados; no había manchas pequeñas.', 'ok'); await refreshProject('labels'); } catch (err) { fail(err); }
});
$('btn-export').addEventListener('click', exportDialog);
$('btn-settings').addEventListener('click', () => openSettings().catch(fail));
$('btn-skills').addEventListener('click', () => window.open('/api/agent/skills/page', '_blank', 'noopener'));
$('job-cancel').addEventListener('click', async () => { if (state.job) { try { await call('job_cancel', { job_id: state.job }); } catch (err) { fail(err); } } });
$('pal-add').addEventListener('click', async () => {
  const p = state.project;
  const used = p.palette.map((c) => c.hex);
  const next = SPARE.find((h) => !used.includes(h)) || '#808080';
  try { await setProject(await call('palette_set', { project_id: p.id, colors: [...p.palette.map(() => null), { hex: next }] })); state.color = state.project.palette.length - 1; drawPalette(); } catch (err) { fail(err); }
});
$('pal-n').addEventListener('change', async (ev) => {
  const n = Number(ev.target.value), p = state.project;
  if (!n || n === p.palette.length) return;
  if (n < p.palette.length && !await confirmBox('Menos colores', p.has_model_colors && p.method === 'model' ? `Se vuelven a elegir ${n} colores desde la textura del modelo.` : `Los colores más parecidos se funden hasta quedar ${n}.`, 'Reducir')) { ev.target.value = p.palette.length; return; }
  try { const out = await call('palette_set', { project_id: p.id, count: n }); state.project = out; await loadFaces('labels'); await setProject(out); } catch (err) { fail(err); }
});
$('reg-detail').addEventListener('change', async (ev) => {
  $('loading').hidden = false; $('loading').textContent = 'Cortando la figura en regiones…';
  try { await call('regions_set', { project_id: state.project.id, detail: Number(ev.target.value) }); await refreshProject('regions'); setWhat('regions'); } catch (err) { fail(err); } finally { $('loading').hidden = true; }
});
$('ref-find').addEventListener('click', findRefs);
$('ref-web').addEventListener('click', async () => {
  $('loading').hidden = false; $('loading').textContent = 'Buscando una imagen del personaje…';
  try {
    const found = await call('source_fetch', { project_id: state.project.id });
    toast(`Referencia añadida: ${found.name}. Si no es el personaje, quítala.`, 'ok', 6000);
    await refreshProject(null);
  } catch (err) { fail(err); } finally { $('loading').hidden = true; }
});
$('ref-path').addEventListener('click', async () => {
  const input = el('input', { type: 'text', class: 'input', placeholder: 'C:\\…\\modelo.fbx, una imagen o una carpeta', autocomplete: 'off' });
  const r = await modal({ title: 'Añadir referencia por ruta', body: [el('p', { text: 'Un modelo con texturas (OBJ, GLB, DAE, FBX, .blend), una imagen o una carpeta que los tenga.' }), input],
    buttons: [{ label: 'Cancelar', value: null }, { label: 'Añadir', value: 'ok', kind: 'primary' }], onOpen: () => input.focus(), validate: () => (input.value.trim() ? '' : 'Escribe una ruta.') });
  if (r) addRefPath(input.value.trim().replace(/^"|"$/g, ''));
});
$('ref-file').addEventListener('click', () => $('ref-input').click());
$('ref-input').addEventListener('change', (ev) => { uploadImages([...ev.target.files]); ev.target.value = ''; });
const drop = $('ref-drop');
['dragenter', 'dragover'].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((n) => drop.addEventListener(n, (ev) => { ev.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (ev) => { if (state.project) uploadImages([...ev.dataTransfer.files]); });
document.addEventListener('paste', (ev) => {
  if (!state.project || isTyping(ev)) return;
  const files = [...(ev.clipboardData ? ev.clipboardData.files : [])].filter((f) => f.type.startsWith('image/'));
  if (files.length) { ev.preventDefault(); uploadImages(files); }
});
document.addEventListener('keydown', (ev) => {
  if (isTyping(ev) || !state.project || document.querySelector('dialog[open]')) return;
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); $('btn-undo').click(); return; }
  const tools = { g: 'orbit', r: 'region', m: 'patch', p: 'brush' };
  if (tools[ev.key.toLowerCase()] && !ev.ctrlKey && !ev.metaKey) setTool(tools[ev.key.toLowerCase()]);
  const n = Number(ev.key);
  if (n >= 1 && n <= state.project.palette.length) { state.color = n - 1; drawPalette(); }
});

(async function boot() {
  try {
    [state.status, state.settings] = await Promise.all([api('/api/status'), call('settings_get')]);
  } catch (err) { fail(err); }
  refreshButtons();
  await loadLibrary();
  const last = store('last');
  if (last) { try { await setProject(await call('project_get', { project_id: last }), true); } catch { store('last', null); } }
  if (state.status && state.status.jobs && state.status.jobs.length) watchJob(state.status.jobs[0]);
})();
