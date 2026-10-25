/**
 * HudTerminal - Gestisce la Top Bar minimale e il Terminale Assistente Locale (Ollama, Cronologia Chat,
 * Google Calendar, Memoria e Controllo PC) in basso.
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
    this.elActionButtons = document.getElementById('ai-action-buttons');
    this.elAffection = document.getElementById('companion-affection');
    this.elClock = document.getElementById('clock-display');
    this.elTimerBadge = document.getElementById('session-timer-badge');
    this.elTimerLabel = document.getElementById('session-timer-label');
    this.elTimerValue = document.getElementById('session-timer-val');

    this.elAiDock = document.getElementById('ai-terminal-dock');
    this.elAiModelBadge = document.getElementById('ai-model-badge');
    this.elAiForm = document.getElementById('ai-input-form');
    this.elAiInput = document.getElementById('ai-input-field');

    this.elAgendaPill = document.getElementById('agenda-pill');
    this.elMemPill = document.getElementById('mem-pill');
    this.elDeadlineToast = document.getElementById('hud-deadline-toast');
    this.elDeadlineContent = document.getElementById('deadline-toast-content');
    this.elBtnDismissDeadline = document.getElementById('btn-dismiss-deadline');

    this.elBtnToggleHistory = document.getElementById('btn-toggle-history');
    this.elHistoryPanel = document.getElementById('chat-history-panel');
    this.elHistoryList = document.getElementById('chat-history-list');
    this.elBtnClearHistory = document.getElementById('btn-clear-history');

    this.elBtnCalSettings = document.getElementById('btn-cal-settings');
    this.elCalDrawer = document.getElementById('cal-config-drawer');
    this.elCalInput = document.getElementById('cal-ical-input');
    this.elBtnSaveIcal = document.getElementById('btn-save-ical');
    this.elBtnOpenGcalWeb = document.getElementById('btn-open-gcal-web');

    // Dirty-checking cache for 60 FPS zero-allocation updates
    this._lastAgendaKey = null;
    this._lastMemKey = null;
    this._lastImminentHash = null;
    this._imminentDismissedFor = null;

    this.currentMessage = '';
    this.targetMessage = '';
    this.charIndex = 0;
    this.typeTimer = 0;
    this.countdownRemaining = 0;

    // Cronologia comandi digitati (Freccia Su / Giù)
    this.sentCommands = [];
    this.cmdCursor = -1;

    this.bindEvents();
    this.loadInitialHistory();
  }

  async loadInitialHistory() {
    if (!this.actions.fetchChatHistory) return;
    const data = await this.actions.fetchChatHistory();
    if (data && Array.isArray(data.history) && data.history.length > 0) {
      this.renderHistoryList(data.history);
      const lastAssistant = [...data.history].reverse().find((t) => t.role === 'assistant');
      if (lastAssistant && lastAssistant.text) {
        this.say(lastAssistant.text, lastAssistant.actions || []);
      }
      this.sentCommands = data.history
        .filter((t) => t.role === 'user' && t.text)
        .map((t) => t.text)
        .slice(-30);
      this.cmdCursor = this.sentCommands.length;
    }
    if (data && data.calendar && Array.isArray(data.calendar.ical_urls) && data.calendar.ical_urls[0] && this.elCalInput) {
      this.elCalInput.value = data.calendar.ical_urls[0];
    }
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
        this._imminentDismissedFor = this._lastImminentHash;
        if (this.elDeadlineToast) {
          this.elDeadlineToast.style.display = 'none';
        }
      });
    }

    if (this.elAgendaPill) {
      this.elAgendaPill.addEventListener('click', (e) => {
        e.stopPropagation();
        this.submitAiCommand('/cal');
      });
    }

    if (this.elMemPill) {
      this.elMemPill.addEventListener('click', (e) => {
        e.stopPropagation();
        this.submitAiCommand('/mem');
      });
    }

    // Toggle Cronologia Chat (📜 STORICO)
    if (this.elBtnToggleHistory && this.elHistoryPanel) {
      this.elBtnToggleHistory.addEventListener('click', async (e) => {
        e.stopPropagation();
        const isHidden = this.elHistoryPanel.style.display === 'none';
        this.elHistoryPanel.style.display = isHidden ? 'flex' : 'none';
        this.elBtnToggleHistory.classList.toggle('active', isHidden);
        if (isHidden && this.actions.fetchChatHistory) {
          const data = await this.actions.fetchChatHistory();
          if (data && Array.isArray(data.history)) {
            this.renderHistoryList(data.history);
          }
        }
      });
    }

    // Pulisci Cronologia Chat
    if (this.elBtnClearHistory) {
      this.elBtnClearHistory.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (this.actions.clearChatHistory) {
          await this.actions.clearChatHistory();
        }
        this.renderHistoryList([]);
        this.say('🧹 Cronologia chat pulita! (Le tue note in 🧠 Memoria e l\'Agenda restano salve).');
      });
    }

    // Toggle Pannello Impostazioni Google Calendar (⚙️ CAL)
    if (this.elBtnCalSettings && this.elCalDrawer) {
      this.elBtnCalSettings.addEventListener('click', (e) => {
        e.stopPropagation();
        const isHidden = this.elCalDrawer.style.display === 'none';
        this.elCalDrawer.style.display = isHidden ? 'flex' : 'none';
        this.elBtnCalSettings.classList.toggle('active', isHidden);
        const cal = this.state.system && this.state.system.calendar;
        if (isHidden && cal && Array.isArray(cal.ical_urls) && cal.ical_urls[0] && this.elCalInput && !this.elCalInput.value) {
          this.elCalInput.value = cal.ical_urls[0];
        }
      });
    }

    if (this.elBtnSaveIcal && this.elCalInput) {
      this.elBtnSaveIcal.addEventListener('click', async (e) => {
        e.stopPropagation();
        const url = this.elCalInput.value.trim();
        this.say('⏳ Collegamento a Google Calendar in corso...');
        if (this.actions.saveCalendarConfig) {
          const res = await this.actions.saveCalendarConfig(url);
          if (res && res.ok) {
            const count = res.calendar ? res.calendar.upcoming_count : 0;
            this.say(
              url
                ? `📅 Google Calendar collegato! Ho trovato ${count} appuntamenti nei prossimi 7 giorni.`
                : '📅 Link Google Calendar rimosso (restano attivi gli impegni locali).'
            );
            if (this.elCalDrawer) this.elCalDrawer.style.display = 'none';
            if (this.elBtnCalSettings) this.elBtnCalSettings.classList.remove('active');
          }
        }
      });
    }

    if (this.elBtnOpenGcalWeb) {
      this.elBtnOpenGcalWeb.addEventListener('click', (e) => {
        e.stopPropagation();
        if (this.actions.triggerPcAction) {
          this.actions.triggerPcAction('https://calendar.google.com');
        } else {
          window.open('https://calendar.google.com', '_blank');
        }
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

    // Invio da tastiera nel Terminale AI + Frecce Su/Giù per richiamare comandi
    if (this.elAiForm && this.elAiInput) {
      this.elAiInput.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowUp' && this.sentCommands.length > 0) {
          e.preventDefault();
          this.cmdCursor = Math.max(0, this.cmdCursor - 1);
          this.elAiInput.value = this.sentCommands[this.cmdCursor] || '';
        } else if (e.key === 'ArrowDown' && this.sentCommands.length > 0) {
          e.preventDefault();
          this.cmdCursor = Math.min(this.sentCommands.length, this.cmdCursor + 1);
          this.elAiInput.value = this.sentCommands[this.cmdCursor] || '';
        }
      });

      this.elAiForm.addEventListener('submit', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const text = this.elAiInput.value.trim();
        if (!text) return;
        this.sentCommands.push(text);
        this.cmdCursor = this.sentCommands.length;
        this.elAiInput.value = '';
        this.submitAiCommand(text);
      });
    }
  }

  renderHistoryList(historyArray) {
    if (!this.elHistoryList) return;
    if (!historyArray || historyArray.length === 0) {
      this.elHistoryList.innerHTML =
        '<div class="chat-turn"><span style="color:var(--text-muted)">Nessun messaggio in cronologia.</span></div>';
      return;
    }

    let html = '';
    for (const item of historyArray) {
      const isUser = item.role === 'user';
      const cls = isUser ? 'turn-user' : 'turn-assistant';
      const who = isUser ? 'TU' : `NEKO (${this.escapeHtml(item.model || 'AI')})`;
      const timeStr = this.escapeHtml(item.time || '');
      html += `<div class="chat-turn ${cls}">
        <div class="chat-turn-meta"><strong>${who}</strong><span>${timeStr}</span></div>
        <div class="chat-turn-text">${this.escapeHtml(item.text || '')}</div>
      </div>`;
    }
    this.elHistoryList.innerHTML = html;
    this.elHistoryList.scrollTop = this.elHistoryList.scrollHeight;
  }

  renderActionButtons(actions) {
    if (!this.elActionButtons) return;
    if (!Array.isArray(actions) || actions.length === 0) {
      this.elActionButtons.style.display = 'none';
      this.elActionButtons.innerHTML = '';
      return;
    }

    this.elActionButtons.innerHTML = '';
    for (const act of actions) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'ai-action-btn';
      btn.textContent = act.label || 'Apri';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (act.url) {
          if (this.actions.triggerPcAction) {
            this.actions.triggerPcAction(act.url);
          } else {
            window.open(act.url, '_blank');
          }
        } else if (act.open_path && this.actions.triggerPcAction) {
          this.actions.triggerPcAction(act.open_path);
        }
      });
      this.elActionButtons.appendChild(btn);
    }
    this.elActionButtons.style.display = 'flex';
  }

  async submitAiCommand(text) {
    this.say(`⏳ Elaborazione: "${text}"...`, []);
    if (this.actions.sendAiPrompt) {
      const res = await this.actions.sendAiPrompt(text);
      if (res && res.reply) {
        this.say(res.reply, res.actions || []);
      }
      if (res && Array.isArray(res.history)) {
        this.renderHistoryList(res.history);
      }
    }
  }

  say(text, actions = []) {
    this.targetMessage = text;
    this.currentMessage = '';
    this.charIndex = 0;
    this.renderActionButtons(actions);
  }

  triggerCompanionReaction(eventType) {}

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
    if (this.elAiDock) {
      this.elAiDock.style.display = this.state.screenIndex === 0 ? '' : 'none';
    }

    // Typewriter fluido nel terminale AI
    if (this.charIndex < this.targetMessage.length) {
      this.typeTimer += dt;
      while (this.typeTimer > 0.008 && this.charIndex < this.targetMessage.length) {
        this.typeTimer -= 0.008;
        this.charIndex += 2;
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

    // --- Dirty-Checked Top Bar Pills (Agenda Google Calendar & Memoria) ---
    const cal = this.state.system ? this.state.system.calendar : null;
    const nxt = cal && cal.next_event ? cal.next_event : null;
    const todayCount = cal ? cal.today_count || 0 : 0;
    const agendaKey = nxt ? `${nxt.day_label}:${nxt.time_label}:${nxt.title}:${todayCount}` : `none:${todayCount}`;

    if (this.elAgendaPill && this._lastAgendaKey !== agendaKey) {
      this._lastAgendaKey = agendaKey;
      if (nxt && nxt.is_today) {
        const shortTitle = nxt.title.length > 18 ? nxt.title.slice(0, 17) + '…' : nxt.title;
        this.elAgendaPill.textContent = `📅 ${nxt.time_label} ${shortTitle}`;
        this.elAgendaPill.classList.add('has-upcoming');
        this.elAgendaPill.title = `Prossimo impegno oggi: ${nxt.time_label} — ${nxt.title} (${todayCount} oggi)`;
      } else {
        this.elAgendaPill.textContent = `📅 AGENDA: ${todayCount} oggi`;
        this.elAgendaPill.classList.toggle('has-upcoming', todayCount > 0);
        this.elAgendaPill.title = nxt
          ? `Prossimo impegno: ${nxt.day_label} ${nxt.time_label} — ${nxt.title}`
          : 'Clicca per vedere l\'Agenda o usa ⚙️ CAL per collegare Google Calendar';
      }
    }

    const memCount = this.state.system ? this.state.system.memoryCount || 0 : 0;
    if (this.elMemPill && this._lastMemKey !== memCount) {
      this._lastMemKey = memCount;
      this.elMemPill.textContent = `🧠 MEM: ${memCount}`;
      this.elMemPill.title = `Neko ricorda ${memCount} fatti/preferenze su di te (clicca per vederli)`;
    }

    // Promemoria automatico sul desktop quando un evento inizia entro 15 minuti
    if (this.elDeadlineToast && cal) {
      const imminent = Array.isArray(cal.imminent_events) ? cal.imminent_events : [];
      if (imminent.length > 0) {
        const hash = imminent.map((e) => `${e.id}:${e.title}:${e.time_label}`).join('|');
        if (this._lastImminentHash !== hash) {
          this._lastImminentHash = hash;
          if (this._imminentDismissedFor !== hash) {
            let html = '';
            for (const ev of imminent.slice(0, 3)) {
              const mins = ev.minutes_until;
              const whenLabel =
                mins > 0 ? `tra ${mins} min (${ev.time_label})` : `IN CORSO (${ev.time_label})`;
              html += `<div>⏰ <strong>${this.escapeHtml(ev.title)}</strong> — ${this.escapeHtml(whenLabel)}</div>`;
            }
            if (this.elDeadlineContent) {
              this.elDeadlineContent.innerHTML = html;
            }
            this.elDeadlineToast.style.display = 'block';
          }
        }
      } else if (this._lastImminentHash !== null) {
        this.elDeadlineToast.style.display = 'none';
        this._lastImminentHash = null;
        this._imminentDismissedFor = null;
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
