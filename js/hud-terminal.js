/**
 * HudTerminal - Gestisce i pannelli terminale dell'interfaccia, l'adattamento automatico
 * alle cartelle del Desktop di Windows e i dialoghi del Neko.
 */
class HudTerminal {
  constructor(state, actions) {
    this.state = state;
    this.actions = actions;

    this.elHudLayer = document.getElementById('hud-layer');
    this.elCpuBar = document.getElementById('cpu-bar');
    this.elCpuVal = document.getElementById('cpu-val');
    this.elRamBar = document.getElementById('ram-bar');
    this.elRamVal = document.getElementById('ram-val');
    this.elAiBanner = document.getElementById('ai-status-banner');
    this.elAiText = document.getElementById('ai-status-text');
    this.elBridgeBadge = document.getElementById('bridge-badge');
    this.elSystemPanel = document.getElementById('system-panel');
    this.elFoldersBar = document.getElementById('desktop-folders-bar');

    this.elDialogue = document.getElementById('companion-dialogue');
    this.elDialogueMode = document.getElementById('dialogue-mode-tag');
    this.elAffection = document.getElementById('companion-affection');

    this.elClock = document.getElementById('clock-display');
    this.elDate = document.getElementById('date-display');
    this.elSpectrum = document.getElementById('audio-spectrum');
    this.elTimerBadge = document.getElementById('session-timer-badge');
    this.elTimerLabel = document.getElementById('session-timer-label');
    this.elTimerValue = document.getElementById('session-timer-val');

    this.currentMessage = '';
    this.targetMessage = '';
    this.charIndex = 0;
    this.typeTimer = 0;
    this.idleQuoteTimer = 18.0;
    this.affectionLevel = 1;
    this.lastFoldersSig = '';

    this.quotes = {
      companion: [
        'Nyaa~ Clicca su una tua cartella del Desktop per farmici saltare sopra, o trascinami col mouse!',
        '*Dondola la coda* ... Tasto sinistro per il gomitolo (@), tasto destro per lanciarmi un pescetto ><(((º>!',
        '*Guarda curioso le tue cartelle sul desktop* ... Posso arrampicarmi su [EPO] o [DIE_ASSETS]!',
        'Passami il mouse sulla testa per le fusa (//^ω^//) o afferrami per la collottola per spostarmi!'
      ],
      ai: [
        'Nyaa! ⚡ Un\'AI sta lavorando! *Digita veloce sulla tastiera meccanica con gli occhialini!*',
        'Elaborazione AI rilevata sul PC! Controllo io il codice insieme a te.'
      ],
      coffee: [
        'Miao~ ☕ Pausa Caffè! Mi rilasso accanto alla tazzina fumante c[_].',
        '*Fa le fusa piano* ... Goditi il caffè, sorveglio io le cartelle del desktop!'
      ],
      afk: [
        '*Si acciambella per fare un pisolino... zZz... Muovi il mouse per svegliarmi.*',
        'Modalità sonnellino AFK attiva... sognando pesciolini ASCII...'
      ],
      focus: [
        'Shhh... 🐾 Modalità Focus: resto buono e silenzioso senza distrarti.'
      ],
      poke: [
        'Mrrp?! ( o ω o )! Mi hai preso! Trascinami pure dove vuoi o posami su una cartella!',
        'Nyaa! *Si agita con le zampette penzoloni ( > ω < )!*'
      ],
      pat: [
        'Purrrr... ♡ ( //^ω^// ) *Fa le fusa felice mentre lo accarezzi!*',
        'Nyaa~ ♡ Così mi vizi! Livello affinità aumentato!'
      ],
      yarn: [
        'Il gomitolo (@)~!! *Trotta veloce per acchiapparlo!*',
        'Miao! *Corre dietro al gomitolo attraverso il desktop!* 🐾'
      ],
      fish: [
        'Un pescetto ><(((º>!! *Corre a mangiarlo felice ( ˆ ڡ ˆ )!* Buonissimo! ♡',
        'MGNAM! *Sgranocchia lo snack facendo le fusa!* Grazie!! 🐟'
      ]
    };

    this.bindEvents();
    this.say('Miao! 🐾 Vedo le tue cartelle del Desktop! Cliccaci sopra o trascinami per giocare!');
  }

  bindEvents() {
    document.querySelectorAll('[data-mode-btn]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const mode = btn.getAttribute('data-mode-btn');
        this.actions.setMode(mode, true);
      });
    });

    if (this.elAffection) {
      this.elAffection.style.cursor = 'pointer';
      this.elAffection.title = 'Clicca per cambiare manualmente tra Schermo 1 (Principale) e Schermo 2 (Secondario)';
      this.elAffection.addEventListener('click', (e) => {
        e.stopPropagation();
        this.state.screenManualOverride = true;
        this.state.screenIndex = this.state.screenIndex === 0 ? 1 : 0;
        this.lastFoldersSig = '';
      });
    }
    const btnFish = document.getElementById('btn-fish-snack');
    if (btnFish) {
      btnFish.addEventListener('click', (e) => {
        e.stopPropagation();
        this.actions.spawnFish();
      });
    }

    const btnAiAuto = document.getElementById('btn-ai-auto');
    if (btnAiAuto) {
      btnAiAuto.addEventListener('click', (e) => {
        e.stopPropagation();
        this.state.aiAutoSync = !this.state.aiAutoSync;
        btnAiAuto.classList.toggle('active', this.state.aiAutoSync);
        btnAiAuto.textContent = this.state.aiAutoSync ? '[🤖 AUTO-AI: ON]' : '[🤖 AUTO-AI: OFF]';
      });
    }

    const btnAudio = document.getElementById('btn-audio-demo');
    if (btnAudio) {
      btnAudio.addEventListener('click', (e) => {
        e.stopPropagation();
        const active = this.actions.toggleAudioDemo();
        btnAudio.classList.toggle('active', active);
        btnAudio.textContent = active ? '[♪ AUDIO: SYNTH ON]' : '[♪ AUDIO: PULSE]';
      });
    }

    const btnStyle = document.getElementById('btn-face-style');
    if (btnStyle) {
      btnStyle.addEventListener('click', (e) => {
        e.stopPropagation();
        const roam = this.actions.toggleRoamMode();
        btnStyle.classList.toggle('active', roam);
        btnStyle.textContent = roam ? '[🐾 ROAM: LIBERO]' : '[🐾 ROAM: CENTRO]';
        this.say(
          roam
            ? 'Movimento libero attivo! Giocherò con le tue cartelle del Desktop!'
            : 'Resto fermo al centro della scrivania.'
        );
      });
    }

    const btnDensity = document.getElementById('btn-density');
    if (btnDensity) {
      const presets = ['balanced', 'compact', 'large'];
      let idx = 0;
      btnDensity.addEventListener('click', (e) => {
        e.stopPropagation();
        idx = (idx + 1) % presets.length;
        this.actions.setDensity(presets[idx]);
        btnDensity.textContent = `[# GRID: ${presets[idx].toUpperCase()}]`;
      });
    }

    const btnHud = document.getElementById('btn-toggle-hud');
    const btnRestoreHud = document.getElementById('btn-restore-hud');
    const layer = document.getElementById('hud-layer');
    this.hudState = 'full';

    this.cycleHudVisibility = () => {
      if (!layer) return;
      if (this.hudState === 'full') {
        this.hudState = 'compact';
        layer.classList.add('hud-minimal');
        layer.classList.remove('hud-hidden');
        if (btnHud) btnHud.textContent = '[👁 HUD: COMPACT]';
      } else if (this.hudState === 'compact') {
        this.hudState = 'hidden';
        layer.classList.remove('hud-minimal');
        layer.classList.add('hud-hidden');
        if (btnHud) btnHud.textContent = '[👁 HUD: ZEN]';
      } else {
        this.hudState = 'full';
        layer.classList.remove('hud-minimal', 'hud-hidden');
        if (btnHud) btnHud.textContent = '[👁 HUD: FULL]';
      }
    };

    if (btnHud) {
      btnHud.addEventListener('click', (e) => {
        e.stopPropagation();
        this.cycleHudVisibility();
      });
    }

    if (btnRestoreHud) {
      btnRestoreHud.addEventListener('click', (e) => {
        e.stopPropagation();
        this.hudState = 'full';
        layer.classList.remove('hud-minimal', 'hud-hidden');
        if (btnHud) btnHud.textContent = '[👁 HUD: FULL]';
      });
    }
  }

  /**
   * Adatta il posizionamento dell'HUD e aggiorna i pulsanti delle cartelle reali del Desktop
   */
  syncDesktopHud(folders) {
    // Se questo è lo Schermo 2 (Secondario), nascondi il pannello delle cartelle duplicato e usa il layout pulito!
    const isSecondary = this.state.screenIndex !== 0;
    if (this.elSystemPanel) {
      this.elSystemPanel.style.display = isSecondary ? 'none' : '';
    }
    if (this.elHudLayer) {
      if (isSecondary) {
        this.elHudLayer.classList.remove('desktop-safe-1col', 'desktop-safe-2col');
      } else if (folders && folders.length > 0) {
        this.elHudLayer.classList.remove('desktop-safe-1col', 'desktop-safe-2col');
        if (folders.length > 4) {
          this.elHudLayer.classList.add('desktop-safe-2col');
        } else {
          this.elHudLayer.classList.add('desktop-safe-1col');
        }
      }
    }

    if (isSecondary || !folders || !folders.length) return;
    const sig = folders.map((f) => f.name).join('|');
    if (sig === this.lastFoldersSig) return;
    this.lastFoldersSig = sig;

    if (this.elFoldersBar) {
      this.elFoldersBar.innerHTML = '';
      folders.forEach((f) => {
        const chip = document.createElement('button');
        chip.className = 'folder-chip';
        const icon = f.type === 'folder' ? '📁' : '⚙';
        chip.textContent = `${icon} ${f.name}`;
        chip.addEventListener('click', (e) => {
          e.stopPropagation();
          this.actions.playWithFolder(f);
        });
        this.elFoldersBar.appendChild(chip);
      });
    }
  }

  say(text) {
    this.targetMessage = text;
    this.currentMessage = '';
    this.charIndex = 0;
    this.idleQuoteTimer = 22.0;
  }

  triggerCompanionReaction(eventType, extraData = '') {
    if (eventType === 'pat' || eventType === 'fish') {
      this.affectionLevel = Math.min(99, this.affectionLevel + 1);
      if (this.elAffection) {
        this.elAffection.textContent = `LOVE LVL ${this.affectionLevel}`;
      }
    }
    if (eventType === 'folder_play' && extraData) {
      const msgs = [
        `Nyaa~ Vado a curiosare sopra la tua cartella [${extraData}]! 🐾`,
        `*Corre verso [${extraData}] e ci fa capolino da dietro!*`,
        `*Dà un colpetto con la zampina a [${extraData}]!* Che c'è qui dentro?`
      ];
      this.say(msgs[Math.floor(Math.random() * msgs.length)]);
      return;
    }
    if (eventType === 'folder_drop' && extraData) {
      this.say(`Miao! ♡ Mi hai posato proprio sopra [${extraData}]! Che bel posto comodo!`);
      return;
    }

    const list = this.quotes[eventType];
    if (list && list.length > 0) {
      const msg = list[Math.floor(Math.random() * list.length)];
      this.say(msg);
    }
  }

  updateModeUI(mode) {
    document.querySelectorAll('[data-mode-btn]').forEach((btn) => {
      const isMatch = btn.getAttribute('data-mode-btn') === mode;
      btn.classList.toggle('active', isMatch);
    });

    if (this.elDialogueMode) {
      this.elDialogueMode.textContent = `MODE // ${mode.toUpperCase()}`;
    }

    if (mode === 'coffee' || mode === 'focus' || mode === 'afk') {
      this.elTimerBadge.classList.add('visible');
      const labels = {
        coffee: '☕ COFFEE BREAK TIMER',
        focus: '🎯 FOCUS SESSION',
        afk: '🌙 AFK STANDBY TIME'
      };
      this.elTimerLabel.textContent = labels[mode] || 'SESSION';
    } else {
      this.elTimerBadge.classList.remove('visible');
    }

    this.triggerCompanionReaction(mode);
  }

  makeAsciiBar(percent, length = 12) {
    const filled = Math.round((Math.max(0, Math.min(100, percent)) / 100) * length);
    return '[' + '|'.repeat(filled) + '·'.repeat(Math.max(0, length - filled)) + ']';
  }

  formatDuration(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }

  update(dt, systemBridge, companionFolders) {
    if (companionFolders) {
      this.syncDesktopHud(companionFolders);
    }

    if (this.charIndex < this.targetMessage.length) {
      this.typeTimer += dt;
      while (this.typeTimer > 0.018 && this.charIndex < this.targetMessage.length) {
        this.typeTimer -= 0.018;
        this.charIndex++;
        this.currentMessage = this.targetMessage.slice(0, this.charIndex);
        if (this.elDialogue) {
          this.elDialogue.textContent = this.currentMessage;
        }
      }
    }

    this.idleQuoteTimer -= dt;
    if (this.idleQuoteTimer <= 0) {
      this.triggerCompanionReaction(this.state.activeMode);
      this.idleQuoteTimer = 24.0 + Math.random() * 14.0;
    }

    const cpu = this.state.system.cpu;
    const ram = this.state.system.ram;
    if (this.elCpuBar) this.elCpuBar.textContent = this.makeAsciiBar(cpu, 12);
    if (this.elCpuVal) this.elCpuVal.textContent = `${cpu}%`;
    if (this.elRamBar) this.elRamBar.textContent = this.makeAsciiBar(ram, 12);
    if (this.elRamVal) this.elRamVal.textContent = `${ram}%`;

    if (this.elBridgeBadge) {
      this.elBridgeBadge.textContent = this.state.system.bridgeOnline ? 'DESKTOP SYNC: LIVE' : 'DESKTOP: DEFAULT';
    }
    if (this.elAffection) {
      this.elAffection.textContent =
        this.state.screenIndex === 0
          ? `[🖥 SCHERMO 1 ★ · LVL ${this.affectionLevel}]`
          : '[🖥 SCHERMO 2 // SECONDARIO]';
    }

    const aiActive = this.state.activeMode === 'ai' || this.state.system.aiWorking;
    if (this.elAiBanner) this.elAiBanner.classList.toggle('ai-working', aiActive);
    if (this.elSystemPanel) this.elSystemPanel.classList.toggle('active-alert', aiActive);
    if (this.elAiText) {
      if (this.state.system.aiWorking) {
        this.elAiText.textContent = `⚡ AI ACTIVE: ${this.state.system.aiSource}`;
      } else if (this.state.activeMode === 'ai') {
        this.elAiText.textContent = '⚡ AI MODE: MANUAL OVERRIDE';
      } else {
        this.elAiText.textContent = 'AI MONITOR: STANDBY (WATCHING)';
      }
    }

    const now = new Date();
    if (this.elClock) this.elClock.textContent = now.toLocaleTimeString('it-IT', { hour12: false });
    if (this.elDate) {
      this.elDate.textContent = now
        .toLocaleDateString('it-IT', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })
        .toUpperCase();
    }
    if (this.elTimerValue && this.state.modeElapsedSeconds !== undefined) {
      this.elTimerValue.textContent = this.formatDuration(this.state.modeElapsedSeconds);
    }

    if (this.elSpectrum && systemBridge) {
      this.elSpectrum.textContent = systemBridge.getAsciiSpectrum();
    }
  }
}

window.HudTerminal = HudTerminal;
