/**
 * FluidField - Sfondo minimale, leggero e poetico (Zero Stutter, 60 FPS fissi)
 * Sostituisce il pesante Perlin noise a tutto schermo con:
 *  - Lanterne/particelle di Kanji e polvere stellare che fluttuano lentamente (e si spostano al passaggio del mouse)
 *  - Scia delicata del cursore
 *  - Onde circolari sottili e pulite al click
 *  - Pioggia verticale discreta di Kanji/codice ai margini quando lavora l'AI
 */
class FluidField {
  constructor() {
    this.kanjiSets = {
      companion: ['心', '光', '風', '空', '星', '和', '桜', '夢', '·', '˚', '+', '·'],
      ai: ['電', '脳', '知', '算', '創', '核', '零', '壱', '·', ':', '+', '◊'],
      coffee: ['茶', '暖', '香', '憩', '休', '和', '湯', '·', '˚', '~'],
      afk: ['夢', '眠', '夜', '星', '月', '静', '·', '˚', 'z'],
      focus: ['静', '禅', '道', '一', '心', '無', '·', '·']
    };

    this.shockwaves = [];
    this.trail = [];
    this.ambientParticles = [];
    this.aiColumns = [];

    this.initAmbientParticles(38);
    this.initAiColumns(18);
  }

  initAmbientParticles(count) {
    this.ambientParticles = [];
    for (let i = 0; i < count; i++) {
      this.ambientParticles.push({
        nx: Math.random(),
        ny: Math.random(),
        vx: 0,
        vy: 0,
        baseSpeedY: 0.008 + Math.random() * 0.014,
        phase: Math.random() * Math.PI * 2,
        glyphIdx: i,
        lum: (i % 3) + 1 // 1, 2 o 3 (molto soffuso e rilassante)
      });
    }
  }

  initAiColumns(count) {
    this.aiColumns = [];
    for (let i = 0; i < count; i++) {
      this.aiColumns.push({
        side: i % 2 === 0 ? 'left' : 'right',
        offsetRatio: Math.random() * 0.25 + 0.03,
        y: Math.random() * 40,
        speed: 8 + Math.random() * 10,
        len: 4 + (i % 4),
        seed: i * 7
      });
    }
  }

  spawnShockwave(col, row, maxRadius = 32, strength = 1.0) {
    this.shockwaves.push({
      col,
      row,
      radius: 1.5,
      maxRadius,
      speed: 26.0,
      strength
    });
    if (this.shockwaves.length > 5) {
      this.shockwaves.shift();
    }
  }

  addMouseTrail(col, row, vx, vy) {
    const speed = Math.hypot(vx, vy);
    if (speed < 0.2) return;
    const glyphs = ['·', '˚', '+', '·', '✦'];
    this.trail.push({
      col,
      row,
      vx: vx * 0.08,
      vy: vy * 0.08,
      life: 1.0,
      decay: 0.045 + Math.random() * 0.02,
      char: glyphs[Math.floor(Math.random() * glyphs.length)]
    });
    if (this.trail.length > 24) {
      this.trail.shift();
    }
  }

  updateAndRender(renderer, dt, time, state) {
    const cols = renderer.cols;
    const rows = renderer.rows;
    const centerC = cols * 0.5;
    const centerR = rows * 0.5;
    const aspect = renderer.cellH / renderer.cellW;

    const mode = state.activeMode;
    const mouseCol = state.mouse.col;
    const mouseRow = state.mouse.row;
    const kanjiList = this.kanjiSets[mode] || this.kanjiSets.companion;

    // 1. Particelle ambientali di Kanji e polvere stellare (interagiscono dolcemente col mouse)
    for (let i = 0; i < this.ambientParticles.length; i++) {
      const p = this.ambientParticles[i];
      p.ny -= p.baseSpeedY * dt * (mode === 'afk' ? 0.4 : 1.0);
      if (p.ny < 0.03) {
        p.ny = 0.97;
        p.nx = Math.random();
        p.vx = 0;
        p.vy = 0;
      }

      let c = p.nx * cols + Math.sin(time * 0.6 + p.phase) * 1.5 + p.vx;
      let r = p.ny * rows + p.vy;

      // Repulsione morbida se il mouse passa vicino alla particella
      const dxM = c - mouseCol;
      const dyM = (r - mouseRow) * aspect;
      const distM = Math.hypot(dxM, dyM);
      if (distM < 12 && distM > 0.1) {
        const push = (1 - distM / 12) * 18 * dt;
        p.vx += (dxM / distM) * push;
        p.vy += (dyM / distM) * (push / aspect);
      }
      p.vx *= 0.95;
      p.vy *= 0.95;

      const ic = Math.round(c);
      const ir = Math.round(r);

      // Lascia spazio completamente pulito attorno al Neko centrale
      const distCenter = Math.hypot(ic - centerC, (ir - centerR) * aspect);
      if (distCenter < 22) continue;

      const ch = kanjiList[p.glyphIdx % kanjiList.length];
      const lum = distM < 14 ? Math.min(5, p.lum + 2) : p.lum;
      renderer.setCell(ic, ir, ch, lum);
    }

    // 2. Se siamo in modalità AI WORK: pioggia verticale discreta di Kanji/codice solo sui lati
    if (mode === 'ai') {
      const aiGlyphs = ['電', '脳', '知', '算', '零', '壱', '0', '1', ':', '·'];
      for (let i = 0; i < this.aiColumns.length; i++) {
        const colObj = this.aiColumns[i];
        colObj.y += colObj.speed * dt;
        if (colObj.y > rows + colObj.len) {
          colObj.y = -2;
        }
        const c =
          colObj.side === 'left'
            ? Math.round(cols * colObj.offsetRatio)
            : Math.round(cols * (1 - colObj.offsetRatio));

        for (let k = 0; k < colObj.len; k++) {
          const r = Math.round(colObj.y - k);
          const ch = k === 0 ? aiGlyphs[(colObj.seed + Math.floor(time * 4)) % aiGlyphs.length] : k % 2 === 0 ? ':' : '·';
          const lum = k === 0 ? 5 : Math.max(1, 4 - k);
          renderer.setCell(c, r, ch, lum);
        }
      }
    }

    // 3. Scia leggera e pulita del mouse
    for (let i = this.trail.length - 1; i >= 0; i--) {
      const pt = this.trail[i];
      pt.col += pt.vx * dt * 12;
      pt.row += pt.vy * dt * 12;
      pt.life -= pt.decay;
      if (pt.life <= 0) {
        this.trail.splice(i, 1);
        continue;
      }
      const lum = pt.life > 0.65 ? 5 : pt.life > 0.3 ? 3 : 2;
      renderer.setCell(Math.round(pt.col), Math.round(pt.row), pt.char, lum);
    }

    // 4. Onde circolari parametriche al click (zero scansione a tutto schermo!)
    for (let i = this.shockwaves.length - 1; i >= 0; i--) {
      const sw = this.shockwaves[i];
      sw.radius += sw.speed * dt;
      if (sw.radius >= sw.maxRadius) {
        this.shockwaves.splice(i, 1);
        continue;
      }

      const fade = 1 - sw.radius / sw.maxRadius;
      const lum = fade > 0.6 ? 5 : fade > 0.3 ? 3 : 2;
      const steps = Math.max(24, Math.floor(sw.radius * 4.5));

      for (let s = 0; s < steps; s++) {
        const ang = (Math.PI * 2 * s) / steps;
        const rc = Math.round(sw.col + Math.cos(ang) * sw.radius);
        const rr = Math.round(sw.row + (Math.sin(ang) * sw.radius) / aspect);
        const ch = s % 6 === 0 ? kanjiList[s % kanjiList.length] : '·';
        renderer.setCell(rc, rr, ch, lum);
      }
    }

    // 5. Piccolo cursore minimale
    if (state.mouse.active && mouseCol >= 0 && mouseRow >= 0) {
      renderer.setCell(mouseCol, mouseRow, '+', 6, true);
    }
  }
}

window.FluidField = FluidField;
