/**
 * CompanionFace - Unico Gattino Neko Multi-Schermo in ASCII Art
 *
 * - Vive principalmente sullo Schermo 1 (Principale) dove si trovano anche le tue vere cartelle del Desktop.
 * - Sullo Schermo 2 (Secondario) NON ci sono cartelle duplicate né un secondo gatto duplicato!
 * - Se clicchi sullo Schermo 2 (per lanciare un gomitolo (@)~ o un pescetto ><(((º>),
 *   l'unico Neko corre fuori dal bordo destro dello Schermo 1, entra nello Schermo 2,
 *   gioca/mangia sullo Schermo 2 e dopo qualche secondo torna trottando sullo Schermo 1!
 */
class CompanionFace {
  constructor(onEventCallback) {
    this.onEvent = onEventCallback || (() => {});

    // Coordinate "World" (0 .. cols-1 = Schermo 1 Principale, cols .. 2*cols-1 = Schermo 2 Secondario)
    this.worldCol = 60;
    this.row = 24;
    this.initializedPos = false;

    this.facing = 1;
    this.isWalking = false;
    this.walkPhase = 0;
    this.roamEnabled = true;

    this.isDraggingCat = false;
    this.isDraggingFolder = null;

    this.attachedFolder = null;
    this.folderPose = 'sit'; // 'sit', 'peek'

    this.target = null;
    this.toy = null; // { worldCol, row, type: 'yarn' | 'fish' }

    // Timer per far tornare il Neko sullo Schermo 1 dopo una gita sullo Schermo 2
    this.visitSecondScreenTimer = 0;

    this.folders = [];
    this.folderNamesSignature = '';

    this.lookX = 0;
    this.lookY = 0;
    this.bounceY = 0;
    this.bounceVel = 0;

    this.blinkTimer = 3.0;
    this.blinkState = 0;

    this.emotion = 'neutral';
    this.emotionTimer = 0;
    this.autonomousTimer = 7.0;

    this.patScore = 0;
    this.particles = [];
    this.lastNoteSpawn = 0;

    this.syncDesktopFolders([
      { name: 'EPO', type: 'folder' },
      { name: 'DIE_ASSETS', type: 'folder' },
      { name: 'MC_NAV_01', type: 'folder' },
      { name: 'JSplacement', type: 'app' },
      { name: 'TUIOSimulator', type: 'app' }
    ]);
  }

  syncDesktopFolders(items) {
    if (!items || !items.length) return;
    const sig = items.map((i) => i.name).join('|');
    if (sig === this.folderNamesSignature && this.folders.length > 0) return;
    this.folderNamesSignature = sig;

    const maxItems = Math.min(8, items.length);
    const newFolders = [];
    for (let i = 0; i < maxItems; i++) {
      const item = items[i];
      const colIndex = Math.floor(i / 4);
      const rowIndex = i % 4;
      newFolders.push({
        id: i,
        name: item.name.slice(0, 12),
        type: item.type || 'folder',
        col: 6 + colIndex * 22,
        row: 6 + rowIndex * 9,
        w: 16,
        h: 4,
        shakeX: 0
      });
    }
    this.folders = newFolders;
  }

  toggleRoamMode() {
    this.roamEnabled = !this.roamEnabled;
    if (!this.roamEnabled) {
      this.attachedFolder = null;
      this.toy = null;
      this.target = { worldCol: this.homeCol || 60, row: this.homeRow || 24, type: 'walk' };
    }
    return this.roamEnabled;
  }

  sendToFolder(folder) {
    if (!folder) return;
    this.attachedFolder = null;
    const poses = ['sit', 'peek', 'bap'];
    const chosen = poses[Math.floor(Math.random() * poses.length)];
    this.target = {
      worldCol: folder.col + Math.floor(folder.w * 0.5),
      row: folder.row - 2,
      type: 'folder',
      folderRef: folder,
      desiredPose: chosen
    };
    this.onEvent('folder_play', folder.name);
  }

  /**
   * Riceve un comando remoto dallo Schermo 2 (es. l'utente ha cliccato sullo Schermo 2!)
   */
  handleRemoteCommand(cmd, renderer) {
    if (!cmd) return;
    this.attachedFolder = null;
    const targetWorldCol = renderer.cols + (cmd.localCol || Math.round(renderer.cols * 0.5));
    const targetRow = cmd.row || Math.round(renderer.rows * 0.5);
    const toyType = cmd.type === 'fish' ? 'fish' : 'yarn';

    this.toy = { worldCol: targetWorldCol, row: targetRow, type: toyType };
    this.target = { worldCol: targetWorldCol, row: targetRow, type: toyType };
    this.visitSecondScreenTimer = 7.5; // Rimane ~7.5s sullo Schermo 2 prima di tornare a casa sullo Schermo 1
    this.onEvent(toyType === 'fish' ? 'fish' : 'yarn');
  }

  spawnFishSnack(renderer, customLocalCol, customRow, state, systemBridge) {
    const isSecondary = state && state.screenIndex !== 0;
    const localC =
      customLocalCol !== undefined
        ? customLocalCol
        : Math.round(renderer.cols * (0.35 + Math.random() * 0.4));
    const r =
      customRow !== undefined
        ? customRow
        : Math.round(renderer.rows * (0.35 + Math.random() * 0.35));

    if (isSecondary && systemBridge) {
      systemBridge.sendRemoteCommand({ type: 'fish', localCol: localC, row: r });
      return;
    }

    this.attachedFolder = null;
    this.toy = { worldCol: localC, row: r, type: 'fish' };
    this.target = { worldCol: localC, row: r, type: 'fish' };
    this.onEvent('fish');
  }

  handleMouseDown(clickCol, clickRow, button, renderer, state, systemBridge) {
    const isSecondary = state.screenIndex !== 0;

    // Se l'utente clicca sullo Schermo 2 (Secondario), chiama il Neko dallo Schermo 1 allo Schermo 2!
    if (isSecondary) {
      const cmdType = button === 2 ? 'fish' : 'yarn';
      if (systemBridge) {
        systemBridge.sendRemoteCommand({
          type: cmdType,
          localCol: clickCol,
          row: clickRow
        });
      }
      return cmdType;
    }

    // --- Siamo sullo Schermo 1 (Principale) ---
    if (button === 2) {
      this.spawnFishSnack(renderer, clickCol, clickRow, state, systemBridge);
      return 'fish';
    }

    // 1. Click sul Neko (Drag per la collottola)
    const distCat = Math.hypot(clickCol - this.worldCol, (clickRow - this.row) * 1.5);
    if (distCat < 9) {
      this.isDraggingCat = true;
      this.attachedFolder = null;
      this.target = null;
      this.bounceVel = -7.0;
      this.emotion = 'surprised';
      this.emotionTimer = 1.2;
      this.spawnParticles('spark', 2);
      this.onEvent('poke');
      return 'drag_cat';
    }

    // 2. Click su una Cartella del Desktop (presenti solo sullo Schermo 1)
    for (let i = 0; i < this.folders.length; i++) {
      const f = this.folders[i];
      if (
        clickCol >= f.col &&
        clickCol <= f.col + f.w &&
        clickRow >= f.row &&
        clickRow <= f.row + f.h
      ) {
        this.isDraggingFolder = {
          folder: f,
          offC: clickCol - f.col,
          offR: clickRow - f.row,
          moved: false
        };
        return 'folder_click';
      }
    }

    // 3. Click sullo spazio vuoto dello Schermo 1: lancia il Gomitolo (@)~
    if (this.roamEnabled) {
      this.attachedFolder = null;
      const tc = Math.max(10, Math.min(renderer.cols - 10, clickCol));
      const tr = Math.max(8, Math.min(renderer.rows - 8, clickRow));
      this.toy = { worldCol: tc, row: tr, type: 'yarn' };
      this.target = { worldCol: tc, row: tr, type: 'yarn' };
      this.onEvent('yarn');
      return 'yarn';
    }

    return 'wave';
  }

  handleMouseMove(col, row, renderer, state) {
    if (state.screenIndex !== 0) return false;

    if (this.isDraggingCat) {
      this.worldCol = Math.max(8, Math.min(renderer.cols - 8, col));
      this.row = Math.max(6, Math.min(renderer.rows - 6, row + 2));
      return true;
    }
    if (this.isDraggingFolder) {
      const df = this.isDraggingFolder;
      df.moved = true;
      df.folder.col = Math.max(2, Math.min(renderer.cols - df.folder.w - 2, col - df.offC));
      df.folder.row = Math.max(3, Math.min(renderer.rows - df.folder.h - 3, row - df.offR));
      return true;
    }
    return false;
  }

  handleMouseUp(state) {
    if (state && state.screenIndex !== 0) return;

    if (this.isDraggingCat) {
      this.isDraggingCat = false;
      this.bounceVel = -8.0;

      for (let i = 0; i < this.folders.length; i++) {
        const f = this.folders[i];
        if (
          Math.abs(this.worldCol - (f.col + f.w * 0.5)) < f.w * 0.65 &&
          Math.abs(this.row - f.row) < 6
        ) {
          this.attachedFolder = f;
          this.folderPose = Math.random() > 0.4 ? 'sit' : 'peek';
          this.worldCol = Math.round(f.col + f.w * 0.5);
          this.row = f.row - 2;
          this.emotion = 'happy';
          this.emotionTimer = 2.5;
          this.spawnParticles('heart', 2);
          this.onEvent('folder_drop', f.name);
          break;
        }
      }
    }

    if (this.isDraggingFolder) {
      const df = this.isDraggingFolder;
      this.isDraggingFolder = null;
      if (!df.moved) {
        this.sendToFolder(df.folder);
      }
    }
  }

  spawnParticles(type, count = 2) {
    const pools = {
      heart: ['♡', '♪', '*'],
      zzz: ['z', 'Z'],
      ai: ['0', '1', ';'],
      music: ['♪', '♫'],
      spark: ['!', '*']
    };
    const pool = pools[type] || pools.spark;

    for (let i = 0; i < count; i++) {
      if (this.particles.length >= 6) break;
      this.particles.push({
        ox: (Math.random() - 0.5) * 10,
        oy: -5 - Math.random() * 2,
        vx: (Math.random() - 0.5) * 1.4,
        vy: -1.6 - Math.random() * 1.0,
        life: 1.0,
        decay: 0.015 + Math.random() * 0.01,
        char: pool[Math.floor(Math.random() * pool.length)]
      });
    }
  }

  update(dt, time, state, renderer, systemBridge) {
    this.homeCol = Math.round(renderer.cols * 0.55);
    this.homeRow = Math.round(renderer.rows * 0.52);

    if (!this.initializedPos) {
      this.worldCol = this.homeCol;
      this.row = this.homeRow;
      this.initializedPos = true;
    }

    // Se questo è lo Schermo 2 (Secondario), non esegue una seconda AI del gatto:
    // riceve la posizione dell'unico gatto dallo Schermo 1 tramite state.sharedCat!
    if (state.screenIndex !== 0) {
      if (state.sharedCat) {
        this.worldCol = state.sharedCat.worldCol;
        this.row = state.sharedCat.row;
        this.facing = state.sharedCat.facing;
        this.isWalking = state.sharedCat.isWalking;
        this.walkPhase = state.sharedCat.walkPhase;
        this.emotion = state.sharedCat.emotion || 'neutral';
        this.toy = state.sharedCat.toy || null;
      }
      return;
    }

    // --- Logica Master sullo Schermo 1 (Principale) ---
    for (let i = 0; i < this.folders.length; i++) {
      this.folders[i].shakeX *= 0.82;
    }

    if (this.attachedFolder) {
      this.worldCol = Math.round(this.attachedFolder.col + this.attachedFolder.w * 0.5);
      this.row = this.attachedFolder.row - 2;
    }

    // Se il Neko si trova sullo Schermo 2 (worldCol >= cols) e ha finito di giocare,
    // dopo qualche secondo torna trottando sullo Schermo 1 (Principale)!
    if (this.worldCol >= renderer.cols && !this.target) {
      this.visitSecondScreenTimer -= dt;
      if (this.visitSecondScreenTimer <= 0) {
        this.target = {
          worldCol: this.homeCol,
          row: this.homeRow,
          type: 'walk'
        };
      }
    }

    if (
      (state.activeMode === 'ai' || state.activeMode === 'coffee' || state.activeMode === 'focus') &&
      !this.isDraggingCat
    ) {
      this.attachedFolder = null;
      this.toy = null;
      const distHome = Math.hypot(this.homeCol - this.worldCol, this.homeRow - this.row);
      if (distHome > 2) {
        this.target = { worldCol: this.homeCol, row: this.homeRow, type: 'walk' };
      }
    }

    // Movimento verso il target (anche attraverso il confine tra Schermo 1 e Schermo 2!)
    if (this.target && !this.isDraggingCat) {
      const dxT = this.target.worldCol - this.worldCol;
      const dyT = this.target.row - this.row;
      const distT = Math.hypot(dxT, dyT);

      if (distT > 2.2) {
        this.isWalking = true;
        this.facing = dxT >= 0 ? 1 : -1;
        this.walkPhase += dt * 13.0;
        const speed = 32.0;
        this.worldCol += (dxT / distT) * Math.min(distT, speed * dt);
        this.row += (dyT / distT) * Math.min(distT, speed * dt);
      } else {
        this.worldCol = this.target.worldCol;
        this.row = this.target.row;
        this.isWalking = false;
        const tType = this.target.type;
        const fRef = this.target.folderRef;
        const desiredPose = this.target.desiredPose;
        this.target = null;

        if (tType === 'fish') {
          this.toy = null;
          this.emotion = 'eating';
          this.emotionTimer = 2.4;
          this.bounceVel = -6.0;
          this.spawnParticles('heart', 3);
        } else if (tType === 'yarn') {
          this.toy = null;
          this.emotion = 'happy';
          this.emotionTimer = 1.8;
          this.bounceVel = -6.0;
          this.spawnParticles('spark', 2);
        } else if (tType === 'folder' && fRef) {
          if (desiredPose === 'bap') {
            this.emotion = 'bapping';
            this.emotionTimer = 1.6;
            fRef.shakeX = 2.0;
            this.bounceVel = -5.0;
            this.spawnParticles('spark', 2);
          } else {
            this.attachedFolder = fRef;
            this.folderPose = desiredPose || 'sit';
            this.bounceVel = -7.0;
          }
        }
      }
    } else {
      this.isWalking = false;
    }

    // Iniziativa autonoma sullo Schermo 1: gioca con le tue cartelle
    if (
      state.activeMode === 'companion' &&
      this.roamEnabled &&
      !this.isWalking &&
      !this.isDraggingCat &&
      !this.target &&
      this.worldCol < renderer.cols &&
      this.folders.length > 0
    ) {
      this.autonomousTimer -= dt;
      if (this.autonomousTimer <= 0) {
        this.autonomousTimer = 14.0 + Math.random() * 14.0;
        if (Math.random() < 0.72) {
          const randomFolder = this.folders[Math.floor(Math.random() * this.folders.length)];
          this.sendToFolder(randomFolder);
        } else {
          this.attachedFolder = null;
          this.target = {
            worldCol: Math.round(this.homeCol + (Math.random() - 0.5) * 18),
            row: Math.round(this.homeRow + (Math.random() - 0.5) * 10),
            type: 'walk'
          };
        }
      }
    }

    // Calcolo sguardo verso il mouse (anche se il mouse si trova sullo Schermo 2!)
    let mouseWorldCol = state.mouse.col;
    if (state.globalMouse && state.globalMouse.screenW > 0) {
      mouseWorldCol = (state.globalMouse.x / state.globalMouse.screenW) * renderer.cols;
    }
    const dxM = mouseWorldCol - this.worldCol;
    const dyM = state.mouse.row - this.row;
    const distM = Math.hypot(dxM, dyM * 1.5);
    this.lookX = Math.max(-1, Math.min(1, dxM / 20));
    this.lookY = Math.max(-1, Math.min(1, dyM / 14));

    this.bounceVel += (-this.bounceY * 42.0 - this.bounceVel * 7.5) * dt;
    this.bounceY += this.bounceVel * dt;

    if (
      !this.isWalking &&
      !this.isDraggingCat &&
      this.emotion === 'neutral' &&
      distM > 4 &&
      distM < 12
    ) {
      this.emotion = 'reaching';
      this.emotionTimer = 0.25;
    }

    const isOverHead = Math.abs(dxM) < 9 && dyM >= -6 && dyM <= 0;
    const mouseSpeed = Math.hypot(state.mouse.vx, state.mouse.vy);
    if (isOverHead && mouseSpeed > 0.2 && mouseSpeed < 9.0 && !this.isDraggingCat) {
      this.patScore += dt * 2.6;
      if (this.patScore > 0.8) {
        this.emotion = 'happy';
        this.emotionTimer = 2.6;
        this.patScore = 0;
        this.bounceVel = -5.0;
        this.spawnParticles('heart', 3);
        this.onEvent('pat');
      }
    } else {
      this.patScore = Math.max(0, this.patScore - dt * 0.6);
    }

    if (this.emotionTimer > 0) {
      this.emotionTimer -= dt;
      if (this.emotionTimer <= 0) this.emotion = 'neutral';
    }

    this.blinkTimer -= dt;
    if (this.blinkTimer <= 0) {
      if (this.blinkState === 0) {
        this.blinkState = 1;
        this.blinkTimer = 0.13;
      } else {
        this.blinkState = 0;
        this.blinkTimer = 2.8 + Math.random() * 3.2;
      }
    }

    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.ox += p.vx * dt;
      p.oy += p.vy * dt;
      p.life -= p.decay;
      if (p.life <= 0) this.particles.splice(i, 1);
    }

    // Pubblica lo stato dell'unico Neko verso lo Schermo 2!
    if (systemBridge) {
      systemBridge.publishCatState({
        worldCol: Math.round(this.worldCol * 10) / 10,
        row: Math.round(this.row * 10) / 10,
        facing: this.facing,
        isWalking: this.isWalking,
        walkPhase: Math.round(this.walkPhase * 10) / 10,
        emotion: this.emotion,
        toy: this.toy
      });
    }
  }

  drawArtLines(renderer, cx, topY, lines, lum = 7) {
    for (let r = 0; r < lines.length; r++) {
      const line = lines[r];
      const startCol = Math.round(cx - line.length * 0.5);
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch !== ' ') {
          renderer.setCell(startCol + i, Math.round(topY + r), ch, lum, true);
        }
      }
    }
  }

  renderDesktopFolders(renderer, state) {
    // Le cartelle del Desktop vengono disegnate ESCLUSIVAMENTE sullo Schermo 1 (Principale)!
    if (state.screenIndex !== 0) return;

    for (let i = 0; i < this.folders.length; i++) {
      const f = this.folders[i];
      const shake = Math.round(Math.sin(performance.now() * 0.05) * f.shakeX);
      const c = Math.round(f.col + shake);
      const r = Math.round(f.row);

      const isHovered =
        state.mouse.col >= c &&
        state.mouse.col <= c + f.w &&
        state.mouse.row >= r &&
        state.mouse.row <= r + f.h;
      const isCatHere = this.attachedFolder === f;
      const lum = isCatHere || isHovered ? 6 : 4;

      const icon = f.type === 'folder' ? '📁' : '⚙';
      const label = f.name.padEnd(11, ' ').slice(0, 11);

      renderer.drawText(c, r, '╭───╮──────────╮', lum, true);
      renderer.drawText(c, r + 1, `│ ${icon} ${label}│`, isCatHere || isHovered ? 7 : 5, true);
      renderer.drawText(c, r + 2, '╰──────────────╯', lum, true);
    }
  }

  getSimpleFace(time, state) {
    const mode = state.activeMode;
    const audio = state.audioEnergy || 0;
    const shift = this.lookX > 0.25 ? 1 : this.lookX < -0.25 ? -1 : 0;

    if (this.isDraggingCat) return '  > ω <  ';
    if (this.emotion === 'eating') return '  ˆ ڡ ˆ  ';
    if (this.emotion === 'happy') return ' //^ω^// ';
    if (this.emotion === 'surprised') return '  o ω o  ';
    if (this.emotion === 'bapping') return '  • ̀ω •́ ';
    if (mode === 'afk') return '  - ω -  ';
    if (mode === 'coffee') return '  ˘ ω ˘  ';
    if (mode === 'ai') return ' -•-ω-•- ';
    if (this.blinkState === 1) return '  - ω -  ';

    const mouth = audio > 0.05 && Math.sin(time * 7.0) > 0 ? '▽' : 'ω';
    const core = `• ${mouth} •`;
    if (shift < 0) return ` ${core}   `;
    if (shift > 0) return `   ${core} `;
    return `  ${core}  `;
  }

  render(renderer, time, state) {
    const screenOffsetCols = (state.screenIndex || 0) * renderer.cols;
    const localCatCol = Math.round(this.worldCol - screenOffsetCols);

    const isPeeking =
      state.screenIndex === 0 &&
      this.attachedFolder &&
      this.folderPose === 'peek' &&
      !this.isWalking;

    if (!isPeeking) {
      this.renderDesktopFolders(renderer, state);
    }

    // Disegna eventuale Gomitolo o Pescetto se si trova su questo schermo
    if (this.toy) {
      const localToyCol = Math.round(this.toy.worldCol - screenOffsetCols);
      if (localToyCol >= -6 && localToyCol <= renderer.cols + 6) {
        if (this.toy.type === 'fish') {
          renderer.drawText(localToyCol - 3, Math.round(this.toy.row), '><(((º>', 7, true);
        } else {
          renderer.drawText(localToyCol - 2, Math.round(this.toy.row), '(@)~', 7, true);
        }
      }
    }

    // Se l'unico Neko in questo momento è sull'altro schermo, NON disegnarlo qui!
    if (localCatCol < -12 || localCatCol > renderer.cols + 12) {
      if (isPeeking) this.renderDesktopFolders(renderer, state);
      return;
    }

    const cx = localCatCol;
    const cy = Math.round(this.row + this.bounceY);
    const face = this.getSimpleFace(time, state);

    if (this.isDraggingCat) {
      const sway = Math.sin(time * 10) > 0 ? ' ∪   ∪ ' : '  ∪ ∪  ';
      const lines = [
        '  /\\_____/\\  ',
        ` (${face}) `,
        '  /       \\  ',
        ' │  │   │  │ ',
        '  \\       /  ',
        `   ${sway}   `
      ];
      this.drawArtLines(renderer, cx, cy - 3, lines, 7);
    } else if (state.activeMode === 'afk') {
      const lines = [
        '  /\\_____/\\  ',
        ' (  - ω -  )___ ',
        ' (             )╮',
        '  `────────────╯╰'
      ];
      this.drawArtLines(renderer, cx, cy - 2, lines, 7);
    } else if (this.isWalking) {
      const step = Math.floor(this.walkPhase) % 2;
      const feet = step === 0 ? '∪─∪───∪─∪' : '─∪─∪─∪─∪─';
      if (this.facing >= 0) {
        const lines = [
          ' ╭─╮   /\\_____/\\ ',
          ` ╰╮ ╰─(${face})`,
          '  │             \\',
          `  ╰─${feet}─'`
        ];
        this.drawArtLines(renderer, cx, cy - 2, lines, 7);
      } else {
        const lines = [
          ' /\\_____/\\   ╭─╮ ',
          `(${face})─╯ ╭╯ `,
          '/             │  ',
          `'─${feet}─╯  `
        ];
        this.drawArtLines(renderer, cx, cy - 2, lines, 7);
      }
    } else if (isPeeking) {
      const lines = [
        '  /\\_____/\\  ',
        ` (${face}) `,
        ' ──∪─────∪── '
      ];
      this.drawArtLines(renderer, cx, cy - 1, lines, 7);
      this.renderDesktopFolders(renderer, state);
    } else {
      const tailUp = Math.sin(time * 2.5) > 0;
      const tTop = tailUp ? '  ╭' : '   ';
      const tMid = tailUp ? ')─╯' : ')─╮';
      const earRow = Math.sin(time * 1.8) > 0.9 ? '  /\\_____/\\~ ' : '  /\\_____/\\  ';

      let bodyLine = '  /       \\  ';
      if (this.emotion === 'reaching' || this.emotion === 'bapping') {
        bodyLine = this.lookX < 0 ? ' づ       \\  ' : '  /       づ ';
      }

      const lines = [
        earRow,
        ` (${face}) `,
        `${bodyLine}${tTop}`,
        ` (  │   │  ${tMid}`,
        '  `─∪───∪──\'  '
      ];
      this.drawArtLines(renderer, cx, cy - 3, lines, 7);
    }

    if (!this.isWalking && !this.isDraggingCat && state.screenIndex === 0) {
      if (state.activeMode === 'ai') {
        const k = Math.floor(time * 9) % 2 === 0 ? '[ ▀▄▀▄▀▄▀ ]' : '[ ▄▀▄▀▄▀▄ ]';
        renderer.drawText(cx - 5, cy + 2, k, 7, true);
      } else if (state.activeMode === 'coffee') {
        const steam = Math.sin(time * 2.5) > 0 ? ' ~ ' : ' ∿ ';
        renderer.drawText(cx + 8, cy, steam, 5, true);
        renderer.drawText(cx + 8, cy + 1, 'c[_]', 7, true);
      }
    }

    for (let i = 0; i < this.particles.length; i++) {
      const p = this.particles[i];
      renderer.setCell(
        Math.round(cx + p.ox),
        Math.round(cy + p.oy),
        p.char,
        p.life > 0.5 ? 7 : 4,
        true
      );
    }
  }
}

window.CompanionFace = CompanionFace;
