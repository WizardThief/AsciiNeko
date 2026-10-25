/**
 * HudTerminal - Gestisce la Top Bar minimale e il Terminale AI Locale (Ollama) in basso.
 */
class HudTerminal {
  constructor(state, actions) {
    this.state = state;
    this.actions = actions;

    this.elHudLayer = document.getElementById('hud-layer');
    this.elCpuVal = document.getElementById('cpu-val');
    this.elRamVal = document.getElementById('ram-val');
    this.elAiDot = document.getElementById('ai-dot');

    this.elDialogue = document.getElementById('companion-dialogue');
    this.elAffection = document.getElementById('companion-affection');
    this.elClock = document.getElementById('clock-display');
    this.elTimerBadge = document.getElementById('session-timer-badge');
    this.elTimerLabel = document.getElementById('session-timer-label');
    this.elTimerValue = document.getElementById('session-timer-val');

    this.elAiDock = document.getElementById('ai-terminal-dock');
    this.elAiModelBadge = document.getElementById('ai-model-badge');
    this.elAiForm = document.getElementById('ai-input-form');
    this.elAiInput = document.getElementById('ai-input-field');

    this.elCloudBadge = document.getElementById('cloud-sync-badge');
    this.elMemPill = document.getElementById('mem-pill');
    this.elSkillsPill = document.getElementById('skills-pill');
    this.elDeadlineToast = document.getElementById('hud-deadline-toast');
    this.elDeadlineContent = document.getElementById('deadline-toast-content');
    this.elBtnDismissDeadline = document.getElementById('btn-dismiss-deadline');

    // Dirty-checking cache for 60 FPS zero-allocation updates
    this._lastCloudStatus = null;
    this._lastMemKey = null;
    this._lastSkillsKey = null;
    this._lastDeadlineHash = null;
    this._deadlineDismissedFor = null;

    this.currentMessage = '';
    this.targetMessage = '';
    this.charIndex = 0;
    this.typeTimer = 0;

    // Timer Pomodoro opzionale (conto alla rovescia)
    this.countdownRemaining = 0;

    this.bindEvents();
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
      this.elAffection.addEventListener('click', (e) => {
        e.stopPropagation();
        this.state.screenManualOverride = true;
        this.state.screenIndex = this.state.screenIndex === 0 ? 1 : 0;
      });
    }

    const btnFish = document.getElementById('btn-fish-snack');
    if (btnFish) {
      btnFish.addEventListener('click', (e) => {
        e.stopPropagation();
        this.actions.spawnFish();
      });
    }

    const btnStyle = document.getElementById('btn-face-style');
    if (btnStyle) {
      btnStyle.addEventListener('click', (e) => {
        e.stopPropagation();
        const roam = this.actions.toggleRoamMode();
        btnStyle.classList.toggle('active', roam);
        btnStyle.textContent = roam ? '🐾 LIBERO' : '🐾 FISSO';
      });
    }

    // Visibilità HUD (3 stati: FULL -> COMPACT -> ZEN)
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
      } else if (this.hudState === 'compact') {
        this.hudState = 'hidden';
        layer.classList.remove('hud-minimal');
        layer.classList.add('hud-hidden');
      } else {
        this.hudState = 'full';
        layer.classList.remove('hud-minimal', 'hud-hidden');
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
      });
    }

    if (this.elBtnDismissDeadline) {
      this.elBtnDismissDeadline.addEventListener('click', (e) => {
        e.stopPropagation();
        this._deadlineDismissedFor = this._lastDeadlineHash;
        if (this.elDeadlineToast) {
          this.elDeadlineToast.style.display = 'none';
        }
      });
    }

    if (this.elMemPill) {
      this.elMemPill.addEventListener('click', (e) => {
        e.stopPropagation();
        const mem = (this.state.system && this.state.system.memory) || { totalFacts: 0, activeProjects: 0, recentEpisodes: 0 };
        this.say(`🧠 Memoria Neko: ${mem.totalFacts} fatti memorizzati, ${mem.activeProjects} progetti attivi, ${mem.recentEpisodes} sessioni.`);
      });
    }

    if (this.elSkillsPill) {
      this.elSkillsPill.addEventListener('click', (e) => {
        e.stopPropagation();
        const sk = (this.state.system && this.state.system.skills) || { totalSkills: 0, activeSkills: [], lastExecutedSkill: null };
        const listStr = sk.activeSkills && sk.activeSkills.length ? sk.activeSkills.slice(0, 4).join(', ') : 'nessuna';
        this.say(`⚡ Skills Neko: ${sk.totalSkills} attive [${listStr}]. Ultima: ${sk.lastExecutedSkill || 'nessuna'}`);
      });
    }

    if (this.elCloudBadge) {
      this.elCloudBadge.addEventListener('click', (e) => {
        e.stopPropagation();
        const cs = (this.state.system && this.state.system.cloudSync) || { status: 'offline', cloudServerUrl: '127.0.0.1:8767', pendingMutations: 0, lastSyncTime: null };
        const lastSync = cs.lastSyncTime ? new Date(cs.lastSyncTime).toLocaleTimeString('it-IT') : 'mai';
        this.say(`☁️ Cloud Sync [${(cs.status || 'OFFLINE').toUpperCase()}]: server ${cs.cloudServerUrl || '127.0.0.1:8767'}, mutazioni pendenti: ${cs.pendingMutations || 0}, ultimo sync: ${lastSync}`);
      });
    }

    // Pulsante Riduci/Espandi Terminale AI
    const btnMinTerm = document.getElementById('btn-min-term');
    if (btnMinTerm && this.elAiDock) {
      btnMinTerm.addEventListener('click', (e) => {
        e.stopPropagation();
        const collapsed = this.elAiDock.classList.toggle('term-collapsed');
        btnMinTerm.textContent = collapsed ? '[▲]' : '[▼]';
      });
    }

    // Chip di Azione Rapida nel Terminale AI
    document.querySelectorAll('[data-ai-cmd]').forEach((chip) => {
      chip.addEventListener('click', (e) => {
        e.stopPropagation();
        const cmd = chip.getAttribute('data-ai-cmd');
        if (cmd === '__timer25__') {
          this.countdownRemaining = 25 * 60;
          this.actions.setMode('focus', true);
          this.say('⏱ Timer Focus di 25:00 avviato! Ti avviso io quando finisce. Buon lavoro!');
          return;
        }
        this.submitAiCommand(cmd);
      });
    });

    // Invio da tastiera nel Terminale AI
    if (this.elAiForm && this.elAiInput) {
      this.elAiForm.addEventListener('submit', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const text = this.elAiInput.value.trim();
        if (!text) return;
        this.elAiInput.value = '';
        this.submitAiCommand(text);
      });
    }
  }

  async submitAiCommand(text) {
    this.say(`⏳ Elaborazione: "${text}"...`);
    if (this.actions.sendAiPrompt) {
      const res = await this.actions.sendAiPrompt(text);
      if (res && res.reply) {
        this.say(res.reply);
      }
    }
  }

  say(text) {
    this.targetMessage = text;
    this.currentMessage = '';
    this.charIndex = 0;
  }

  triggerCompanionReaction(eventType) {
    // Non sovrascrivere il terminale AI con inutili messaggi di log automatici!
  }

  updateModeUI(mode) {
    document.querySelectorAll('[data-mode-btn]').forEach((btn) => {
      const isMatch = btn.getAttribute('data-mode-btn') === mode;
      btn.classList.toggle('active', isMatch);
    });

    if (mode === 'coffee' || mode === 'focus' || mode === 'afk' || this.countdownRemaining > 0) {
      this.elTimerBadge.classList.add('visible');
      const icons = { coffee: '☕', focus: '🎯', afk: '🌙' };
      this.elTimerLabel.textContent = icons[mode] || '⏱';
    } else {
      this.elTimerBadge.classList.remove('visible');
    }
  }

  formatDuration(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }

  update(dt) {
    // Nascondi il terminale AI sullo Schermo 2 (Secondario) per mantenerlo ultra-pulito
    if (this.elAiDock) {
      this.elAiDock.style.display = this.state.screenIndex === 0 ? '' : 'none';
    }

    // Typewriter nel terminale AI
    if (this.charIndex < this.targetMessage.length) {
      this.typeTimer += dt;
      while (this.typeTimer > 0.012 && this.charIndex < this.targetMessage.length) {
        this.typeTimer -= 0.012;
        this.charIndex++;
        this.currentMessage = this.targetMessage.slice(0, this.charIndex);
        if (this.elDialogue) {
          this.elDialogue.textContent = this.currentMessage;
        }
      }
    }

    // Countdown Timer
    if (this.countdownRemaining > 0) {
      this.countdownRemaining -= dt;
      if (this.countdownRemaining <= 0) {
        this.countdownRemaining = 0;
        this.actions.setMode('companion', true);
        this.actions.spawnFish();
        this.say('🔔 Miao!! ⏱ Il Timer di 25 minuti è terminato! Prenditi una pausa!');
      }
    }

    if (this.elCpuVal) this.elCpuVal.textContent = `${this.state.system.cpu}%`;
    if (this.elRamVal) this.elRamVal.textContent = `${this.state.system.ram}%`;

    const aiActive = this.state.activeMode === 'ai' || this.state.system.aiWorking;
    if (this.elAiDot) {
      this.elAiDot.classList.toggle('ai-active', aiActive);
    }

    if (this.elAiModelBadge) {
      this.elAiModelBadge.textContent = this.state.system.ollamaActive || 'OLLAMA LOCAL';
    }

    if (this.elAffection) {
      this.elAffection.textContent = this.state.screenIndex === 0 ? '🖥 S1 ★' : '🖥 S2';
    }

    const now = new Date();
    if (this.elClock) {
      this.elClock.textContent = now.toLocaleTimeString('it-IT', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      });
    }

    if (this.elTimerValue) {
      const secToShow =
        this.countdownRemaining > 0 ? this.countdownRemaining : this.state.modeElapsedSeconds || 0;
      this.elTimerValue.textContent = this.formatDuration(secToShow);
    }

    // --- Dirty-Checked Ecosystem Telemetry Updates (< 0.1ms per frame for 60 FPS) ---
    // 1. Cloud Sync Badge
    const cs = this.state.system ? this.state.system.cloudSync : null;
    const rawStatus = (cs && cs.status) ? cs.status.toLowerCase() : 'offline';
    const pending = (cs && cs.pendingMutations) ? cs.pendingMutations : 0;
    const statusMap = {
      synced: { text: '[CLOUD: SYNCED]', cls: 'cloud-synced' },
      syncing: { text: '[CLOUD: SYNCING]', cls: 'cloud-syncing' },
      offline: { text: pending > 0 ? `[CLOUD: OFFLINE (Q: ${pending})]` : '[CLOUD: OFFLINE]', cls: 'cloud-offline' },
      error: { text: '[CLOUD: ERROR]', cls: 'cloud-error' }
    };
    const targetStatus = statusMap[rawStatus] || statusMap['offline'];
    const cloudKey = `${rawStatus}:${pending}`;
    if (this.elCloudBadge && this._lastCloudStatus !== cloudKey) {
      this._lastCloudStatus = cloudKey;
      this.elCloudBadge.textContent = targetStatus.text;
      this.elCloudBadge.className = `hud-badge hud-badge-cloud ${targetStatus.cls}`;
      const syncInfo = cs && cs.lastSyncTime ? `Ultimo sync: ${cs.lastSyncTime}` : 'Nessun sync completato';
      this.elCloudBadge.title = `Cloud Sync: ${rawStatus.toUpperCase()} (${syncInfo}, Pendenti: ${pending})`;
    }

    // 2. Memory Pill: [MEM: 12f/3p]
    const mem = this.state.system ? this.state.system.memory : null;
    const memKey = mem ? `${mem.totalFacts || 0}f/${mem.activeProjects || 0}p` : '0f/0p';
    if (this.elMemPill && this._lastMemKey !== memKey) {
      this._lastMemKey = memKey;
      this.elMemPill.textContent = `[MEM: ${memKey}]`;
      this.elMemPill.title = `Memoria Persistente: ${mem ? mem.totalFacts || 0 : 0} Fatti, ${mem ? mem.activeProjects || 0 : 0} Progetti, ${mem ? mem.recentEpisodes || 0 : 0} Episodi`;
    }

    // 3. Skills Pill: [SKILLS: 5 active]
    const sk = this.state.system ? this.state.system.skills : null;
    const skillsKey = sk ? `${sk.totalSkills || 0}` : '0';
    if (this.elSkillsPill && this._lastSkillsKey !== skillsKey) {
      this._lastSkillsKey = skillsKey;
      this.elSkillsPill.textContent = `[SKILLS: ${skillsKey} active]`;
      const lastSk = sk && sk.lastExecutedSkill ? ` (Ultima: ${sk.lastExecutedSkill})` : '';
      this.elSkillsPill.title = `Abilità Autonome: ${skillsKey} registrate${lastSk}`;
    }

    // 4. Upcoming Deadline Warning Banner / Toast
    if (this.elDeadlineToast) {
      const deadlines = mem && Array.isArray(mem.upcomingDeadlines) ? mem.upcomingDeadlines : [];
      const urgentOrHigh = deadlines.filter(d => {
        const risk = (d.risk || '').toLowerCase();
        return d.overdue === true || risk === 'urgent' || risk === 'high' || risk === 'critical';
      });

      if (urgentOrHigh.length > 0) {
        const deadlineHash = urgentOrHigh.map(d => `${d.project || d.name}:${d.milestone}:${d.risk}:${d.due || d.deadline_utc}:${d.overdue}`).join('|');
        if (this._lastDeadlineHash !== deadlineHash) {
          this._lastDeadlineHash = deadlineHash;
          if (this._deadlineDismissedFor !== deadlineHash) {
            let html = '';
            for (const item of urgentOrHigh.slice(0, 3)) {
              const overdueLabel = item.overdue ? '<strong style="color:#ef4444;">[SCADUTO!]</strong> ' : '';
              const dueStr = item.due ? String(item.due).split('T')[0] : (item.deadline_utc ? String(item.deadline_utc).split('T')[0] : 'N/D');
              const riskStr = (item.risk || 'URGENTE').toUpperCase();
              const projName = item.project || item.name || 'Progetto';
              html += `<div class="deadline-toast-item">${overdueLabel}<strong>${this.escapeHtml(projName)}</strong> (${this.escapeHtml(item.milestone || 'Milestone')}): Entro ${dueStr} [${riskStr}]</div>`;
            }
            if (this.elDeadlineContent) {
              this.elDeadlineContent.innerHTML = html;
            }
            this.elDeadlineToast.style.display = 'block';
          }
        }
      } else {
        if (this._lastDeadlineHash !== null) {
          this.elDeadlineToast.style.display = 'none';
          this._lastDeadlineHash = null;
          this._deadlineDismissedFor = null;
        }
      }
    }
  }

  escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
}

window.HudTerminal = HudTerminal;
