// Convert atlas webtool's layout state → ergogen YAML.
//
// Input state shape (from tools/layout/index.html):
//   {
//     profile: 'choc-v1' | 'choc-v2',
//     pitchX: number,        // mm
//     useProfilePitch: bool,
//     cols: [{ keys, x, y, splay }, ...],
//     thumb: { count, x, y, rot, stepDist, stepAngle },
//     halfGap: number,       // u
//     trackpoint: null | { ... },
//   }
//
// atlas convention: x/y in u; y INCREASES DOWNWARD; col.x/col.y =
// center of BOTTOM-MOST key in the col. ergogen convention: y INCREASES
// UPWARD; rows ordered top-to-bottom in YAML get y=0, ky, 2ky, ...
//
// So we negate y deltas when going atlas → ergogen.

const PROFILE_PITCH_Y = {
  'choc-v1': 17,
  'choc-v2': 19.05,
};

const COL_NAMES = ['c0', 'c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7'];
const ROW_NAMES = ['r0', 'r1', 'r2', 'r3', 'r4', 'r5'];

function pad(n, depth = 0) {
  return '  '.repeat(depth) + n;
}

export function layoutToErgogen(state) {
  const ky = PROFILE_PITCH_Y[state.profile] ?? 19.05;
  const kx = state.useProfilePitch ? ky : (state.pitchX ?? ky);

  const maxKeys = state.cols.reduce((m, c) => Math.max(m, c.keys), 0);

  // Each column's anchor in atlas-u: x = col.x, y = col.y.
  // ergogen shift in mm (Y up): [col.x * kx, -col.y * ky]
  // BUT: we need to anchor to the BOTTOM-MOST key to match atlas semantics.
  // ergogen's `rows` list is ordered top-down in the YAML; their y starts at
  // 0 and increases by ky for each subsequent row in the list. So the FIRST
  // row in the YAML lives at the LOWEST ergogen-y → highest atlas-y → the
  // BOTTOM-most key. We list rows bot, home, top for a 3-row col.

  const lines = [];
  const p = (n, d = 0) => lines.push(pad(n, d));

  p('meta:');
  p('engine: 4.2.1', 1);
  p('name: atlas', 1);
  p('version: 0.1.0', 1);
  p('');
  p('units:');
  p(`kx: ${kx}`, 1);
  p(`ky: ${ky}`, 1);
  p('');
  p('points:');
  p('zones:', 1);
  p('matrix:', 2);
  p('anchor:', 3);
  p('shift: [80, 80]', 4);
  p('columns:', 3);

  state.cols.forEach((col, i) => {
    if (col.keys <= 0) return;
    const name = COL_NAMES[i] ?? `col${i}`;
    p(`${name}:`, 4);
    p('key:', 5);
    // Shift this column relative to default (which sits at +kx per column).
    // atlas col.x is absolute (in u); ergogen wants relative-to-previous
    // column. We compute it as the delta from default placement.
    const expectedX = i * kx; // mm
    const actualX = col.x * kx;
    const xShift = actualX - expectedX;
    // y in atlas is "bottom-key center, y-down"; we want bottom row at
    // y=0 in ergogen-relative terms. The col anchor in ergogen sits at
    // the bottom row by default if we order rows bot, home, top.
    const yShift = -col.y * ky + (col.keys - 1) * ky;
    // Actually simpler: shift column by [-col*kx, -col.y*ky] from origin
    // and let ergogen place rows at the bottom-anchored point.
    p(`shift: [${xShift.toFixed(3)}, ${(- col.y * ky).toFixed(3)}]`, 6);
    if (col.splay) {
      p(`rotate: ${-col.splay}`, 6); // atlas splay is CCW for +°, ergogen same
    }
    p(`column_net: COL${i}`, 6);
    p('rows:', 5);
    // Emit rows BOT → TOP so the bottom row lands at the column's anchor.
    for (let r = col.keys - 1; r >= 0; r--) {
      const rowName = ROW_NAMES[col.keys - 1 - r];
      p(`${rowName}: {row_net: ROW${col.keys - 1 - r}}`, 6);
    }
  });

  // Thumb cluster
  if (state.thumb?.count > 0) {
    p('thumbs:', 2);
    p('anchor:', 3);
    p('ref: matrix_' + (COL_NAMES[state.cols.length - 1] ?? 'c4') + '_r' + 0, 4);
    p(`shift: [${((state.thumb.x - (state.cols[state.cols.length - 1]?.x ?? 0)) * kx).toFixed(3)}, ${(-(state.thumb.y - (state.cols[state.cols.length - 1]?.y ?? 0)) * ky).toFixed(3)}]`, 4);
    if (state.thumb.rot) p(`rotate: ${state.thumb.rot}`, 4);
    p('columns:', 3);
    for (let i = 0; i < state.thumb.count; i++) {
      p(`t${i}:`, 4);
      p('key:', 5);
      if (i > 0) {
        const bisector = state.thumb.rot + state.thumb.stepAngle / 2 + (i - 1) * state.thumb.stepAngle;
        const bRad = bisector * Math.PI / 180;
        const dx = state.thumb.stepDist * Math.cos(bRad) * kx;
        const dy = state.thumb.stepDist * Math.sin(bRad) * ky;
        p(`shift: [${dx.toFixed(3)}, ${(-dy).toFixed(3)}]`, 6);
        p(`rotate: ${state.thumb.stepAngle}`, 6);
      }
      p(`column_net: TCOL${i}`, 6);
      p('rows:', 5);
      p('thumb: {row_net: TROW0}', 6);
    }
  }

  // Mirror — sibling of `zones:` under `points:`, depth 1 (2-space indent).
  p('mirror:', 1);
  const lastCol = state.cols[state.cols.length - 1];
  const halfWidth = (lastCol?.x ?? 4) * kx;
  // Approximate distance: 2 * half_width + halfGap × kx
  const mirrorDist = 2 * halfWidth + (state.halfGap ?? 0.5) * kx;
  p(`ref: matrix_${COL_NAMES[state.cols.length - 1] ?? 'c4'}_r0`, 2);
  p(`distance: ${mirrorDist.toFixed(3)}`, 2);

  // Outlines
  p('');
  p('outlines:');
  p('_keys:', 1);
  p('- what: rectangle', 2);
  p('  where: true', 2);
  p('  size: [18, 18]', 2);
  p('board:', 1);
  p('- what: outline', 2);
  p('  name: _keys', 2);
  p('  expand: 3', 2);
  p('  fillet: 2', 2);

  // PCB
  p('');
  p('pcbs:');
  p('keyboard:', 1);
  p('template: kicad8', 2);
  p('outlines:', 2);
  p('main:', 3);
  p('outline: board', 4);
  p('footprints:', 2);
  p('switch:', 3);
  p('what: switch_choc_v1_v2', 4);
  p('where: true', 4);
  p('params:', 4);
  p('from: "{{column_net}}"', 5);
  p('to: "{{colrow}}"', 5);
  p('reversible: false', 5);
  p('hotswap: true', 5);
  p('solder: false', 5);
  p('side: F', 5);
  p('diode:', 3);
  p('what: diode_tht_sod123', 4);
  p('where: true', 4);
  p('params:', 4);
  p('from: "{{colrow}}"', 5);
  p('to: "{{row_net}}"', 5);
  p('side: B', 5);
  p('reversible: false', 5);
  p('include_tht: false', 5);
  p('adjust:', 4);
  p('shift: [-6, -4]', 5);
  p('rotate: 90', 5);
  p('led:', 3);
  p('what: led_sk6812mini-e', 4);
  p('where: true', 4);
  p('params:', 4);
  p('P1: VCC', 5);
  p('P2: "led_dout_{{name}}"', 5);
  p('P3: GND', 5);
  p('P4: "led_din_{{name}}"', 5);
  p('side: B', 5);
  p('adjust:', 4);
  p('shift: [0, 4.93]', 5);

  return lines.join('\n') + '\n';
}
