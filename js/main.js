/**
 * main.js - Orchestratore principale del Neko Desktop Companion Multi-Schermo + Assistente AI Locale
 */
(function () {
  const canvas = document.getElementById('ascii-canvas');
  const renderer = new AsciiRenderer(canvas);
  const fluidField = new FluidField();

  const state = {
    screenIndex: window.screenX >= 1000 || window.screenLeft >= 1000 ? 1 : 0,
    screenCalibrated: false,
    screenManualOverride: false,
    sharedCat: null,
    globalMouse: null,
    activeMode: 'companion',
    previousUserMode: 'companion',
    manualAfk: false,
    aiAutoSync: true,
    modeElapsedSeconds: 0,
    idleSeconds: 0,
    afkThresholdSeconds: 180,
    audioEnergy: 0,
    mouse: {
      x: window.innerWidth * 0.5,
      y: window.innerHeight * 0.5,
      col: Math.floor(renderer.cols * 0.5),
      row: Math.floor(renderer.rows * 0.5),
      vx: 0,
      vy: 0,
      active: false,
      lastMoveTime: 0
    },
    system: {
      cpu: 18,
      ram: 42,
      aiWorking: false,
      aiSource: 'IDLE',
      aiDetail: '',
      bridgeOnline: false,
      isDesktopFocus: true,
      ollamaActive: '',
      ollamaModels: [],
      memoryCount: 0,
      todoCount: 0,
      calendar: null
    }
  };

  let hud = null;
  let systemBridge = null;

  const companion = new CompanionFace((eventType, extra) => {
    if (hud) {
      hud.triggerCompanionReaction(eventType, extra);
    }
  });

  function setMode(newMode, isManual = false) {
    if (state.activeMode === newMode && !isManual) return;

    if (isManual) {
      state.manualAfk = newMode === 'afk';
      if (newMode !== 'ai') {
        state.previousUserMode = newMode;
      }
    }

    state.activeMode = newMode;
    state.modeElapsedSeconds = 0;

    fluidField.spawnShockwave(Math.round(companion.worldCol), Math.round(companion.row), 20, 0.65);

    if (hud) {
      hud.updateModeUI(newMode);
    }
  }

  systemBridge = new SystemBridge(
    state,
    (isAiWorking) => {
      if (!state.aiAutoSync) return;
      if (state.activeMode === 'coffee') return;

      if (isAiWorking) {
        if (state.activeMode !== 'ai') {
          state.previousUserMode = state.activeMode;
          setMode('ai', false);
        }
      } else {
        if (state.activeMode === 'ai') {
          setMode(state.previousUserMode || 'companion', false);
        }
      }
    },
    (remoteCmd) => {
      companion.handleRemoteCommand(remoteCmd, renderer);
    }
  );

  hud = new HudTerminal(state, {
    setMode,
    toggleAudioDemo: () => systemBridge.toggleDemoAudio(),
    toggleRoamMode: () => companion.toggleRoamMode(),
    setDensity: (preset) => renderer.setDensity(preset),
    spawnFish: () => companion.spawnFishSnack(renderer, undefined, undefined, state, systemBridge),
    sendAiPrompt: (text) => systemBridge.sendAiPrompt(text),
    fetchChatHistory: () => systemBridge.fetchChatHistory(),
    clearChatHistory: () => systemBridge.clearChatHistory(),
    saveCalendarConfig: (url) => systemBridge.saveCalendarConfig(url),
    triggerPcAction: (path) => systemBridge.triggerPcAction(path)
  });

  window.addEventListener('resize', () => {
    renderer.resize();
  });

  window.addEventListener('mousemove', (e) => {
    const newCol = Math.floor(e.clientX / renderer.cellW);
    const newRow = Math.floor(e.clientY / renderer.cellH);

    state.mouse.vx = newCol - state.mouse.col;
    state.mouse.vy = newRow - state.mouse.row;
    state.mouse.x = e.clientX;
    state.mouse.y = e.clientY;
    state.mouse.col = newCol;
    state.mouse.row = newRow;
    state.mouse.active = true;
    state.mouse.lastMoveTime = performance.now();

    if (state.idleSeconds > 5 && state.activeMode === 'afk' && !state.manualAfk) {
      setMode(state.previousUserMode || 'companion', false);
    }
    state.idleSeconds = 0;

    companion.handleMouseMove(newCol, newRow, renderer, state);
    if (state.system.isDesktopFocus !== false) {
      fluidField.addMouseTrail(newCol, newRow, state.mouse.vx, state.mouse.vy);
    }
  });

  window.addEventListener('mousedown', (e) => {
    if (
      e.target.closest('#status-matrix-panel') ||
      e.target.closest('#ai-terminal-dock') ||
      e.target.closest('.hud-restore-pill') ||
      e.target.closest('#hud-deadline-toast')
    ) {
      return;
    }
    state.mouse.lastMoveTime = performance.now();

    const clickCol = Math.floor(e.clientX / renderer.cellW);
    const clickRow = Math.floor(e.clientY / renderer.cellH);

    const res = companion.handleMouseDown(clickCol, clickRow, e.button, renderer, state, systemBridge);
    if (res !== 'ignored_not_desktop') {
      fluidField.spawnShockwave(clickCol, clickRow, res === 'drag_cat' ? 14 : 18, 0.7);
    }
  });

  window.addEventListener('mouseup', () => {
    companion.handleMouseUp(state);
  });

  window.addEventListener('contextmenu', (e) => {
    if (
      e.target.closest('#ai-terminal-dock') ||
      e.target.closest('#status-matrix-panel') ||
      e.target.closest('#hud-deadline-toast')
    ) return;
    e.preventDefault();
  });

  window.addEventListener('keydown', (e) => {
    if (document.activeElement && document.activeElement.tagName === 'INPUT') return;

    if (e.key === '1') setMode('companion', true);
    else if (e.key === '2') setMode('ai', true);
    else if (e.key === '3') setMode('coffee', true);
    else if (e.key === '4') setMode('afk', true);
    else if (e.key === '5') setMode('focus', true);
    else if (e.key === ' ') {
      companion.spawnFishSnack(renderer, undefined, undefined, state, systemBridge);
    } else if (e.key.toLowerCase() === 'h') {
      if (hud && hud.cycleHudVisibility) {
        hud.cycleHudVisibility();
      }
    }
  });

  let lastTime = performance.now();
  let totalTime = 0;

  function animate(now) {
    const dt = Math.min(0.1, (now - lastTime) / 1000);
    lastTime = now;
    totalTime += dt;

    state.modeElapsedSeconds += dt;
    state.idleSeconds += dt;
    state.mouse.vx *= 0.85;
    state.mouse.vy *= 0.85;

    if (
      state.idleSeconds >= state.afkThresholdSeconds &&
      state.activeMode === 'companion' &&
      !state.system.aiWorking
    ) {
      state.manualAfk = false;
      setMode('afk', false);
    }

    systemBridge.update(dt, totalTime);
    renderer.clear();
    fluidField.updateAndRender(renderer, dt, totalTime, state);
    companion.update(dt, totalTime, state, renderer, systemBridge);
    companion.render(renderer, totalTime, state);
    renderer.render();
    hud.update(dt);

    requestAnimationFrame(animate);
  }

  requestAnimationFrame(animate);
})();
