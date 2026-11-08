// Atlas case viewer: the assembly plus one view per pair of neighbouring layers, all on one
// shared camera, reloading whenever `cad` writes a new view/view.json.
// CAD frame is z-up mm; the GLBs are y-up (build123d's glTF export), possibly in metres.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const COLORS = { housing: 0x8d939d, switchplate: 0x3f72d6, pcb: 0x2f8a55, parts: 0xc9a227, bottomplate: 0xe07b39 };
const NAMES = { housing: "Housing", switchplate: "Switch plate", pcb: "PCB", parts: "PCB parts", bottomplate: "Bottom plate" };
// the pairs to inspect: each mating interface on its own
const PAIRS = [
  ["switchplate", "housing", "Switch plate · housing"],
  ["switchplate", "pcb", "Switch plate · PCB"],
  ["parts", "bottomplate", "PCB parts · bottom plate"],
  ["bottomplate", "housing", "Bottom plate · housing skirt"],
  ["switchplate", "bottomplate", "Thumb lip · bottom plate lock"],
];
const EXPLODE = { housing: 2, switchplate: 1, pcb: 0, parts: 0, bottomplate: -1 };
const TIGHT = 0.2;   // mm: passing gaps below this get an orange marker

const canvas = document.getElementById("gl");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
const DPR = Math.min(window.devicePixelRatio, 1.5);   // full retina x6 views is too slow
renderer.setPixelRatio(DPR);
renderer.localClippingEnabled = true;
const persp = new THREE.PerspectiveCamera(35, 1, 0.5, 5000);
const ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 5000);
let camera = persp;
let orthoHalf = 60;          // mm: half the visible height at zoom 1 in the ortho view
// the camera follows drags on the 3D views only: header, sidebar and sliders keep their clicks
const controls = new OrbitControls(camera, document.getElementById("app"));
controls.enableDamping = false;
let dirty = true;                       // render on demand, not every frame
const redraw = () => { dirty = true; };
controls.addEventListener("change", redraw);
window.addEventListener("resize", redraw);
for (const el of [document.querySelector("header"), document.getElementById("side")])
  for (const ev of ["pointerdown", "wheel", "contextmenu"])
    el.addEventListener(ev, (e) => e.stopPropagation());
const clip = new THREE.Plane(new THREE.Vector3(0, -1, 0), 6);   // keeps y < constant (CAD z)
const loader = new GLTFLoader();

let half = "left", manifest = null, version = null, meshes = {}, scale = 1, framed = false;
const views = [];

function makeScene() {
  const s = new THREE.Scene();
  s.add(new THREE.HemisphereLight(0xffffff, 0x445066, 1.6));
  const d = new THREE.DirectionalLight(0xffffff, 1.4); d.position.set(80, 160, 120); s.add(d);
  return s;
}

function addView(el, parts, opts = {}) {
  const v = { el, parts, scene: makeScene(), group: new THREE.Group(), sep: 0, markers: [], ...opts };
  v.scene.add(v.group);
  views.push(v);
  return v;
}

const main = addView(document.getElementById("main"), Object.keys(COLORS), { main: true });
let pairsOn = false;   // pair views cost a scene + render each: off unless asked for

const pairsEl = document.getElementById("pairs");
for (const [a, b, title] of PAIRS) {
  const el = document.createElement("div");
  el.className = "view";
  const hex = (n) => "#" + COLORS[n].toString(16).padStart(6, "0");
  el.innerHTML = `<div class="label">${title}<br><i style="background:${hex(a)}"></i>${NAMES[a]}` +
    `<i style="background:${hex(b)}"></i>${NAMES[b]}</div>` +
    `<div class="ctl">apart <input type="range" min="0" max="15" step="0.25" value="0"></div>`;
  pairsEl.appendChild(el);
  const v = addView(el, [a, b]);
  const slider = el.querySelector("input");
  slider.addEventListener("input", () => { v.sep = +slider.value; place(v); });
}
// sliders shouldn't spin the camera
document.querySelectorAll("input").forEach((i) => {
  i.addEventListener("pointerdown", (e) => e.stopPropagation());
  i.addEventListener("input", redraw);
});

// --- toggles ------------------------------------------------------------------------------
const visible = Object.fromEntries(Object.keys(COLORS).map((n) => [n, true]));
const toggles = document.getElementById("toggles");
for (const n of Object.keys(COLORS)) {
  const l = document.createElement("label");
  l.innerHTML = `<input type="checkbox" checked> ${NAMES[n]}`;
  l.querySelector("input").addEventListener("change", (e) => { visible[n] = e.target.checked; rebuildViews(); frame(true); });
  toggles.appendChild(l);
}
document.getElementById("explode").addEventListener("input", () => place(main));
document.getElementById("centre").addEventListener("click", () => frame(true));
document.getElementById("top").addEventListener("click", topView);
document.getElementById("persp").addEventListener("click", perspView);
document.getElementById("pairtoggle").addEventListener("change", (e) => {
  pairsOn = e.target.checked;
  document.getElementById("app").classList.toggle("pairs-on", pairsOn);
  rebuildViews(); redraw();
});
const secIn = document.getElementById("section");
secIn.addEventListener("input", () => {
  const z = +secIn.value;
  clip.constant = z; redraw();          // scene units are mm (meshes are scaled on load)
  document.getElementById("secv").textContent = z >= 6 ? "off" : `z ${z.toFixed(1)}`;
});
document.querySelectorAll("#halves button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#halves button").forEach((x) => x.classList.toggle("on", x === b));
  half = b.dataset.h; framed = false; load(true);
}));

// --- loading ------------------------------------------------------------------------------
async function poll() {
  try {
    const r = await fetch(`view/view.json?t=${Date.now()}`);
    if (r.ok) {
      const m = await r.json();
      if (m.version !== version) { manifest = m; version = m.version; await load(false); }
    }
  } catch (e) { document.getElementById("status").textContent = "waiting for `cad`…"; }
  setTimeout(poll, 1500);
}

// build state from view.py (absent when the page is served some other way): rebuilding,
// the last build's duration, or why it failed (the last good meshes stay up meanwhile)
let builtText = "";
async function pollStatus() {
  try {
    const r = await fetch(`status?half=${half}&t=${Date.now()}`);
    if (r.ok) showStatus(await r.json());
  } catch (e) { /* server restarting */ }
  setTimeout(pollStatus, 1000);
}

function showStatus(s) {
  const el = document.getElementById("status");
  const last = s.duration != null ? `last build ${s.duration.toFixed(0)} s` : "";
  if (s.state === "building") {
    el.innerHTML = `<span class="pill wait">rebuilding… ${Math.round(s.now - s.started)} s</span> ${last}`;
  } else if (s.state === "waiting") {
    el.innerHTML = `<span class="pill wait">change seen, waiting for quiet</span> ${last}`;
  } else if (s.state === "failed") {
    el.innerHTML = `<span class="pill fail" title="${esc(s.error || "")}">build failed: ${esc((s.error || "").slice(0, 120))}</span>` +
      ` ${builtText} (last good)`;
  } else {
    el.textContent = [builtText, last].filter(Boolean).join(" · ");
  }
}
const esc = (t) => t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function load(force) {
  if (!manifest || !manifest.halves[half]) return;
  const h = manifest.halves[half];
  const got = {};
  await Promise.all(h.parts.map(async (n) => {
    const g = await loader.loadAsync(`view/${half}-${n}.glb?v=${version}`);
    got[n] = g.scene;
  }));
  meshes = got;
  if (meshes.housing) {
    const size = new THREE.Box3().setFromObject(meshes.housing).getSize(new THREE.Vector3());
    scale = size.x < 1 ? 1000 : 1;   // glTF in metres -> mm
  }
  rebuildViews();
  showChecks(h.checks, h.skipped);
  builtText = `${half} built ${h.built || ""}`;
  if (!document.querySelector("#status .pill")) document.getElementById("status").textContent = builtText;
  if (!framed) frame();
}

// parts touch face to face (PCB underside on the bottom plate, plates on the housing):
// a depth bias per layer keeps the coincident faces from flickering (z-fighting)
const BIAS = { housing: -2, switchplate: -1, pcb: 0, parts: 0, bottomplate: 1 };
function material(n) {
  return new THREE.MeshStandardMaterial({ color: COLORS[n], metalness: 0.05, roughness: 0.7,
    side: THREE.DoubleSide, clippingPlanes: [clip],
    polygonOffset: true, polygonOffsetFactor: BIAS[n], polygonOffsetUnits: 4 * BIAS[n] });
}

function rebuildViews() {
  for (const v of views) {
    v.group.clear();
    v.objs = {};
    if (!v.main && !pairsOn) { v.markers?.forEach((m) => m.el.remove()); continue; }
    for (const n of v.parts) {
      if (!meshes[n] || !visible[n]) continue;
      const o = meshes[n].clone(true);
      const mat = material(n);
      o.traverse((c) => { if (c.isMesh) c.material = mat; });
      o.scale.setScalar(scale);
      v.objs[n] = o;
      v.group.add(o);
    }
    place(v);
  }
  updateMarkers();
}

function place(v) {
  redraw();
  const e = +document.getElementById("explode").value;
  for (const [n, o] of Object.entries(v.objs || {})) {
    let dy = 0;
    if (v.main) dy = EXPLODE[n] * e;
    else dy = n === v.parts[0] ? v.sep : 0;     // first of the pair lifts off the second
    o.position.y = dy;
  }
}

function setCamera(cam) {
  cam.position.copy(camera.position); cam.up.copy(camera.up); cam.zoom = 1;
  camera = cam; controls.object = cam;
}

// Top: orthographic, straight down (CAD -z), CAD +y up on screen; rotation locked so
// edges stay square-on for judging gaps. 3D: back to the perspective orbit.
function topView() {
  const box = new THREE.Box3().setFromObject(main.group);
  if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
  setCamera(ortho);
  camera.up.set(0, 0, -1);
  controls.target.copy(c);
  camera.position.set(c.x, c.y + size.length() * 2, c.z);
  camera.near = 0.1; camera.far = size.length() * 5;
  const r = main.el.getBoundingClientRect(), aspect = r.width / Math.max(r.height, 1);
  orthoHalf = Math.max(size.z / 2, size.x / 2 / aspect) * 1.08;   // whole model in view
  controls.enableRotate = false;
  controls.update(); redraw();
}
function perspView() {
  setCamera(persp);
  camera.up.set(0, 1, 0);
  controls.enableRotate = true;
  frame(false);
}

function frame(keepDir = false) {
  if (camera === ortho) { topView(); return; }
  const box = new THREE.Box3().setFromObject(main.group);
  if (box.isEmpty()) return;
  const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3()).length();
  const dir = keepDir ? camera.position.clone().sub(controls.target).normalize()
                      : new THREE.Vector3(0.15, 0.75, 0.65).normalize();
  controls.target.copy(c);           // orbit pivot = centre of the visible parts
  camera.position.copy(c).add(dir.multiplyScalar(s * 1.05));
  camera.near = s / 500; camera.far = s * 10; camera.updateProjectionMatrix();
  controls.update(); framed = true; redraw();
}

// --- checks -------------------------------------------------------------------------------
let checkList = [];
function cadToView(p) { return new THREE.Vector3(p[0], p[2], -p[1]); }   // z-up -> y-up

function showChecks(list, skipped) {
  const el = document.getElementById("checks"), sum = document.getElementById("summary");
  checkList = list || [];
  if (!list) { el.innerHTML = '<span class="pill wait">running…</span>'; sum.className = "pill wait";
    sum.textContent = "checks running"; updateMarkers(); return; }
  const bad = list.filter((r) => !r.ok);
  sum.className = "pill " + (bad.length ? "fail" : "ok");
  sum.textContent = bad.length ? `${bad.length} check${bad.length > 1 ? "s" : ""} failing` : `all ${list.length} checks ok`;
  el.innerHTML = "";
  if (skipped && skipped.length) {
    const d = document.createElement("div");
    d.className = "skipped";
    d.textContent = `fast build: skipped ${skipped.join(", ")} (run \`cad\` for all checks)`;
    el.appendChild(d);
  }
  for (const r of [...bad, ...list.filter((r) => r.ok)]) {
    const d = document.createElement("div");
    d.className = "chk";
    d.innerHTML = `<b class="${r.ok ? "ok" : "fail"}">${r.ok ? "ok" : "FAIL"}</b><span>${r.check}<small>${r.detail}</small></span>`;
    if (r.where) d.addEventListener("click", () => {
      const p = cadToView(r.where);
      const off = camera.position.clone().sub(controls.target).setLength(40);
      controls.target.copy(p); camera.position.copy(p).add(off); controls.update(); redraw();
    });
    el.appendChild(d);
  }
  updateMarkers();
}

function gapOf(r) { const m = /closest ([\d.]+) mm/.exec(r.detail); return m ? +m[1] : null; }

function updateMarkers() {
  redraw();
  for (const v of views) { v.markers.forEach((m) => m.el.remove()); v.markers = []; }
  for (const r of checkList) {
    if (!r.where) continue;
    const g = gapOf(r);
    const kind = !r.ok ? "fail" : g !== null && g < TIGHT ? "warn" : null;
    if (!kind) continue;
    for (const v of views) {
      if (!v.main && !pairsOn) continue;
      if (!v.main && r.parts.length && !r.parts.some((p) => v.parts.includes(p))) continue;
      const el = document.createElement("div");
      el.className = `marker ${kind}`;
      el.textContent = g !== null ? `${g.toFixed(2)} mm` : r.check;
      v.el.appendChild(el);
      v.markers.push({ el, pos: cadToView(r.where) });
    }
  }
}

// --- render -------------------------------------------------------------------------------
function render() {
  requestAnimationFrame(render);
  const w = window.innerWidth, h = window.innerHeight;
  if (canvas.width !== Math.floor(w * DPR) || canvas.height !== Math.floor(h * DPR)) {
    renderer.setSize(w, h, false); dirty = true;
  }
  if (!dirty) return;
  dirty = false;
  renderer.setScissorTest(false);
  renderer.clear();
  renderer.setScissorTest(true);
  for (const v of views) {
    if (!v.main && !pairsOn) continue;
    const r = v.el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const y = h - r.bottom;
    renderer.setViewport(r.left, y, r.width, r.height);
    renderer.setScissor(r.left, y, r.width, r.height);
    const aspect = r.width / r.height;
    if (camera === ortho) {
      camera.top = orthoHalf; camera.bottom = -orthoHalf;
      camera.left = -orthoHalf * aspect; camera.right = orthoHalf * aspect;
    } else camera.aspect = aspect;
    camera.updateProjectionMatrix();
    renderer.render(v.scene, camera);
    for (const m of v.markers) {
      const p = m.pos.clone().project(camera);
      m.el.style.display = p.z < 1 && Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1 ? "" : "none";
      m.el.style.left = `${(p.x + 1) / 2 * r.width}px`;
      m.el.style.top = `${(1 - p.y) / 2 * r.height}px`;
    }
  }
}
render();
poll();
pollStatus();
