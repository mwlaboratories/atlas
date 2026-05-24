// Atlas dev server — serves the layout webtool and runs the
// layout → ergogen → KiCad → render pipeline on demand.
//
// Endpoints:
//   GET  /                 → tools/layout/index.html
//   GET  /api/render.png   → most recent rendered top view (cached)
//   POST /api/build        → body: atlas layout state JSON
//                            response: { ok, log, pcbUrl, renderUrl }
//
// Run with: nix-shell -p nodejs_22 --run 'node tools/server.js'
// Then open http://localhost:8000

import express from 'express';
import { execFile } from 'node:child_process';
import { mkdir, writeFile, readFile, copyFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ergogen from 'ergogen';
import { layoutToErgogen } from './layout-to-ergogen.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const ERGOGEN_DIR = resolve(ROOT, 'tools/ergogen');
const OUT_DIR = resolve(ROOT, 'tools/ergogen/output');
const KICAD_DIR = resolve(ROOT, 'pcb/kicad');
const RENDERS_DIR = resolve(ROOT, 'tools/renders');
const LAYOUT_DIR = resolve(ROOT, 'tools/layout');
const FOOTPRINTS_DIR = resolve(ERGOGEN_DIR, 'footprints');

const PORT = process.env.PORT || 8000;

const app = express();
app.use(express.json({ limit: '1mb' }));
app.use(express.static(LAYOUT_DIR));
app.use('/renders', express.static(RENDERS_DIR));

// Load and register the vendored ceoloide footprints with ergogen's JS API.
async function loadFootprints() {
  const { readdir } = await import('node:fs/promises');
  // ceoloide footprints are CommonJS (`module.exports = ...`). Load via
  // createRequire — package.json no longer declares "type": "module" so
  // .js files default to CommonJS and require() works directly.
  const { createRequire } = await import('node:module');
  const require = createRequire(import.meta.url);
  const files = (await readdir(FOOTPRINTS_DIR)).filter(f => f.endsWith('.js'));
  for (const f of files) {
    const name = f.replace(/\.js$/, '');
    const mod = require(`${FOOTPRINTS_DIR}/${f}`);
    ergogen.inject('footprint', name, mod);
  }
}

await loadFootprints();

function runCmd(cmd, args, cwd = ROOT) {
  return new Promise((res, rej) => {
    execFile(cmd, args, { cwd, env: process.env }, (err, stdout, stderr) => {
      if (err) rej(Object.assign(err, { stdout, stderr }));
      else res({ stdout, stderr });
    });
  });
}

async function buildPipeline(yamlText) {
  const lines = [];
  const log = (...m) => lines.push(m.join(' '));

  log('1/4 running ergogen...');
  const results = await ergogen.process(yamlText, true, log);
  if (!results?.pcbs?.keyboard) {
    throw new Error('ergogen produced no pcbs.keyboard output');
  }

  await mkdir(`${OUT_DIR}/pcbs`, { recursive: true });
  await mkdir(KICAD_DIR, { recursive: true });
  const pcbPath = `${OUT_DIR}/pcbs/keyboard.kicad_pcb`;
  await writeFile(pcbPath, results.pcbs.keyboard);
  await copyFile(pcbPath, `${KICAD_DIR}/keyboard.kicad_pcb`);
  log('2/4 wrote pcb/kicad/keyboard.kicad_pcb');

  await mkdir(RENDERS_DIR, { recursive: true });
  await runCmd('kicad-cli', [
    'pcb', 'render',
    '--side', 'top',
    '--width', '1600', '--height', '1100',
    '--quality', 'high',
    '--output', `${RENDERS_DIR}/atlas-top.png`,
    `${KICAD_DIR}/keyboard.kicad_pcb`,
  ]);
  log('3/4 rendered top view → tools/renders/atlas-top.png');

  await runCmd('kicad-cli', [
    'pcb', 'render',
    '--side', 'bottom',
    '--width', '1600', '--height', '1100',
    '--quality', 'high',
    '--output', `${RENDERS_DIR}/atlas-bottom.png`,
    `${KICAD_DIR}/keyboard.kicad_pcb`,
  ]);
  log('4/4 rendered bottom view → tools/renders/atlas-bottom.png');

  return { log: lines };
}

app.post('/api/build', async (req, res) => {
  try {
    const state = req.body?.state;
    if (!state) {
      return res.status(400).json({ ok: false, error: 'missing state' });
    }
    const yamlText = layoutToErgogen(state);
    // Also save the YAML for inspection / debugging.
    await writeFile(`${ERGOGEN_DIR}/generated.yaml`, yamlText);
    const out = await buildPipeline(yamlText);
    res.json({
      ok: true,
      log: out.log,
      yamlUrl: '/api/generated.yaml',
      renderTopUrl: `/renders/atlas-top.png?t=${Date.now()}`,
      renderBottomUrl: `/renders/atlas-bottom.png?t=${Date.now()}`,
    });
  } catch (e) {
    res.status(500).json({
      ok: false,
      error: String(e?.message || e),
      stderr: e?.stderr || '',
      stdout: e?.stdout || '',
    });
  }
});

app.get('/api/generated.yaml', async (_req, res) => {
  try {
    res.type('text/plain');
    res.send(await readFile(`${ERGOGEN_DIR}/generated.yaml`, 'utf-8'));
  } catch (e) {
    res.status(404).send('no generated.yaml yet — click Build PCB first');
  }
});

app.listen(PORT, () => {
  console.log(`atlas dev server on http://localhost:${PORT}`);
  console.log(`  GET  /                 webtool UI`);
  console.log(`  POST /api/build        run ergogen + render pipeline`);
  console.log(`  GET  /api/generated.yaml   last generated ergogen yaml`);
  console.log(`  GET  /renders/atlas-{top,bottom}.png   rendered views`);
});
