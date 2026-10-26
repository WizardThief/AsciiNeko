/**
 * HudTerminal - Gestisce la Top Bar semplificata, il Control Hub Centrale a 5 Tab
 * ("🐾 Conosciamoci", "📌 Progetti & Server", "🧠 Memorie & To-Do", "🔌 Connessioni", "⚙️ Scorciatoia & Reset"),
 * la modalità Mini-Widget Always-on-Top e la Command & Chat Bar in stile Spotlight.
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
    this.elActiveProjBadge = document.getElementById('active-proj-badge');
    this.elHotkeyHintBadge = document.getElementById('hotkey-hint-badge');
    this.elAiForm = document.getElementById('ai-input-form');
    this.elAiInput = document.getElementById('ai-input-field');

    // Top Bar Pills & Buttons
    this.elAgendaPill = document.getElementById('agenda-pill');
    this.elTodoPill = document.getElementById('todo-pill');
    this.elProjectsPill = document.getElementById('projects-pill');
    this.elBtnConosciamoci = document.getElementById('btn-conosciamoci');
    this.elBtnOpenSettings = document.getElementById('btn-open-settings');
    this.elMemTotalBadge = document.getElementById('mem-total-badge');
    this.elBtnWindowMode = document.getElementById('btn-window-mode');

    // Quick To-Do Popover
    this.elTodoPopover = document.getElementById('hud-todo-popover');
    this.elBtnCloseTodoPopover = document.getElementById('btn-close-todo-popover');
    this.elQuickTodoInput = document.getElementById('quick-todo-input');
    this.elBtnQuickTodoAdd = document.getElementById('btn-quick-todo-add');
    this.elQuickTodoList = document.getElementById('quick-todo-list');
    this.elQuickTodoCount = document.getElementById('quick-todo-count');

    // Control Hub Modal
    this.elModalOverlay = document.getElementById('neko-modal-overlay');
    this.elBtnCloseModal = document.getElementById('btn-close-modal');
    this.activeModalTab = 'tab-memorie';

    // Deadline Toast
    this.elDeadlineToast = document.getElementById('hud-deadline-toast');
    this.elDeadlineContent = document.getElementById('deadline-toast-content');
    this.elBtnDismissDeadline = document.getElementById('btn-dismiss-deadline');

    // TAB 1: Conosciamoci Elements
    this.elOnboardProgress = document.getElementById('onboard-progress-badge');
    this.elBtnRandomQuestion = document.getElementById('btn-random-question');
    this.elOnboardQCat = document.getElementById('onboard-q-cat');
    this.elOnboardQText = document.getElementById('onboard-q-text');
    this.elOnboardOptions = document.getElementById('onboard-options-grid');
    this.elOnboardInput = document.getElementById('onboard-custom-input');
    this.elBtnSaveOnboard = document.getElementById('btn-save-onboard');
    this.elOnboardTraits = document.getElementById('onboard-traits-list');
    this.currentOnboardCategory = '';
    this.currentQuestion = null;

    // TAB 2: Progetti & Archivio Elements
    this.elTabProjActive = document.getElementById('tab-btn-proj-active');
    this.elTabProjArchive = document.getElementById('tab-btn-proj-archive');
    this.elCountProjActive = document.getElementById('count-proj-active');
    this.elCountProjArchive = document.getElementById('count-proj-archive');
    this.elStaleBanner = document.getElementById('stale-alert-banner');
    this.elProjNewName = document.getElementById('proj-new-name');
    this.elProjNewClient = document.getElementById('proj-new-client');
    this.elProjNewNote = document.getElementById('proj-new-note');
    this.elBtnCreateProj = document.getElementById('btn-create-project');
    this.elProjActiveView = document.getElementById('proj-active-view');
    this.elProjArchiveView = document.getElementById('proj-archive-view');
    this.elProjSelectorList = document.getElementById('proj-selector-list');
    this.elProjDetailCard = document.getElementById('proj-detail-card');
    this.elOpenWindowsPicker = document.getElementById('open-windows-picker');
    this.elProjArchiveList = document.getElementById('proj-archive-list');
    this.projectsState = { projects: [], archive: [], active_project_id: '', stale_projects: [] };

    // TAB 3: Memory Inspector & To-Do Elements
    this.elInspCountMem = document.getElementById('insp-count-mem');
    this.elInspListMem = document.getElementById('insp-list-mem');
    this.elInspNewMemInput = document.getElementById('insp-new-mem-input');
    this.elBtnInspAddMem = document.getElementById('btn-insp-add-mem');

    this.elInspCountTodo = document.getElementById('insp-count-todo');
    this.elInspListTodo = document.getElementById('insp-list-todo');
    this.elInspNewTodoInput = document.getElementById('insp-new-todo-input');
    this.elBtnInspAddTodo = document.getElementById('btn-insp-add-todo');

    this.elInspCountProfile = document.getElementById('insp-count-profile');
    this.elInspListProfile = document.getElementById('insp-list-profile');

    // TAB 4: Connessioni Elements
    this.elCalInput = document.getElementById('cal-ical-input');
    this.elBtnSaveIcal = document.getElementById('btn-save-ical');
    this.elBtnOpenGcalWeb = document.getElementById('btn-open-gcal-web');
    this.elConnBadgeCal = document.getElementById('conn-badge-cal');
    this.elConnBadgeOllama = document.getElementById('conn-badge-ollama');
    this.elConnOllamaSelect = document.getElementById('conn-ollama-select');
    this.elBtnPullLlama = document.getElementById('btn-pull-llama');
    this.elConnOllamaHint = document.getElementById('conn-ollama-hint');
    this.elConnBadgeStorage = document.getElementById('conn-badge-storage');
    this.elStorageDirInput = document.getElementById('storage-dir-input');
    this.elBtnPickStorageDir = document.getElementById('btn-pick-storage-dir');
    this.elBtnSaveStorageDir = document.getElementById('btn-save-storage-dir');
    this.elBtnOpenStorageDir = document.getElementById('btn-open-storage-dir');
    this.elConnStorageHint = document.getElementById('conn-storage-hint');
    this.elQuickLinksChips = document.getElementById('quick-links-chips');
    this.elNewLinkLabel = document.getElementById('new-link-label');
    this.elNewLinkUrl = document.getElementById('new-link-url');
    this.elBtnAddQuickLink = document.getElementById('btn-add-quick-link');

    // TAB 5: Scorciatoia & Reset Elements
    this.elBtnResetAllNeko = document.getElementById('btn-reset-all-neko');
    this._resetAllArmed = false;
    this._resetAllTimer = null;

    // History Elements
    this.elBtnToggleHistory = document.getElementById('btn-toggle-history');
    this.elHistoryPanel = document.getElementById('chat-history-panel');
    this.elHistoryList = document.getElementById('chat-history-list');
    this.elBtnClearHistory = document.getElementById('btn-clear-history');

    // Dirty-checking cache for 60 FPS zero-allocation updates
    this._lastAgendaKey = null;
    this._lastTodoKey = null;
    this._lastMemKey = null;
    this._lastProjKey = null;
    this._lastOnboardKey = null;
    this._lastHotkeyKey = null;
    this._lastFocusSeq = 0;
    this._lastImminentHash = null;
    this._imminentDismissedFor = null;
    this.miniWidgetMode = false;

    this.currentMessage = '';
    this.targetMessage = '';
    this.charIndex = 0;
    this.typeTimer = 0;
    this.countdownRemaining = 0;

    // Cronologia comandi digitati (Freccia Su / Giù)
    this.sentCommands = [];
    this.cmdCursor = -1;

    // Se la finestra era rimasta bloccata piccola da un vecchio test Mini, ripristinala subito a 1180x780
    try {
      if (window.outerWidth > 0 && (window.outerWidth < 650 || window.outerHeight < 480)) {
        const sw = window.screen.availWidth || 1920;
        const sh = window.screen.availHeight || 1080;
        const w = Math.min(1180, sw - 80);
        const h = Math.min(780, sh - 90);
        window.resizeTo(w, h);
        window.moveTo(Math.max(20, Math.floor((sw - w) / 2)), Math.max(20, Math.floor((sh - h) / 2)));
      }
    } catch (e) {}

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
    if (data && data.onboarding) {
      this.renderOnboardingState(data.onboarding);
    }
    if (data && data.projects) {
      this.renderProjectsState(data.projects);
    }
  }

  async openModalTab(tabId) {
    if (!this.elModalOverlay) return;
    this.activeModalTab = tabId || 'tab-memorie';
    this.elModalOverlay.style.display = 'flex';

    // Aggiorna bottoni sidebar e pannelli
    document.querySelectorAll('[data-modal-tab]').forEach((btn) => {
      const isMatch = btn.getAttribute('data-modal-tab') === this.activeModalTab;
      btn.classList.toggle('active', isMatch);
    });

    document.querySelectorAll('.modal-tab-pane').forEach((pane) => {
      pane.style.display = pane.id === this.activeModalTab ? 'flex' : 'none';
    });

    // Evidenzia pill corrispondente nella Top Bar
    if (this.elBtnConosciamoci) this.elBtnConosciamoci.classList.toggle('active', this.activeModalTab === 'tab-conosciamoci');
    if (this.elProjectsPill) this.elProjectsPill.classList.toggle('active', this.activeModalTab === 'tab-progetti');
    if (this.elBtnOpenSettings) {
      this.elBtnOpenSettings.classList.toggle(
        'active',
        ['tab-memorie', 'tab-connessioni', 'tab-impostazioni'].includes(this.activeModalTab)
      );
    }

    await this.refreshAllSettingsData();
  }

  closeModal() {
    if (!this.elModalOverlay) return;
    this.elModalOverlay.style.display = 'none';
    if (this.elBtnConosciamoci) this.elBtnConosciamoci.classList.remove('active');
    if (this.elProjectsPill) this.elProjectsPill.classList.remove('active');
    if (this.elBtnOpenSettings) this.elBtnOpenSettings.classList.remove('active');
  }

  async refreshAllSettingsData() {
    if (!this.actions.settingsAction) return;
    const data = await this.actions.settingsAction({ action: 'get_all' });
    if (!data || !data.ok) return;

    if (data.onboarding) {
      this.renderOnboardingState(data.onboarding);
    }
    if (data.projects) {
      this.renderProjectsState(data.projects);
    }
    if (data.connections) {
      this.renderConnectionsState(data.connections);
    }
    this.renderMemoryInspector(data);
    this.renderHotkeySelector(data.hotkey || 'alt+n');
  }

  formatHotkeyLabel(hk) {
    const map = {
      'alt+n': 'Alt + N',
      'alt+q': 'Alt + Q',
      'ctrl+space': 'Ctrl + Spazio',
      'alt+z': 'Alt + Z'
    };
    return map[(hk || 'alt+n').toLowerCase()] || 'Alt + N';
  }

  renderHotkeySelector(activeHk) {
    const norm = (activeHk || 'alt+n').toLowerCase();
    document.querySelectorAll('[data-hk-val]').forEach((btn) => {
      const val = (btn.getAttribute('data-hk-val') || '').toLowerCase();
      btn.classList.toggle('active', val === norm);
    });
    if (this.elHotkeyHintBadge) {
      this.elHotkeyHintBadge.textContent = `⌨️ ${this.formatHotkeyLabel(norm)}`;
    }
  }

  renderMemoryInspector(data) {
    const memories = Array.isArray(data.memories) ? data.memories : [];
    const todos = Array.isArray(data.todos) ? data.todos : [];
    const answersObj =
      data.onboarding && data.onboarding.profile && data.onboarding.profile.answers
        ? data.onboarding.profile.answers
        : {};
    const answerEntries = Object.entries(answersObj);

    // 1. Note & Fatti in `memory.json`
    if (this.elInspCountMem) this.elInspCountMem.textContent = String(memories.length);
    if (this.elInspListMem) {
      if (memories.length === 0) {
        this.elInspListMem.innerHTML =
          '<div class="inspector-row"><span class="text-muted">Nessuna nota salvata in memory.json.</span></div>';
      } else {
        this.elInspListMem.innerHTML = '';
        memories.forEach((m, idx) => {
          const text = typeof m === 'string' ? m : m.text || '';
          const date = m.date ? ` (${m.date})` : '';
          const row = document.createElement('div');
          row.className = 'inspector-row';
          row.innerHTML = `
            <span class="inspector-item-text">🧠 ${this.escapeHtml(text)}<span class="text-muted">${this.escapeHtml(date)}</span></span>
            <button type="button" class="inspector-del-btn" title="Elimina questa memoria">🗑️ Elimina</button>
          `;
          const btn = row.querySelector('.inspector-del-btn');
          if (btn) {
            btn.addEventListener('click', async (e) => {
              e.stopPropagation();
              const res = await this.actions.settingsAction({ action: 'delete_memory', index: idx });
              if (res && res.ok) {
                this.renderMemoryInspector(res);
                this.say('🗑️ Nota rimossa dalla memoria di Neko.');
              }
            });
          }
          this.elInspListMem.appendChild(row);
        });
      }
    }

    // 2. Task & Promemoria in `todo.json`
    if (this.elInspCountTodo) this.elInspCountTodo.textContent = String(todos.length);
    if (this.elInspListTodo) {
      if (todos.length === 0) {
        this.elInspListTodo.innerHTML =
          '<div class="inspector-row"><span class="text-muted">Nessun To-Do attivo al momento.</span></div>';
      } else {
        this.elInspListTodo.innerHTML = '';
        todos.forEach((t, idx) => {
          const text = typeof t === 'string' ? t : t.text || '';
          const row = document.createElement('div');
          row.className = 'inspector-row';
          row.innerHTML = `
            <span class="inspector-item-text">☐ ${this.escapeHtml(text)}</span>
            <button type="button" class="inspector-del-btn" title="Segna come completato / rimuovi">✓ Fatto</button>
          `;
          const btn = row.querySelector('.inspector-del-btn');
          if (btn) {
            btn.addEventListener('click', async (e) => {
              e.stopPropagation();
              const res = await this.actions.settingsAction({ action: 'delete_todo', index: idx });
              if (res && res.ok) {
                this.renderMemoryInspector(res);
                this.say(`✅ Task completato e rimosso: "${text}"`);
              }
            });
          }
          this.elInspListTodo.appendChild(row);
        });
      }
    }

    // 3. Risposte Profilo Base (`profile.json`)
    if (this.elInspCountProfile) this.elInspCountProfile.textContent = String(answerEntries.length);
    if (this.elInspListProfile) {
      if (answerEntries.length === 0) {
        this.elInspListProfile.innerHTML =
          '<div class="inspector-row"><span class="text-muted">Nessuna risposta salvata nel Profilo Base. Vai nel tab "🐾 Conosciamoci" per rispondere!</span></div>';
      } else {
        this.elInspListProfile.innerHTML = '';
        for (const [qid, item] of answerEntries) {
          const cat = item.category || 'Profilo';
          const qText = item.question || qid;
          const ans = item.answer || '';
          const row = document.createElement('div');
          row.className = 'inspector-row';
          row.innerHTML = `
            <span class="inspector-item-text">
              <strong style="color:#c084fc;">[${this.escapeHtml(cat)}]</strong> ${this.escapeHtml(qText)} → <strong>${this.escapeHtml(ans)}</strong>
            </span>
            <button type="button" class="inspector-del-btn" title="Rimuovi questa risposta dal Profilo Base">🗑️ Rimuovi</button>
          `;
          const btn = row.querySelector('.inspector-del-btn');
          if (btn) {
            btn.addEventListener('click', async (e) => {
              e.stopPropagation();
              const res = await this.actions.settingsAction({
                action: 'delete_profile_answer',
                question_id: qid
              });
              if (res && res.ok) {
                this.renderMemoryInspector(res);
                if (res.onboarding) this.renderOnboardingState(res.onboarding);
                this.say('🗑️ Risposta rimossa dal Profilo Base.');
              }
            });
          }
          this.elInspListProfile.appendChild(row);
        }
      }
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

    // Apertura/Chiusura Finestra Centrale Control Hub
    if (this.elBtnConosciamoci) {
      this.elBtnConosciamoci.addEventListener('click', (e) => {
        e.stopPropagation();
        this.openModalTab('tab-conosciamoci');
      });
    }

    if (this.elProjectsPill) {
      this.elProjectsPill.addEventListener('click', (e) => {
        e.stopPropagation();
        this.openModalTab('tab-progetti');
      });
    }

    if (this.elBtnOpenSettings) {
      this.elBtnOpenSettings.addEventListener('click', (e) => {
        e.stopPropagation();
        if (this.elModalOverlay && this.elModalOverlay.style.display !== 'none' &&
            ['tab-memorie', 'tab-connessioni', 'tab-impostazioni'].includes(this.activeModalTab)) {
          this.closeModal();
        } else {
          this.openModalTab('tab-memorie');
        }
      });
    }

    document.querySelectorAll('[data-modal-tab]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const tabId = btn.getAttribute('data-modal-tab');
        if (tabId) this.openModalTab(tabId);
      });
    });

    if (this.elBtnCloseModal) {
      this.elBtnCloseModal.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeModal();
      });
    }

    if (this.elModalOverlay) {
      this.elModalOverlay.addEventListener('click', (e) => {
        if (e.target === this.elModalOverlay) {
          this.closeModal();
        }
      });
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.elModalOverlay && this.elModalOverlay.style.display !== 'none') {
        this.closeModal();
      }
    });

    document.querySelectorAll('[data-close-drawer]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (this.elHistoryPanel) this.elHistoryPanel.style.display = 'none';
        if (this.elBtnToggleHistory) this.elBtnToggleHistory.classList.remove('active');
      });
    });

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

    // Toggle Modalità Compatta / Mini-Widget (`🗕 Compatta` <-> `🗖 Finestra Grande`)
    if (this.elBtnWindowMode) {
      this.elBtnWindowMode.addEventListener('click', async (e) => {
        e.stopPropagation();
        this.miniWidgetMode = !this.miniWidgetMode;
        if (this.miniWidgetMode) this.closeModal();
        document.body.classList.toggle('mini-widget-mode', this.miniWidgetMode);
        this.elBtnWindowMode.textContent = this.miniWidgetMode ? '🗖 Finestra Grande' : '🗕 Compatta';

        try {
          const sw = window.screen.availWidth || 1920;
          const sh = window.screen.availHeight || 1080;
          if (this.miniWidgetMode) {
            const w = 480;
            const h = 420;
            window.resizeTo(w, h);
            window.moveTo(Math.max(20, sw - w - 28), Math.max(20, sh - h - 48));
          } else {
            const w = Math.min(1180, sw - 80);
            const h = Math.min(780, sh - 90);
            window.resizeTo(w, h);
            window.moveTo(Math.max(20, Math.floor((sw - w) / 2)), Math.max(20, Math.floor((sh - h) / 2)));
          }
        } catch (err) {}

        if (this.actions.setWindowMode) {
          await this.actions.setWindowMode(this.miniWidgetMode ? 'mini' : 'dashboard');
        }
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

    // Popover Rapido To-Do nella Top Bar
    if (this.elTodoPill && this.elTodoPopover) {
      this.elTodoPill.addEventListener('click', async (e) => {
        e.stopPropagation();
        const willOpen = this.elTodoPopover.style.display === 'none';
        this.elTodoPopover.style.display = willOpen ? 'flex' : 'none';
        this.elTodoPill.classList.toggle('active', willOpen);
        if (willOpen) {
          const list = (this.state.system && this.state.system.todosList) || [];
          this.renderQuickTodoPopover(list);
          if (this.elQuickTodoInput) this.elQuickTodoInput.focus();
        }
      });
    }

    if (this.elBtnCloseTodoPopover && this.elTodoPopover) {
      this.elBtnCloseTodoPopover.addEventListener('click', (e) => {
        e.stopPropagation();
        this.elTodoPopover.style.display = 'none';
        if (this.elTodoPill) this.elTodoPill.classList.remove('active');
      });
    }

    const addQuickTodo = async () => {
      if (!this.elQuickTodoInput || !this.actions.settingsAction) return;
      const text = this.elQuickTodoInput.value.trim();
      if (!text) return;
      this.elQuickTodoInput.value = '';
      const res = await this.actions.settingsAction({ action: 'add_todo', text });
      if (res && Array.isArray(res.todos)) {
        if (this.state.system) {
          this.state.system.todosList = res.todos;
          this.state.system.todoCount = res.todos.length;
        }
        this.renderQuickTodoPopover(res.todos);
        this.say(`✅ Aggiunto ai To-Do: "${text}"`);
      }
    };

    if (this.elBtnQuickTodoAdd) {
      this.elBtnQuickTodoAdd.addEventListener('click', (e) => {
        e.stopPropagation();
        addQuickTodo();
      });
    }
    if (this.elQuickTodoInput) {
      this.elQuickTodoInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          addQuickTodo();
        }
      });
    }

    // --- 1. EVENTI TAB "🐾 CONOSCIAMOCI" (Tutorial Adattivo AI) ---
    document.querySelectorAll('[data-onboard-cat]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        document.querySelectorAll('[data-onboard-cat]').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        this.currentOnboardCategory = btn.getAttribute('data-onboard-cat') || '';
        if (this.elOnboardQText) {
          this.elOnboardQText.textContent = '🧠 Neko sta formulando una domanda su misura con l\'AI...';
        }
        if (this.actions.conosciamociAction) {
          const res = await this.actions.conosciamociAction({
            action: 'random',
            category: this.currentOnboardCategory,
            generate_ai: true
          });
          if (res && res.onboarding) this.renderOnboardingState(res.onboarding);
        }
      });
    });

    if (this.elBtnRandomQuestion) {
      this.elBtnRandomQuestion.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!this.actions.conosciamociAction) return;
        const excludeId = this.currentQuestion ? this.currentQuestion.id : '';
        this.elBtnRandomQuestion.textContent = '⏳ Generazione AI...';
        if (this.elOnboardQText) {
          this.elOnboardQText.textContent = '🧠 Neko sta elaborando una nuova domanda personalizzata sul tuo profilo...';
        }
        const res = await this.actions.conosciamociAction({
          action: 'random',
          category: this.currentOnboardCategory,
          exclude_id: excludeId,
          generate_ai: true
        });
        this.elBtnRandomQuestion.textContent = '🎲 Nuova domanda su misura (AI)';
        if (res && res.onboarding) this.renderOnboardingState(res.onboarding);
      });
    }

    const saveCurrentOnboardAnswer = async (answerText) => {
      const clean = (answerText || '').trim();
      if (!clean || !this.currentQuestion || !this.actions.conosciamociAction) return;
      if (this.elOnboardQText) {
        this.elOnboardQText.textContent = '🧠 Neko sta analizzando la tua risposta con l\'AI e preparando la prossima domanda...';
      }
      if (this.elOnboardOptions) {
        this.elOnboardOptions.innerHTML = '';
      }
      const res = await this.actions.conosciamociAction({
        action: 'answer',
        question_id: this.currentQuestion.id,
        category: this.currentQuestion.category,
        question_text: this.currentQuestion.question,
        answer: clean
      });
      if (this.elOnboardInput) this.elOnboardInput.value = '';
      if (res && res.onboarding) {
        this.renderOnboardingState(res.onboarding);
        this.say(`🐾 Perfetto! Ho analizzato la tua risposta e aggiornato il tuo Profilo Utente.`);
      }
    };

    if (this.elBtnSaveOnboard) {
      this.elBtnSaveOnboard.addEventListener('click', (e) => {
        e.stopPropagation();
        saveCurrentOnboardAnswer(this.elOnboardInput ? this.elOnboardInput.value : '');
      });
    }

    if (this.elOnboardInput) {
      this.elOnboardInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          saveCurrentOnboardAnswer(this.elOnboardInput.value);
        }
      });
    }

    // --- 2. EVENTI TAB "📌 PROGETTI & ARCHIVIO STORICO" ---
    if (this.elTabProjActive && this.elTabProjArchive) {
      this.elTabProjActive.addEventListener('click', (e) => {
        e.stopPropagation();
        this.elTabProjActive.classList.add('active');
        this.elTabProjArchive.classList.remove('active');
        if (this.elProjActiveView) this.elProjActiveView.style.display = 'flex';
        if (this.elProjArchiveView) this.elProjArchiveView.style.display = 'none';
      });
      this.elTabProjArchive.addEventListener('click', (e) => {
        e.stopPropagation();
        this.elTabProjArchive.classList.add('active');
        this.elTabProjActive.classList.remove('active');
        if (this.elProjArchiveView) this.elProjArchiveView.style.display = 'flex';
        if (this.elProjActiveView) this.elProjActiveView.style.display = 'none';
      });
    }

    if (this.elBtnCreateProj) {
      this.elBtnCreateProj.addEventListener('click', async (e) => {
        e.stopPropagation();
        const name = this.elProjNewName ? this.elProjNewName.value.trim() : '';
        const client = this.elProjNewClient ? this.elProjNewClient.value.trim() : '';
        const note = this.elProjNewNote ? this.elProjNewNote.value.trim() : '';
        if (!name) {
          this.say('⚠️ Inserisci almeno il nome del progetto (es. "Villa Rossi").');
          return;
        }
        if (this.actions.projectsAction) {
          const res = await this.actions.projectsAction({
            action: 'create',
            name,
            client,
            note
          });
          if (res && res.ok) {
            if (this.elProjNewName) this.elProjNewName.value = '';
            if (this.elProjNewClient) this.elProjNewClient.value = '';
            if (this.elProjNewNote) this.elProjNewNote.value = '';
            this.renderProjectsState(res.state);

            if (Array.isArray(res.archived_matches) && res.archived_matches.length > 0) {
              const m = res.archived_matches[0];
              this.say(
                `🐾 Ehi! Ho trovato in Archivio Storico delle note passate per "${m.client}" (${m.notes.join('; ')}). Vuoi riattivarle su "${name}"?`,
                [
                  {
                    label: `🔄 Riattiva preferenze di "${m.client}" su "${name}"`,
                    restore_archive_id: m.archive_id
                  }
                ]
              );
            } else {
              this.say(`📌 Scheda Progetto "${name}" attivata! Ora puoi fissare le cartelle aperte sul Server.`);
            }
          }
        }
      });
    }

    // --- 3. EVENTI TAB "🧠 MEMORIE & TO-DO" (MEMORY INSPECTOR) ---
    const addMemoryFromInspector = async () => {
      if (!this.elInspNewMemInput || !this.actions.settingsAction) return;
      const text = this.elInspNewMemInput.value.trim();
      if (!text) return;
      const res = await this.actions.settingsAction({ action: 'add_memory', text });
      this.elInspNewMemInput.value = '';
      if (res && res.ok) {
        this.renderMemoryInspector(res);
        this.say(`🧠 Nota salvata nella memoria di Neko: "${text}"`);
      }
    };

    if (this.elBtnInspAddMem) {
      this.elBtnInspAddMem.addEventListener('click', (e) => {
        e.stopPropagation();
        addMemoryFromInspector();
      });
    }
    if (this.elInspNewMemInput) {
      this.elInspNewMemInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          addMemoryFromInspector();
        }
      });
    }

    const addTodoFromInspector = async () => {
      if (!this.elInspNewTodoInput || !this.actions.settingsAction) return;
      const text = this.elInspNewTodoInput.value.trim();
      if (!text) return;
      const res = await this.actions.settingsAction({ action: 'add_todo', text });
      this.elInspNewTodoInput.value = '';
      if (res && res.ok) {
        this.renderMemoryInspector(res);
        this.say(`📌 Aggiunto ai To-Do: "${text}"`);
      }
    };

    if (this.elBtnInspAddTodo) {
      this.elBtnInspAddTodo.addEventListener('click', (e) => {
        e.stopPropagation();
        addTodoFromInspector();
      });
    }
    if (this.elInspNewTodoInput) {
      this.elInspNewTodoInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          addTodoFromInspector();
        }
      });
    }

    // --- 4. EVENTI TAB "🔌 CONNESSIONI" ---
    if (this.elBtnSaveIcal && this.elCalInput) {
      this.elBtnSaveIcal.addEventListener('click', async (e) => {
        e.stopPropagation();
        const url = this.elCalInput.value.trim();
        this.say('⏳ Test e collegamento a Google Calendar in corso...');
        if (this.actions.saveCalendarConfig) {
          const res = await this.actions.saveCalendarConfig(url);
          if (res && res.ok) {
            const count = res.calendar ? res.calendar.upcoming_count : 0;
            this.say(
              url
                ? `📅 Google Calendar collegato! Ho caricato ${count} appuntamenti nei prossimi 7 giorni.`
                : '📅 Link Google Calendar rimosso (restano attivi gli impegni locali).'
            );
            if (this.actions.connectionsAction) {
              const st = await this.actions.connectionsAction({ action: 'status' });
              if (st && st.connections) this.renderConnectionsState(st.connections);
            }
          }
        }
      });
    }

    if (this.elBtnOpenGcalWeb) {
      this.elBtnOpenGcalWeb.addEventListener('click', (e) => {
        e.stopPropagation();
        if (this.actions.triggerPcAction) {
          this.actions.triggerPcAction('https://calendar.google.com');
        }
      });
    }

    if (this.elConnOllamaSelect) {
      this.elConnOllamaSelect.addEventListener('change', async (e) => {
        e.stopPropagation();
        const model = this.elConnOllamaSelect.value;
        if (model && this.actions.connectionsAction) {
          const res = await this.actions.connectionsAction({ action: 'set_model', model });
          if (res && res.connections) {
            this.renderConnectionsState(res.connections);
            this.say(`🧠 Modello Ollama attivo impostato su "${model}".`);
          }
        }
      });
    }

    if (this.elBtnPullLlama) {
      this.elBtnPullLlama.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!this.actions.connectionsAction) return;
        this.say('⬇️ Avvio download automatico del modello locale `qwen2.5:7b` in background...');
        const res = await this.actions.connectionsAction({ action: 'pull_model', model: 'qwen2.5:7b' });
        if (res && res.connections) this.renderConnectionsState(res.connections);
      });
    }

    if (this.elBtnPickStorageDir) {
      this.elBtnPickStorageDir.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!this.actions.connectionsAction) return;
        this.elBtnPickStorageDir.textContent = '⏳ Seleziona...';
        const res = await this.actions.connectionsAction({ action: 'pick_storage_dir' });
        this.elBtnPickStorageDir.textContent = '📂 Sfoglia...';
        if (res && !res.cancelled) {
          if (res.message) this.say(res.message);
          await this.refreshAllSettingsData();
        }
      });
    }

    if (this.elBtnSaveStorageDir && this.elStorageDirInput) {
      this.elBtnSaveStorageDir.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!this.actions.connectionsAction) return;
        const dir = this.elStorageDirInput.value.trim();
        const res = await this.actions.connectionsAction({ action: 'set_storage_dir', data_dir: dir });
        if (res && res.message) {
          this.say(res.message);
        }
        await this.refreshAllSettingsData();
      });
    }

    if (this.elBtnOpenStorageDir) {
      this.elBtnOpenStorageDir.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!this.actions.connectionsAction) return;
        const res = await this.actions.connectionsAction({ action: 'open_storage_dir' });
        if (res && res.message) this.say(res.message);
      });
    }

    if (this.elBtnAddQuickLink) {
      this.elBtnAddQuickLink.addEventListener('click', async (e) => {
        e.stopPropagation();
        const label = this.elNewLinkLabel ? this.elNewLinkLabel.value.trim() : '';
        const url = this.elNewLinkUrl ? this.elNewLinkUrl.value.trim() : '';
        if (!label || !url || !this.actions.connectionsAction) return;
        const res = await this.actions.connectionsAction({ action: 'add_link', label, url });
        if (this.elNewLinkLabel) this.elNewLinkLabel.value = '';
        if (this.elNewLinkUrl) this.elNewLinkUrl.value = '';
        if (res && Array.isArray(res.quick_links)) {
          this.renderQuickLinks(res.quick_links);
        }
      });
    }

    // --- 5. EVENTI TAB "⚙️ SCORCIATOIA & RESET" ---
    document.querySelectorAll('[data-hk-val]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const hk = btn.getAttribute('data-hk-val');
        if (!hk || !this.actions.settingsAction) return;
        const res = await this.actions.settingsAction({ action: 'set_hotkey', hotkey: hk });
        if (res && res.ok) {
          this.renderHotkeySelector(res.hotkey || hk);
          this.say(`⌨️ Scorciatoia globale aggiornata in tempo reale su "${this.formatHotkeyLabel(res.hotkey || hk)}"!`);
        }
      });
    });

    document.querySelectorAll('[data-reset-target]').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const target = btn.getAttribute('data-reset-target');
        if (!target || !this.actions.settingsAction) return;
        const res = await this.actions.settingsAction({ action: 'reset', target });
        if (res && res.ok) {
          await this.refreshAllSettingsData();
          if (target === 'chat') this.renderHistoryList([]);
          this.say(res.message || '🧹 Reset selettivo completato.');
        }
      });
    });

    if (this.elBtnResetAllNeko) {
      this.elBtnResetAllNeko.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!this._resetAllArmed) {
          this._resetAllArmed = true;
          this.elBtnResetAllNeko.classList.add('confirm-armed');
          this.elBtnResetAllNeko.textContent = '⚠️ SEI SICURO? CLICCA DI NUOVO ENTRO 5 SECONDI PER CONFERMARE IL RESET TOTALE';
          if (this._resetAllTimer) clearTimeout(this._resetAllTimer);
          this._resetAllTimer = setTimeout(() => {
            this._resetAllArmed = false;
            if (this.elBtnResetAllNeko) {
              this.elBtnResetAllNeko.classList.remove('confirm-armed');
              this.elBtnResetAllNeko.textContent =
                '⚠️ RESET TOTALE DI NEKO (Torna come appena installato — mantiene solo Connessioni)';
            }
          }, 5000);
          return;
        }

        // Secondo click: esegue il reset totale!
        this._resetAllArmed = false;
        if (this._resetAllTimer) clearTimeout(this._resetAllTimer);
        this.elBtnResetAllNeko.classList.remove('confirm-armed');
        this.elBtnResetAllNeko.textContent =
          '⚠️ RESET TOTALE DI NEKO (Torna come appena installato — mantiene solo Connessioni)';

        if (this.actions.settingsAction) {
          const res = await this.actions.settingsAction({ action: 'reset', target: 'all' });
          if (res && res.ok) {
            await this.refreshAllSettingsData();
            this.renderHistoryList([]);
            this.say('🐾 Reset Totale completato! Non ricordo più nulla del passato: clicca su "🐾 Conosciamoci" per ricominciare!');
          }
        }
      });
    }

    // Toggle Cronologia Chat (📜 STORICO CHAT)
    if (this.elBtnToggleHistory && this.elHistoryPanel) {
      this.elBtnToggleHistory.addEventListener('click', async (e) => {
        e.stopPropagation();
        const willOpen = this.elHistoryPanel.style.display === 'none';
        this.elHistoryPanel.style.display = willOpen ? 'flex' : 'none';
        this.elBtnToggleHistory.classList.toggle('active', willOpen);
        if (willOpen && this.actions.fetchChatHistory) {
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
        this.say('🧹 Cronologia chat pulita! (Profilo, Schede Progetto e Memoria restano salvi).');
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

  // --- RENDERING POPOVER RAPIDO TO-DO ---
  renderQuickTodoPopover(todos) {
    const list = Array.isArray(todos) ? todos : [];
    if (this.elQuickTodoCount) {
      this.elQuickTodoCount.textContent = String(list.length);
    }
    if (!this.elQuickTodoList) return;
    if (list.length === 0) {
      this.elQuickTodoList.innerHTML =
        '<div style="color:var(--text-muted); font-size:12.5px; padding:6px 2px;">🎉 Tutte le task sono completate! Scrivi qui sopra per aggiungerne una.</div>';
      return;
    }
    this.elQuickTodoList.innerHTML = '';
    list.forEach((txt, idx) => {
      const row = document.createElement('div');
      row.className = 'todo-popover-item';
      row.innerHTML = `
        <span style="flex:1; word-break:break-word;">${this.escapeHtml(txt)}</span>
        <button type="button" class="todo-popover-check" title="Segna come completato">✓ Fatto</button>
      `;
      const btn = row.querySelector('.todo-popover-check');
      if (btn) {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          if (!this.actions.settingsAction) return;
          const res = await this.actions.settingsAction({ action: 'delete_todo', index: idx });
          if (res && Array.isArray(res.todos)) {
            if (this.state.system) {
              this.state.system.todosList = res.todos;
              this.state.system.todoCount = res.todos.length;
            }
            this.renderQuickTodoPopover(res.todos);
            this.say(`✅ Task completata: "${txt}"!`);
          }
        });
      }
      this.elQuickTodoList.appendChild(row);
    });
  }

  // --- RENDERING TAB 1: "🐾 CONOSCIAMOCI" (Tutorial Adattivo AI) ---
  renderOnboardingState(onb) {
    if (!onb) return;
    const traits = onb.profile && Array.isArray(onb.profile.traits) ? onb.profile.traits : [];
    if (this.elOnboardProgress) {
      this.elOnboardProgress.textContent = `${traits.length} tratti appresi`;
    }
    const q = onb.next_question;
    this.currentQuestion = q;
    if (q) {
      const aiBadge = q.ai_generated ? ' ✨ (Domanda AI su misura)' : '';
      if (this.elOnboardQCat) this.elOnboardQCat.textContent = `🐾 ${q.category || 'Profilo'}${aiBadge}`;
      if (this.elOnboardQText) this.elOnboardQText.textContent = q.question || '';
      if (this.elOnboardOptions) {
        this.elOnboardOptions.innerHTML = '';
        for (const opt of q.options || []) {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'onboard-opt-chip';
          btn.textContent = opt;
          btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            if (!this.actions.conosciamociAction) return;
            if (this.elOnboardQText) {
              this.elOnboardQText.textContent =
                '🧠 Neko sta analizzando la tua risposta con l\'AI e preparando la prossima domanda su misura...';
            }
            this.elOnboardOptions.innerHTML = '';
            const res = await this.actions.conosciamociAction({
              action: 'answer',
              question_id: q.id,
              category: q.category,
              question_text: q.question,
              answer: opt
            });
            if (res && res.onboarding) {
              this.renderOnboardingState(res.onboarding);
              this.say(`🐾 Perfetto! Ho aggiornato il tuo profilo e generato la prossima domanda.`);
            }
          });
          this.elOnboardOptions.appendChild(btn);
        }
      }
    }

    if (this.elOnboardTraits) {
      if (traits.length === 0) {
        this.elOnboardTraits.innerHTML =
          '<span class="text-muted">Rispondi alla domanda qui sopra: l\'AI sintetizzerà qui chi sei e cosa fai!</span>';
      } else {
        this.elOnboardTraits.innerHTML = traits
          .map((t) => `<span class="trait-pill">✓ ${this.escapeHtml(t)}</span>`)
          .join('');
      }
    }
  }

  // --- RENDERING TAB 2: "📌 PROGETTI & ARCHIVIO STORICO" ---
  renderProjectsState(pState) {
    if (!pState) return;
    this.projectsState = pState;
    const projects = Array.isArray(pState.projects) ? pState.projects : [];
    const archive = Array.isArray(pState.archive) ? pState.archive : [];
    const stale = Array.isArray(pState.stale_projects) ? pState.stale_projects : [];
    const activeId = pState.active_project_id || (projects[0] ? projects[0].id : '');

    if (this.elCountProjActive) this.elCountProjActive.textContent = String(projects.length);
    if (this.elCountProjArchive) this.elCountProjArchive.textContent = String(archive.length);

    // 1. Banner Check-up Progetti Inattivi (>14 giorni)
    if (this.elStaleBanner) {
      if (stale.length > 0) {
        const sp = stale[0];
        this.elStaleBanner.style.display = 'flex';
        this.elStaleBanner.innerHTML = `
          <span>🧹 <strong>Check-up Memoria:</strong> il progetto <strong>"${this.escapeHtml(sp.name)}"</strong> non viene toccato da ${sp.days_inactive} giorni. È concluso?</span>
          <button type="button" class="term-btn term-btn-sm btn-accent" id="btn-stale-archive-now">📦 SPOSTA IN ARCHIVIO STORICO</button>
        `;
        const btnStale = document.getElementById('btn-stale-archive-now');
        if (btnStale) {
          btnStale.addEventListener('click', async (e) => {
            e.stopPropagation();
            const res = await this.actions.projectsAction({ action: 'archive', project_id: sp.id });
            if (res && res.state) {
              this.renderProjectsState(res.state);
              this.say(`📦 Progetto "${sp.name}" spostato nell'Archivio Storico! Le preferenze del cliente restano salve per il futuro.`);
            }
          });
        }
      } else {
        this.elStaleBanner.style.display = 'none';
      }
    }

    // 2. Pills selettore Progetti Attivi
    if (this.elProjSelectorList) {
      this.elProjSelectorList.innerHTML = '';
      for (const p of projects) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'proj-tab-pill' + (p.id === activeId ? ' active' : '');
        btn.textContent = p.client ? `📌 ${p.name} (${p.client})` : `📌 ${p.name}`;
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const res = await this.actions.projectsAction({ action: 'set_active', project_id: p.id });
          if (res && res.state) this.renderProjectsState(res.state);
        });
        this.elProjSelectorList.appendChild(btn);
      }
    }

    // 3. Scheda Dettaglio Progetto Attivo
    const activeProj = projects.find((p) => p.id === activeId) || projects[0];
    if (this.elProjDetailCard) {
      if (!activeProj) {
        this.elProjDetailCard.innerHTML =
          '<span class="text-muted">Nessun progetto attivo in corso. Creane uno qui sopra (es. "Villa Rossi" - Cliente "Sig. Rossi")!</span>';
      } else {
        const pins = Array.isArray(activeProj.pins) ? activeProj.pins : [];
        const notes = Array.isArray(activeProj.notes) ? activeProj.notes : [];

        let pinsHtml = '';
        if (pins.length === 0) {
          pinsHtml = '<span class="text-muted">Nessuna cartella Server fissata. Clicca su "📌 Fissa cartelle aperte" o incolla il percorso qui sotto!</span>';
        } else {
          for (const pin of pins) {
            pinsHtml += `
              <span class="pin-chip" data-open-pin="${this.escapeHtml(pin.path)}" title="Apri istantaneamente: ${this.escapeHtml(pin.path)}">
                ${this.escapeHtml(pin.label)}
                <button type="button" class="pin-del-x" data-del-pin="${this.escapeHtml(pin.id)}" title="Rimuovi Pin">×</button>
              </span>
            `;
          }
        }

        let notesHtml = '';
        if (notes.length === 0) {
          notesHtml = '<span class="text-muted">Nessuna regola/preferenza salvata per questo progetto.</span>';
        } else {
          notes.forEach((n, idx) => {
            notesHtml += `
              <span class="note-item-pill">
                🧠 ${this.escapeHtml(n)}
                <button type="button" class="note-del-x" data-del-note="${idx}" title="Rimuovi nota">×</button>
              </span>
            `;
          });
        }

        this.elProjDetailCard.innerHTML = `
          <div class="proj-detail-top">
            <div>
              <span class="proj-detail-name">📌 ${this.escapeHtml(activeProj.name)}</span>
              ${activeProj.client ? `<span class="drawer-count-badge" style="margin-left:6px;">🤝 Cliente: ${this.escapeHtml(activeProj.client)}</span>` : ''}
            </div>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">
              <button type="button" class="term-btn term-btn-sm btn-accent" id="btn-capture-open-win" title="Legge al volo le cartelle aperte in Esplora File (anche su Server NAS!) senza fare scansioni">
                📌 FISSA CARTELLE/FINESTRE APERTE
              </button>
              <button type="button" class="term-btn term-btn-sm" id="btn-archive-current-proj" title="Progetto terminato? Sposta in Archivio Storico così le note non appesantiscono i task quotidiani">
                📦 ARCHIVIA FINE PROGETTO
              </button>
            </div>
          </div>

          <div class="proj-section-label">📂 Pin Rapidi Server &amp; Cartelle Lavoro (Zero Scansione):</div>
          <div class="proj-pins-list">${pinsHtml}</div>
          <div class="modal-form-row">
            <input type="text" id="proj-pin-label-in" class="modal-input modal-input-sm" placeholder="Etichetta (es. 03_3D)..." />
            <input type="text" id="proj-pin-path-in" class="modal-input modal-input-wide" placeholder="Incolla percorso Server o locale (es. \\\\Server\\Progetti\\Villa_Rossi)..." />
            <button type="button" class="term-btn term-btn-sm" id="btn-add-proj-pin">+ FISSA PIN</button>
          </div>

          <div class="proj-section-label" style="margin-top:4px;">🧠 Regole &amp; Gusti del Cliente (Memoria Calda attiva per Ollama):</div>
          <div class="proj-notes-list">${notesHtml}</div>
          <div class="modal-form-row">
            <input type="text" id="proj-note-in" class="modal-input modal-input-wide" placeholder="Aggiungi nota progetto/cliente (es. Il Sig. Rossi vuole sempre tutto minimale)..." />
            <button type="button" class="term-btn term-btn-sm" id="btn-add-proj-note">+ REGOLA</button>
          </div>
        `;

        // Bind apertura e cancellazione Pin
        this.elProjDetailCard.querySelectorAll('[data-open-pin]').forEach((chip) => {
          chip.addEventListener('click', async (e) => {
            if (e.target.closest('[data-del-pin]')) return;
            e.stopPropagation();
            const path = chip.getAttribute('data-open-pin');
            if (this.actions.triggerPcAction) {
              const res = await this.actions.triggerPcAction(path);
              if (res && res.message) this.say(res.message);
            }
          });
        });

        this.elProjDetailCard.querySelectorAll('[data-del-pin]').forEach((delBtn) => {
          delBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const pinId = delBtn.getAttribute('data-del-pin');
            const res = await this.actions.projectsAction({
              action: 'delete_pin',
              project_id: activeProj.id,
              pin_id: pinId
            });
            if (res && res.state) this.renderProjectsState(res.state);
          });
        });

        this.elProjDetailCard.querySelectorAll('[data-del-note]').forEach((delBtn) => {
          delBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const idx = parseInt(delBtn.getAttribute('data-del-note'), 10);
            const res = await this.actions.projectsAction({
              action: 'delete_note',
              project_id: activeProj.id,
              note_index: idx
            });
            if (res && res.state) this.renderProjectsState(res.state);
          });
        });

        const btnAddPin = document.getElementById('btn-add-proj-pin');
        if (btnAddPin) {
          btnAddPin.addEventListener('click', async (e) => {
            e.stopPropagation();
            const lbl = (document.getElementById('proj-pin-label-in') || {}).value || '';
            const pth = (document.getElementById('proj-pin-path-in') || {}).value || '';
            if (!pth.trim()) return;
            const res = await this.actions.projectsAction({
              action: 'add_pin',
              project_id: activeProj.id,
              label: lbl,
              path: pth
            });
            if (res && res.state) {
              this.renderProjectsState(res.state);
              this.say(`📌 Collegamento rapido fissato su "${activeProj.name}"!`);
            }
          });
        }

        const btnAddNote = document.getElementById('btn-add-proj-note');
        if (btnAddNote) {
          btnAddNote.addEventListener('click', async (e) => {
            e.stopPropagation();
            const nIn = document.getElementById('proj-note-in');
            const noteVal = nIn ? nIn.value.trim() : '';
            if (!noteVal) return;
            const res = await this.actions.projectsAction({
              action: 'add_note',
              project_id: activeProj.id,
              note: noteVal
            });
            if (res && res.state) {
              this.renderProjectsState(res.state);
              this.say(`🧠 Regola aggiunta a "${activeProj.name}": "${noteVal}"`);
            }
          });
        }

        const btnArchiveProj = document.getElementById('btn-archive-current-proj');
        if (btnArchiveProj) {
          btnArchiveProj.addEventListener('click', async (e) => {
            e.stopPropagation();
            const res = await this.actions.projectsAction({
              action: 'archive',
              project_id: activeProj.id
            });
            if (res && res.state) {
              this.renderProjectsState(res.state);
              this.say(
                `📦 Progetto "${activeProj.name}" archiviato! Le note sul cliente non intaseranno i task giornalieri, ma Neko le ricorderà se il cliente torna.`
              );
            }
          });
        }

        const btnCaptureOpen = document.getElementById('btn-capture-open-win');
        if (btnCaptureOpen && this.elOpenWindowsPicker) {
          btnCaptureOpen.addEventListener('click', async (e) => {
            e.stopPropagation();
            btnCaptureOpen.textContent = '⏳ LETTURA FINESTRE...';
            const res = await this.actions.projectsAction({ action: 'capture_open' });
            btnCaptureOpen.textContent = '📌 FISSA CARTELLE/FINESTRE APERTE';
            const list = res && Array.isArray(res.open_windows) ? res.open_windows : [];
            this.elOpenWindowsPicker.style.display = 'flex';
            if (list.length === 0) {
              this.elOpenWindowsPicker.innerHTML =
                '<span class="text-muted">Nessuna finestra di Esplora File o software 3D rilevata aperta in questo istante. Apri una cartella in Esplora File e riprova!</span>';
            } else {
              let h =
                '<div class="proj-section-label">📌 Clicca per fissare subito nella scheda di ' +
                this.escapeHtml(activeProj.name) +
                ':</div><div class="proj-pins-list">';
              for (const item of list) {
                h += `<button type="button" class="onboard-opt-chip" data-pick-open-path="${this.escapeHtml(item.path)}" data-pick-open-label="${this.escapeHtml(item.label)}">+ ${this.escapeHtml(item.label)} (${this.escapeHtml(item.path.slice(-42))})</button>`;
              }
              h += '</div>';
              this.elOpenWindowsPicker.innerHTML = h;
              this.elOpenWindowsPicker.querySelectorAll('[data-pick-open-path]').forEach((b) => {
                b.addEventListener('click', async (ev) => {
                  ev.stopPropagation();
                  const pth = b.getAttribute('data-pick-open-path');
                  const lbl = b.getAttribute('data-pick-open-label');
                  const r2 = await this.actions.projectsAction({
                    action: 'add_pin',
                    project_id: activeProj.id,
                    label: lbl,
                    path: pth
                  });
                  this.elOpenWindowsPicker.style.display = 'none';
                  if (r2 && r2.state) {
                    this.renderProjectsState(r2.state);
                    this.say(`📌 Fissato "${lbl}" nella scheda di "${activeProj.name}"!`);
                  }
                });
              });
            }
          });
        }
      }
    }

    // 4. Lista Archivio Storico (Memoria Fredda)
    if (this.elProjArchiveList) {
      if (archive.length === 0) {
        this.elProjArchiveList.innerHTML =
          '<span class="text-muted">Nessun progetto in Archivio Storico. Quando termini un progetto clicca su "📦 Archivia Fine Progetto".</span>';
      } else {
        let archHtml = '';
        for (const a of archive) {
          const notesStr = Array.isArray(a.notes) && a.notes.length ? a.notes.join(' · ') : 'Nessuna nota cliente';
          archHtml += `
            <div class="archive-item-card">
              <div>
                <strong>📦 ${this.escapeHtml(a.name)}</strong>
                ${a.client ? `<span class="drawer-count-badge" style="margin-left:5px;">Cliente: ${this.escapeHtml(a.client)}</span>` : ''}
                <div class="text-muted" style="font-size:10.5px; margin-top:3px;">🧠 Storico: ${this.escapeHtml(notesStr)}</div>
              </div>
              <div style="display:flex; gap:5px; flex-wrap:wrap;">
                <button type="button" class="term-btn term-btn-sm btn-accent" data-restore-notes="${this.escapeHtml(a.id)}" title="Copia le preferenze di questo cliente nel progetto attivo di oggi">
                  📋 USA REGOLE CLIENTE OGGI
                </button>
                <button type="button" class="term-btn term-btn-sm" data-reactivate-proj="${this.escapeHtml(a.id)}" title="Riporta questo progetto tra quelli Attivi">
                  🔄 RIATTIVA
                </button>
                <button type="button" class="pin-del-x" data-delete-arch="${this.escapeHtml(a.id)}" title="Elimina dall'archivio">×</button>
              </div>
            </div>
          `;
        }
        this.elProjArchiveList.innerHTML = archHtml;

        this.elProjArchiveList.querySelectorAll('[data-restore-notes]').forEach((btn) => {
          btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const aid = btn.getAttribute('data-restore-notes');
            const res = await this.actions.projectsAction({
              action: 'restore',
              archive_id: aid,
              mode: 'copy_client_notes'
            });
            if (res && res.state) {
              this.renderProjectsState(res.state);
              this.say(`🔄 Preferenze storiche del cliente riattivate sul progetto "${res.restored_to || 'attivo'}"!`);
            }
          });
        });

        this.elProjArchiveList.querySelectorAll('[data-reactivate-proj]').forEach((btn) => {
          btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const aid = btn.getAttribute('data-reactivate-proj');
            const res = await this.actions.projectsAction({
              action: 'restore',
              archive_id: aid,
              mode: 'reactivate_project'
            });
            if (res && res.state) {
              this.renderProjectsState(res.state);
              this.say('🔄 Progetto riportato tra i Progetti Attivi!');
            }
          });
        });

        this.elProjArchiveList.querySelectorAll('[data-delete-arch]').forEach((btn) => {
          btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const aid = btn.getAttribute('data-delete-arch');
            const res = await this.actions.projectsAction({
              action: 'delete_archive',
              archive_id: aid
            });
            if (res && res.state) this.renderProjectsState(res.state);
          });
        });
      }
    }
  }

  // --- RENDERING TAB 4: "🔌 CONNESSIONI" ---
  renderConnectionsState(conn) {
    if (!conn) return;
    const cal = conn.calendar || {};
    if (this.elConnBadgeCal) {
      this.elConnBadgeCal.textContent = cal.configured
        ? `🟢 Connesso (${cal.upcoming_count || 0} eventi)`
        : '🔴 Da collegare';
    }
    if (this.elCalInput && Array.isArray(cal.ical_urls) && cal.ical_urls[0] && !this.elCalInput.value) {
      this.elCalInput.value = cal.ical_urls[0];
    }

    const ol = conn.ollama || {};
    const models = Array.isArray(ol.models) ? ol.models : [];
    if (this.elConnBadgeOllama) {
      if (ol.pull_status && ol.pull_status.active) {
        this.elConnBadgeOllama.textContent = '⬇️ Download in corso...';
      } else if (ol.online && models.length > 0) {
        this.elConnBadgeOllama.textContent = `🟢 Attivo (${ol.active_model || models[0]})`;
      } else if (ol.online) {
        this.elConnBadgeOllama.textContent = '🟡 Online (Scarica modello)';
      } else {
        this.elConnBadgeOllama.textContent = '🔴 Spento / Non installato';
      }
    }

    if (this.elConnOllamaSelect) {
      if (models.length === 0) {
        this.elConnOllamaSelect.innerHTML = '<option value="">Nessun modello presente (Clicca Scarica)</option>';
      } else {
        this.elConnOllamaSelect.innerHTML = models
          .map(
            (m) =>
              `<option value="${this.escapeHtml(m)}" ${m === ol.active_model ? 'selected' : ''}>🧠 ${this.escapeHtml(m)}</option>`
          )
          .join('');
      }
    }

    if (this.elConnOllamaHint && ol.pull_status && ol.pull_status.message) {
      this.elConnOllamaHint.textContent = ol.pull_status.message;
    }

    const stg = conn.storage || {};
    if (this.elConnBadgeStorage) {
      this.elConnBadgeStorage.textContent = stg.is_custom
        ? `☁️ Cartella Personalizzata (${stg.files_count || 0} file)`
        : `📂 Locale (${stg.files_count || 0} file)`;
    }
    if (this.elStorageDirInput && stg.data_dir) {
      this.elStorageDirInput.value = stg.data_dir;
    }

    if (Array.isArray(conn.quick_links)) {
      this.renderQuickLinks(conn.quick_links);
    }
  }

  renderQuickLinks(links) {
    if (!this.elQuickLinksChips) return;
    this.elQuickLinksChips.innerHTML = '';
    links.forEach((lnk, idx) => {
      const chip = document.createElement('span');
      chip.className = 'pin-chip';
      chip.innerHTML = `
        <span>${this.escapeHtml(lnk.label)}</span>
        <button type="button" class="pin-del-x" title="Rimuovi link">×</button>
      `;
      chip.addEventListener('click', (e) => {
        if (e.target.closest('.pin-del-x')) return;
        e.stopPropagation();
        if (this.actions.triggerPcAction) {
          this.actions.triggerPcAction(lnk.url);
        }
      });
      const delBtn = chip.querySelector('.pin-del-x');
      if (delBtn) {
        delBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          if (this.actions.connectionsAction) {
            const res = await this.actions.connectionsAction({ action: 'delete_link', index: idx });
            if (res && Array.isArray(res.quick_links)) this.renderQuickLinks(res.quick_links);
          }
        });
      }
      this.elQuickLinksChips.appendChild(chip);
    });
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
      btn.textContent = act.label || 'Azione';
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (act.url) {
          if (this.actions.triggerPcAction) {
            this.actions.triggerPcAction(act.url);
          } else {
            window.open(act.url, '_blank');
          }
        } else if (act.open_path && this.actions.triggerPcAction) {
          const res = await this.actions.triggerPcAction(act.open_path);
          if (res && res.message) this.say(res.message);
        } else if (act.restore_archive_id && this.actions.projectsAction) {
          const res = await this.actions.projectsAction({
            action: 'restore',
            archive_id: act.restore_archive_id,
            mode: 'copy_client_notes'
          });
          if (res && res.ok) {
            this.renderProjectsState(res.state);
            this.say(`🔄 Preferenze storiche del cliente riattivate con successo!`);
          }
        } else if (act.save_project_note && this.actions.saveQuickMemory) {
          const res = await this.actions.saveQuickMemory(
            act.save_project_note.note,
            act.save_project_note.project_id
          );
          if (res && res.ok) {
            if (res.projects) this.renderProjectsState(res.projects);
            this.say(`🧠 Regola salvata nella Scheda Progetto: "${act.save_project_note.note}"`);
          }
        } else if (act.save_memory && this.actions.saveQuickMemory) {
          const res = await this.actions.saveQuickMemory(act.save_memory, '');
          if (res && res.ok) {
            this.say(`🧠 Salvato nella Memoria di Neko: "${act.save_memory}"`);
          }
        } else if (act.complete_todo_idx !== undefined && this.actions.settingsAction) {
          const res = await this.actions.settingsAction({
            action: 'delete_todo',
            index: act.complete_todo_idx
          });
          if (res && Array.isArray(res.todos)) {
            if (this.state.system) {
              this.state.system.todosList = res.todos;
              this.state.system.todoCount = res.todos.length;
            }
            this.renderQuickTodoPopover(res.todos);
            this.say(`✅ Task completata! Restano ${res.todos.length} To-Do attivi.`);
          }
        } else if (act.pull_ollama_model && this.actions.connectionsAction) {
          this.openModalTab('tab-connessioni');
          const res = await this.actions.connectionsAction({
            action: 'pull_model',
            model: act.pull_ollama_model
          });
          if (res && res.connections) this.renderConnectionsState(res.connections);
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
    // Risposta istantanea alla scorciatoia globale (es. Alt + N)
    const focusSeq = this.state.system ? this.state.system.focusInputSeq || 0 : 0;
    if (focusSeq > this._lastFocusSeq) {
      this._lastFocusSeq = focusSeq;
      if (this.elAiDock) this.elAiDock.classList.remove('term-collapsed');
      if (this.elAiInput) {
        this.elAiInput.focus();
        this.elAiInput.select();
      }
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
      const pull = this.state.system ? this.state.system.ollamaPull : null;
      if (pull && pull.active) {
        this.elAiModelBadge.textContent = `⬇️ ${pull.model || 'OLLAMA'}`;
      } else {
        this.elAiModelBadge.textContent = this.state.system.ollamaActive || 'OLLAMA LOCAL';
      }
    }

    const hk = this.state.system ? this.state.system.hotkey || 'alt+n' : 'alt+n';
    if (this.elHotkeyHintBadge && this._lastHotkeyKey !== hk) {
      this._lastHotkeyKey = hk;
      this.elHotkeyHintBadge.textContent = `⌨️ ${this.formatHotkeyLabel(hk)}`;
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

    // --- Dirty-Checked Top Bar Pills (Agenda, Progetti, Conosciamoci, Impostazioni & Memorie) ---
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
          : "Clicca per vedere l'Agenda o apri ⚙️ Impostazioni & Memorie -> Connessioni per collegare Google Calendar";
      }
    }

    const todoCount = this.state.system ? this.state.system.todoCount || 0 : 0;
    const todosList = this.state.system && Array.isArray(this.state.system.todosList) ? this.state.system.todosList : [];
    const todoKey = `${todoCount}:${todosList.join('|')}`;
    if (this.elTodoPill && this._lastTodoKey !== todoKey) {
      this._lastTodoKey = todoKey;
      this.elTodoPill.textContent = `✅ TO-DO: ${todoCount}`;
      this.elTodoPill.classList.toggle('has-todos', todoCount > 0);
      if (this.elTodoPopover && this.elTodoPopover.style.display !== 'none') {
        this.renderQuickTodoPopover(todosList);
      }
    }

    const projCount = this.state.system ? this.state.system.projectsCount || 0 : 0;
    const archCount = this.state.system ? this.state.system.archiveCount || 0 : 0;
    const staleCount = this.state.system ? this.state.system.staleProjectsCount || 0 : 0;
    const activeProjName = this.state.system ? this.state.system.activeProjectName || '' : '';
    const projKey = `${projCount}:${archCount}:${staleCount}:${activeProjName}`;

    if (this.elProjectsPill && this._lastProjKey !== projKey) {
      this._lastProjKey = projKey;
      if (activeProjName) {
        const shortP = activeProjName.length > 14 ? activeProjName.slice(0, 13) + '…' : activeProjName;
        this.elProjectsPill.textContent = `📌 ${shortP} (${projCount})`;
      } else {
        this.elProjectsPill.textContent = `📌 PROGETTI (${projCount})`;
      }
      this.elProjectsPill.classList.toggle('has-stale', staleCount > 0);
      if (this.elActiveProjBadge) {
        if (activeProjName) {
          this.elActiveProjBadge.style.display = 'inline-block';
          this.elActiveProjBadge.textContent = `📌 ${activeProjName}`;
        } else {
          this.elActiveProjBadge.style.display = 'none';
        }
      }
    }

    const ansCount = this.state.system ? this.state.system.profileAnsweredCount || 0 : 0;
    if (this.elBtnConosciamoci && this._lastOnboardKey !== ansCount) {
      this._lastOnboardKey = ansCount;
      this.elBtnConosciamoci.textContent = ansCount > 0 ? `🐾 CONOSCIAMOCI (${ansCount})` : '🐾 CONOSCIAMOCI';
    }

    const memCount = this.state.system ? this.state.system.memoryCount || 0 : 0;
    if (this.elMemTotalBadge && this._lastMemKey !== memCount) {
      this._lastMemKey = memCount;
      this.elMemTotalBadge.textContent = String(memCount);
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
        this.elDeadlineToast = this.elDeadlineToast;
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
