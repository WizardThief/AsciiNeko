/**
 * AsciiRenderer - Motore Canvas 2D ultra-leggero (zero-stutter, <0.5ms/frame)
 * Disegna esclusivamente le celle attive raggruppate per livello di luminanza.
 */
class AsciiRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });

    this.cellW = 10;
    this.cellH = 18;
    this.fontSize = 15;
    this.cols = 0;
    this.rows = 0;

    // 8 livelli di luminanza monocromatica pulita
    this.palette = [
      '#060608', // 0: Sfondo nero profondo
      '#1e1e28', // 1: Particelle lontane
      '#343444', // 2: Griglia/elementi soffusi
      '#54546a', // 3: Kanji ambientali / ombre
      '#7e7e98', // 4: Contorni secondari
      '#b2b2c8', // 5: Linee principali
      '#e0e0f0', // 6: Corso primario del Companion
      '#ffffff'  // 7: Punti focali nitidi (occhi, dettagli)
    ];

    this.charGrid = null;
    this.lumGrid = null;
    // Bucket di indici attivi per i 7 livelli di luminanza (evita di scansionare le celle vuote!)
    this.buckets = Array.from({ length: 8 }, () => []);
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.fontString = '';

    this.resize();
  }

  setDensity(preset) {
    if (preset === 'compact') {
      this.cellW = 8;
      this.cellH = 15;
      this.fontSize = 13;
    } else if (preset === 'large') {
      this.cellW = 12;
      this.cellH = 22;
      this.fontSize = 18;
    } else {
      this.cellW = 10;
      this.cellH = 18;
      this.fontSize = 15;
    }
    this.resize();
  }

  resize() {
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = window.innerWidth;
    const height = window.innerHeight;

    this.canvas.width = Math.floor(width * this.dpr);
    this.canvas.height = Math.floor(height * this.dpr);
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;

    this.cols = Math.max(40, Math.floor(width / this.cellW));
    this.rows = Math.max(24, Math.floor(height / this.cellH));

    const total = this.cols * this.rows;
    this.charGrid = new Array(total).fill(' ');
    this.lumGrid = new Uint8Array(total);

    this.fontString = `500 ${this.fontSize}px "Cascadia Mono", "Consolas", "JetBrains Mono", "MS Gothic", monospace`;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.ctx.font = this.fontString;
    this.ctx.textBaseline = 'top';
  }

  clear() {
    this.lumGrid.fill(0);
  }

  setCell(col, row, char, lumLevel, force = false) {
    if (col < 0 || col >= this.cols || row < 0 || row >= this.rows) return;
    if (char === ' ' && !force) return;
    const idx = row * this.cols + col;
    const clampedLum = char === ' ' ? 0 : Math.max(0, Math.min(7, Math.round(lumLevel)));
    if (force || clampedLum >= this.lumGrid[idx]) {
      this.charGrid[idx] = char;
      this.lumGrid[idx] = clampedLum;
    }
  }

  drawText(col, row, text, lumLevel, force = true) {
    if (row < 0 || row >= this.rows) return;
    for (let i = 0; i < text.length; i++) {
      const c = col + i;
      if (c >= 0 && c < this.cols) {
        this.setCell(c, row, text[i], lumLevel, force);
      }
    }
  }

  render() {
    const ctx = this.ctx;
    const width = window.innerWidth;
    const height = window.innerHeight;

    ctx.fillStyle = this.palette[0];
    ctx.fillRect(0, 0, width, height);

    const cols = this.cols;
    const rows = this.rows;
    const cellW = this.cellW;
    const cellH = this.cellH;
    const charGrid = this.charGrid;
    const lumGrid = this.lumGrid;
    const buckets = this.buckets;

    for (let b = 1; b <= 7; b++) {
      buckets[b].length = 0;
    }

    // Singola passata veloce sulla memoria tipizzata Uint8Array
    const total = cols * rows;
    for (let idx = 0; idx < total; idx++) {
      const lum = lumGrid[idx];
      if (lum > 0) {
        buckets[lum].push(idx);
      }
    }

    const offsetX = Math.floor((width - cols * cellW) * 0.5);
    const offsetY = Math.floor((height - rows * cellH) * 0.5);

    ctx.font = this.fontString;

    for (let lum = 1; lum <= 7; lum++) {
      const list = buckets[lum];
      const len = list.length;
      if (len === 0) continue;

      ctx.fillStyle = this.palette[lum];
      for (let i = 0; i < len; i++) {
        const idx = list[i];
        const r = (idx / cols) | 0;
        const c = idx - r * cols;
        ctx.fillText(charGrid[idx], offsetX + c * cellW, offsetY + r * cellH);
      }
    }
  }
}

window.AsciiRenderer = AsciiRenderer;
