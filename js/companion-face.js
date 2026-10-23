/**
 * CompanionFace - Unico Gattino Neko Multi-Schermo in ASCII Art
 *
 * Caratteristiche:
 *  - Design 5 righe essenziale, pulito e super espressivo (senza scritte o cartelle finte sotto i piedi)
 *  - Reagisce ai click SOLO quando vengono fatti davvero sullo sfondo vuoto del Desktop (isDesktopFocus)
 *  - 6 Animazioni di Idle autonome:
 *      1. 'sit'     : Seduto composto che muove la coda e segue il mouse
 *      2. 'groom'   : Si lecca la zampina (づ) passando sul musetto ( ˘ ω ˘ )
 *      3. 'stretch' : Si stiracchia allungando le zampe avanti e sbadiglia (  〇  )
 *      4. 'scratch' : Si gratta dietro l'orecchio ( > ω ^ )
 *      5. 'firefly' : Punta una lucciola luminosa (*), scodinzola veloce e salta per prenderla!
 *      6. 'loaf'    : Si acciambella a pagnotta nascondendo le zampine
 *  - Multi-Schermo intelligente: vive sullo Schermo 1, ma se lavori a lungo sullo Schermo 2
 *    viene a sbirciare oltre il bordo tra i due monitor (o corre sullo Schermo 2 se gli lanci il gomitolo/pescetto)!
 */
class CompanionFace {
  constructor(onEventCallback) {
    this.onEvent = onEventCallback || (() => {});

    // Coordinate "World" (0 .. cols-1 = Schermo 1, cols .. 2*cols-1 = Schermo 2)
    this.worldCol = 60;
    this.row = 24;
    this.initializedPos = false;

    this.facing = 1;
    this.isWalking = false;
    this.walkPhase = 0;
    this.roamEnabled = true;

    this.isDraggingCat = false;
    this.target = null;
    this.toy = null; // { worldCol, row, type: 'yarn' | 'fish' }

    // Lucciola per l'animazione idle 'firefly'
    this.firefly = null; // { ox, oy, phase }

    // Stato di Idle corrente: 'sit', 'groom', 'stretch', 'scratch', 'firefly', 'loaf'
    this.idleState = 'sit';
    this.idleStateTimer = 6.0;
    this.autonomousWalkTimer = 18.0;

    // Curiosità verso il secondo schermo quando l'utente lavora a lungo sullo Schermo 2
    this.userOnScreen2Seconds = 0;
    this.visitSecondScreenTimer = 0;

    this.lookX = 0;
    this.lookY = 0;
    this.bounceY = 0;
    this.bounceVel = 0;

    this.blinkTimer = 3.0;
    this.blinkState = 0;

    this.emotion = 'neutral';
    this.emotionTimer = 0;

    this.patScore = 0;
    this.particles = [];
    this.lastNoteSpawn = 0;
  }

  toggleRoamMode() {
    this.roamEnabled = !this.roamEnabled;
    if (!this.roamEnabled) {
      this.toy = null;
      this.target = { worldCol: this.homeCol || 60, row: this.homeRow || 24, type: 'walk' };
    }
    return this.roamEnabled;
  }

  handleRemoteCommand(cmd, renderer) {
    if (!cmd) return;
    const targetWorldCol = renderer.cols + (cmd.localCol || Math.round(renderer.cols * 0.35));
    const targetRow = cmd.row || Math.round(renderer.rows * 0.5);
    const toyType = cmd.type === 'fish' ? 'fish' : 'yarn';

    this.idleState = 'sit';
    this.toy = { worldCol: targetWorldCol, row: targetRow, type: toyType };
    this.target = { worldCol: targetWorldCol, row: targetRow, type: toyType };
    this.visitSecondScreenTimer = 8.0;
    this.onEvent(toyType === 'fish' ? 'fish' : 'yarn');
  }

  spawnFishSnack(renderer, customLocalCol, customRow, state, systemBridge) {
    const isSecondary = state && state.screenIndex !== 0;
    const localC =
      customLocalCol !== undefined
        ? customLocalCol
        : Math.round(renderer.cols * (0.25 + Math.random() * 0.5));
    const r =
      customRow !== undefined
        ? customRow
        : Math.round(renderer.rows * (0.3 + Math.random() * 0.4));

    if (isSecondary && systemBridge) {
      systemBridge.sendRemoteCommand({ type: 'fish', localCol: localC, row: r });
      return;
    }

    this.idleState = 'sit';
    this.toy = { worldCol: localC, row: r, type: 'fish' };
    this.target = { worldCol: localC, row: r, type: 'fish' };
    this.onEvent('fish');
  }

  handleMouseDown(clickCol, clickRow, button, renderer, state, systemBridge) {
    // Ignora i click se l'utente sta cliccando dentro un'altra finestra/applicazione aperta!
    if (state.system && state.system.isDesktopFocus === false) {
      return 'ignored_not_desktop';
    }

    const isSecondary = state.screenIndex !== 0;
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

    if (button === 2) {
      this.spawnFishSnack(renderer, clickCol, clickRow, state, systemBridge);
      return 'fish';
    }

    // 1. Click diretto sul Neko: solleva per la collottola (Drag) o fai saltare
    const distCat = Math.hypot(clickCol - this.worldCol, (clickRow - this.row) * 1.5);
    if (distCat < 9) {
      this.isDraggingCat = true;
      this.idleState = 'sit';
      this.target = null;
      this.bounceVel = -7.5;
      this.emotion = 'surprised';
      this.emotionTimer = 1.2;
      this.spawnParticles('spark', 2);
      this.onEvent('poke');
      return 'drag_cat';
    }

    // 2. Click sullo sfondo vuoto: lancia il Gomitolo (@)~
    if (this.roamEnabled) {
      this.idleState = 'sit';
      const tc = Math.max(10, Math.min(renderer.cols - 10, clickCol));
      const tr = Math.max(8, Math.min(renderer.rows - 10, clickRow));
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
      this.row = Math.max(6, Math.min(renderer.rows - 8, row + 2));
      return true;
    }
    return false;
  }

  handleMouseUp(state) {
    if (state && state.screenIndex !== 0) return;
    if (this.isDraggingCat) {
      this.isDraggingCat = false;
      this.bounceVel = -8.0;
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

  pickNextIdleAnimation() {
    const choices = ['sit', 'sit', 'groom', 'stretch', 'scratch', 'firefly', 'loaf'];
    const next = choices[Math.floor(Math.random() * choices.length)];
    this.idleState = next;

    if (next === 'groom') {
      this.idleStateTimer = 4.2;
    } else if (next === 'stretch') {
      this.idleStateTimer = 3.2;
    } else if (next === 'scratch') {
      this.idleStateTimer = 3.0;
    } else if (next === 'firefly') {
      this.idleStateTimer = 5.5;
      this.firefly = { ox: 8, oy: -4, phase: 0 };
    } else if (next === 'loaf') {
      this.idleStateTimer = 8.0;
    } else {
      this.idleStateTimer = 7.0 + Math.random() * 6.0;
      this.firefly = null;
    }
  }

  update(dt, time, state, renderer, systemBridge) {
    this.homeCol = Math.round(renderer.cols * 0.5);
    this.homeRow = Math.round(renderer.rows * 0.5);

    if (!this.initializedPos) {
      this.worldCol = this.homeCol;
      this.row = this.homeRow;
      this.initializedPos = true;
    }

    // Schermo 2 (Secondario): riceve lo stato dell'unico Neko dallo Schermo 1
    if (state.screenIndex !== 0) {
      if (state.sharedCat) {
        this.worldCol = state.sharedCat.worldCol;
        this.row = state.sharedCat.row;
        this.facing = state.sharedCat.facing;
        this.isWalking = state.sharedCat.isWalking;
        this.walkPhase = state.sharedCat.walkPhase;
        this.emotion = state.sharedCat.emotion || 'neutral';
        this.idleState = state.sharedCat.idleState || 'sit';
        this.toy = state.sharedCat.toy || null;
      }
      return;
    }

    // --- Logica Master sullo Schermo 1 (Principale) ---

    // 1. Se l'utente sta lavorando a lungo sullo Schermo 2, il Neko si incuriosisce
    //    e cammina fino al bordo tra Schermo 1 e Schermo 2 per fare capolino!
    if (state.globalMouse && state.globalMouse.screen === 1 && state.activeMode === 'companion') {
      this.userOnScreen2Seconds += dt;
      if (
        this.userOnScreen2Seconds > 14.0 &&
        this.roamEnabled &&
        !this.target &&
        this.worldCol < renderer.cols - 6
      ) {
        this.userOnScreen2Seconds = 0;
        // Cammina appena oltre il bordo (entra di ~14 colonne nello Schermo 2 per sbirciare!)
        this.target = {
          worldCol: renderer.cols + 12,
          row: Math.max(10, Math.min(renderer.rows - 12, this.row)),
          type: 'walk'
        };
        this.visitSecondScreenTimer = 6.5;
      }
    } else {
      this.userOnScreen2Seconds = Math.max(0, this.userOnScreen2Seconds - dt * 0.5);
    }

    // 2. Ritorno automatico sullo Schermo 1 dopo aver visitato lo Schermo 2
    if (this.worldCol >= renderer.cols - 2 && !this.target) {
      this.visitSecondScreenTimer -= dt;
      if (this.visitSecondScreenTimer <= 0) {
        this.target = {
          worldCol: Math.round(renderer.cols * (0.4 + Math.random() * 0.3)),
          row: Math.round(renderer.rows * (0.4 + Math.random() * 0.25)),
          type: 'walk'
        };
      }
    }

    // Se siamo in modalità AI, Coffee o Focus, il Neko resta nella zona centrale dello Schermo 1
    if (
      (state.activeMode === 'ai' || state.activeMode === 'coffee' || state.activeMode === 'focus') &&
      !this.isDraggingCat
    ) {
      this.toy = null;
      this.idleState = 'sit';
      const distHome = Math.hypot(this.homeCol - this.worldCol, this.homeRow - this.row);
      if (distHome > 3) {
        this.target = { worldCol: this.homeCol, row: this.homeRow, type: 'walk' };
      }
    }

    // 3. Movimento verso il target
    if (this.target && !this.isDraggingCat) {
      const dxT = this.target.worldCol - this.worldCol;
      const dyT = this.target.row - this.row;
      const distT = Math.hypot(dxT, dyT);

      if (distT > 2.0) {
        this.isWalking = true;
        this.facing = dxT >= 0 ? 1 : -1;
        this.walkPhase += dt * 13.0;
        const speed = 30.0;
        this.worldCol += (dxT / distT) * Math.min(distT, speed * dt);
        this.row += (dyT / distT) * Math.min(distT, speed * dt);
      } else {
        this.worldCol = this.target.worldCol;
        this.row = this.target.row;
        this.isWalking = false;
        const tType = this.target.type;
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
        }
      }
    } else {
      this.isWalking = false;
    }

    // 4. Ciclo delle Animazioni di Idle & Passeggiate autonome sullo Schermo 1
    if (
      state.activeMode === 'companion' &&
      !this.isWalking &&
      !this.isDraggingCat &&
      !this.target &&
      this.emotion === 'neutral'
    ) {
      this.idleStateTimer -= dt;
      if (this.idleStateTimer <= 0) {
        if (this.idleState === 'firefly') {
          // Balzo finale per acchiappare la lucciola!
          this.bounceVel = -9.0;
          this.spawnParticles('spark', 2);
          this.firefly = null;
        }
        this.pickNextIdleAnimation();
      }

      if (this.idleState === 'firefly' && this.firefly) {
        this.firefly.phase += dt * 3.5;
        this.firefly.ox = Math.cos(this.firefly.phase) * 9;
        this.firefly.oy = -5 + Math.sin(this.firefly.phase * 2) * 2.5;
      }

      // Passeggiata autonoma occasionale sullo Schermo 1
      if (this.roamEnabled && this.idleState === 'sit') {
        this.autonomousWalkTimer -= dt;
        if (this.autonomousWalkTimer <= 0) {
          this.autonomousWalkTimer = 20.0 + Math.random() * 18.0;
          this.target = {
            worldCol: Math.round(renderer.cols * (0.22 + Math.random() * 0.56)),
            row: Math.round(renderer.rows * (0.28 + Math.random() * 0.42)),
            type: 'walk'
          };
        }
      }
    } else {
      this.firefly = null;
    }

    // 5. Sguardo verso il mouse (globale su entrambi gli schermi)
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

    // Allunga la zampina se il mouse passa vicino (e siamo sul desktop)
    if (
      !this.isWalking &&
      !this.isDraggingCat &&
      this.emotion === 'neutral' &&
      this.idleState === 'sit' &&
      distM > 4 &&
      distM < 12
    ) {
      this.emotion = 'reaching';
      this.emotionTimer = 0.25;
    }

    // Carezze sulla testa
    const isOverHead = Math.abs(dxM) < 9 && dyM >= -6 && dyM <= 0;
    const mouseSpeed = Math.hypot(state.mouse.vx, state.mouse.vy);
    if (isOverHead && mouseSpeed > 0.2 && mouseSpeed < 9.0 && !this.isDraggingCat) {
      this.patScore += dt * 2.6;
      if (this.patScore > 0.8) {
        this.emotion = 'happy';
        this.emotionTimer = 2.6;
        this.idleState = 'sit';
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

    if ((state.audioEnergy || 0) > 0.05 && time - this.lastNoteSpawn > 1.4) {
      this.lastNoteSpawn = time;
      this.spawnParticles('music', 1);
    }
    if (state.activeMode === 'afk' && Math.random() < dt * 0.8) {
      this.spawnParticles('zzz', 1);
    }

    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.ox += p.vx * dt;
      p.oy += p.vy * dt;
      p.life -= p.decay;
      if (p.life <= 0) this.particles.splice(i, 1);
    }

    if (systemBridge) {
      systemBridge.publishCatState({
        worldCol: Math.round(this.worldCol * 10) / 10,
        row: Math.round(this.row * 10) / 10,
        facing: this.facing,
        isWalking: this.isWalking,
        walkPhase: Math.round(this.walkPhase * 10) / 10,
        emotion: this.emotion,
        idleState: this.idleState,
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

  getSimpleFace(time, state) {
    const mode = state.activeMode;
    const audio = state.audioEnergy || 0;
    const shift = this.lookX > 0.25 ? 1 : this.lookX < -0.25 ? -1 : 0;

    if (this.isDraggingCat) return '  > ω <  ';
    if (this.emotion === 'eating') return '  ˆ ڡ ˆ  ';
    if (this.emotion === 'happy') return ' //^ω^// ';
    if (this.emotion === 'surprised') return '  o ω o  ';
    if (mode === 'afk') return '  - ω -  ';
    if (mode === 'coffee') return '  ˘ ω ˘  ';
    if (mode === 'ai') return ' -•-ω-•- ';

    // Espressioni delle Animazioni di Idle
    if (this.idleState === 'groom') return '  ˘ ω ˘  ';
    if (this.idleState === 'stretch') {
      return this.idleStateTimer < 1.8 ? '  > 〇 < ' : '  ˘ ω ˘  ';
    }
    if (this.idleState === 'scratch') return '  > ω ^  ';
    if (this.idleState === 'firefly') return '  ⊙ ω ⊙  ';
    if (this.idleState === 'loaf') return '  ˘ ω ˘  ';

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

    // 1. Disegna eventuale Gomitolo (@)~ o Pescetto ><(((º>
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

    // Se il Neko in questo momento è sull'altro monitor, non disegnarlo qui
    if (localCatCol < -14 || localCatCol > renderer.cols + 14) {
      return;
    }

    const cx = localCatCol;
    const cy = Math.round(this.row + this.bounceY);
    const face = this.getSimpleFace(time, state);

    // 2. Disegna la Lucciola (*) se il Neko è nell'animazione 'firefly'
    if (this.idleState === 'firefly' && this.firefly && !this.isWalking) {
      const fx = Math.round(cx + this.firefly.ox);
      const fy = Math.round(cy + this.firefly.oy);
      renderer.setCell(fx, fy, '*', 7, true);
    }

    // 3. Disegna il Neko nella posa corrente
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
    } else if (state.activeMode === 'afk' || (this.idleState === 'loaf' && !this.isWalking)) {
      // Posa acciambellata a pagnotta (Cat Loaf / AFK)
      const lines = [
        '  /\\_____/\\  ',
        ` (${face})___ `,
        ' (             )╮',
        '  `────────────╯╰'
      ];
      this.drawArtLines(renderer, cx, cy - 2, lines, 7);
    } else if (this.isWalking) {
      // Camminata a 4 righe super pulita
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
    } else if (this.idleState === 'stretch') {
      // Stiracchiamento felino con zampe allungate in avanti e sbadiglio!
      const lines = [
        '    /\\_____/\\    ╭─╮',
        `   (${face})  ╭╯ │`,
        '  /           \\─╯  │',
        ' ∪──∪──────────∪───╯'
      ];
      this.drawArtLines(renderer, cx, cy - 2, lines, 7);
    } else if (this.idleState === 'groom') {
      // Si lecca la zampina passando sul musetto
      const lickUp = Math.sin(time * 7.0) > 0;
      const pawLine = lickUp ? '  / づ    \\  ╭' : '  /       \\  ╭';
      const faceGroom = lickUp ? '  ˘ ω ˘づ' : '  ˘ ω ˘  ';
      const lines = [
        '  /\\_____/\\  ',
        ` (${faceGroom}) `,
        pawLine,
        ' (  │   │  )─╯',
        '  `─∪───∪──\'  '
      ];
      this.drawArtLines(renderer, cx, cy - 3, lines, 7);
    } else if (this.idleState === 'scratch') {
      // Si gratta dietro l'orecchio
      const kick = Math.floor(time * 12) % 2 === 0 ? '彡' : '  ';
      const lines = [
        `  /\\_____/\\${kick}`,
        ` (${face}) `,
        '  /       \\ ╭ ',
        ' (  │   │  ∪╯ ',
        '  `─∪───∪──\'  '
      ];
      this.drawArtLines(renderer, cx, cy - 3, lines, 7);
    } else if (this.idleState === 'firefly') {
      // Accovacciato che punta la lucciola scodinzolando veloce!
      const wiggle = Math.floor(time * 14) % 2 === 0 ? '╭─╮' : '╰─╯';
      const lines = [
        '  /\\_____/\\     ',
        ` (${face})___ `,
        ` (  │   │      )${wiggle}`,
        '  `─∪───∪──────\'   '
      ];
      this.drawArtLines(renderer, cx, cy - 2, lines, 7);
    } else {
      // Posa Seduta Classica
      const tailUp = Math.sin(time * 2.5) > 0;
      const tTop = tailUp ? '  ╭' : '   ';
      const tMid = tailUp ? ')─╯' : ')─╮';
      const earRow = Math.sin(time * 1.8) > 0.9 ? '  /\\_____/\\~ ' : '  /\\_____/\\  ';

      let bodyLine = '  /       \\  ';
      if (this.emotion === 'reaching') {
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

    // 4. Accessori minimali per AI Work (Tastiera) e Pausa Caffè (Tazzina)
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

    // 5. Particelle emotive sopra il Neko
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
