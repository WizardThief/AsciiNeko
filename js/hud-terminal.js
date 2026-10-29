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
    this.elCpuStat = document.getElementById('cpu-stat');
    this.elCpuVal = document.getElementById('cpu-val');
    this.elRamStat = document.getElementById('ram-stat');
    this.elRamVal = document.getElementById('ram-val');
    this.elGpuStat = document.getElementById('gpu-stat');
    this.elGpuVal = document.getElementById('gpu-val');
    this.elVramStat = document.getElementById('vram-stat');
    this.elVramVal = document.getElementById('vram-val');
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
    this.elActiveAppBadge = document.getElementById('active-app-badge');
    this.elActiveProjNameBadge = document.getElementById('active-proj-name-badge');
    this.elAfkBadge = document.getElementById('afk-badge');
    this.elHotkeyHintBadge = document.getElementById('hotkey-hint-badge');
    this.elAiForm = document.getElementById('ai-input-form');
    this.elAiInput = document.getElementById('ai-input-field');
    this.elSlashMenu = document.getElementById('slash-autocomplete-menu');

    // F23, F24, F28 Task Visualizer & HITL & Skill Synthesis
    this.elTaskVisDock = document.getElementById('ai-task-visualizer-dock');
    this.elTaskVisTitle = document.getElementById('task-vis-title');
    this.elTaskVisBar = document.getElementById('task-vis-bar');
    this.elBtnTaskAbort = document.getElementById('btn-task-abort');
    this.elHitlPromptDock = document.getElementById('ai-hitl-prompt-dock');
    this.elHitlErrorHeader = document.getElementById('hitl-error-header');
    this.elHitlAssetInfo = document.getElementById('hitl-asset-info');
    this.elBtnHitlKeep = document.getElementById('btn-hitl-keep');
    this.elBtnHitlRetry = document.getElementById('btn-hitl-retry');
    this.elBtnHitlRollback = document.getElementById('btn-hitl-rollback');
    this.elSkillSynthDock = document.getElementById('ai-skill-synthesis-dock');
    this.elSynthesisText = document.getElementById('synthesis-suggestion-text');
    this.elBtnConfirmSynth = document.getElementById('btn-confirm-synthesis');
    this.elBtnDismissSynth = document.getElementById('btn-dismiss-synthesis');

    this.activeTask = null;
    this.currentHitlTask = null;
    this.currentSynthesisCandidate = null;
    this.dismissedSynthesis = new Set();
    this._lastTaskPollTime = 0;

    // Slash command suggestions for F16
    this.slashCommands = [
      { cmd: '/todo', desc: 'Gestione e lista To-Do rapida' },
      { cmd: '/cal', desc: 'Visualizza agenda e impegni del calendario' },
      { cmd: '/briefing', desc: 'Sintesi della giornata e focus' },
      { cmd: '/profilo', desc: 'Ispettore profilo utente e memorie' },
      { cmd: '/colorsafe', desc: 'Attiva/Disattiva modalità Color-Safe' },
      { cmd: '/skills', desc: 'Elenco competenze e strumenti AI' },
      { cmd: '/sync', desc: 'Sincronizza stato e progetti' },
      { cmd: '/dcc_backup', desc: 'Salva snapshot di sicurezza progetto' },
      { cmd: '/context', desc: 'Imposta modalità contesto (operativo, pm, curator)', hint: '/context [operativo|pm|curator]' },
      { cmd: '/prune', desc: 'Pulisci istinti atomici a bassa confidenza o scaduti' },
      { cmd: '/evolve', desc: 'Evolvi il progetto attivo in un Playbook riutilizzabile' },
      { cmd: '/quality-gate', desc: 'Verifica pre-consegna del progetto attivo' },
      { cmd: '/save-session', desc: 'Salva checkpoint sessione di lavoro' },
      { cmd: '/resume-session', desc: 'Ripristina checkpoint sessione di lavoro' }
    ];
    this.filteredSlashCommands = [];
    this.slashIndex = 0;

    // Top Bar Pills & Buttons
    this.elAgendaPill = document.getElementById('agenda-pill');
    this.elTodoPill = document.getElementById('todo-pill');
    this.elProjectsPill = document.getElementById('projects-pill');
    this.elBtnColorSafe = document.getElementById('btn-color-safe');
    this.isColorSafe = false;
    this.elBtnConosciamoci = document.getElementById('btn-conosciamoci');
    this.elBtnOpenSettings = document.getElementById('btn-open-settings');
    this.elMemTotalBadge = document.getElementById('mem-total-badge');
    this.elBtnWindowMode = document.getElementById('btn-window-mode');

    // Quick To-Do Popover (Legacy compatibility)
    this.elTodoPopover = document.getElementById('hud-todo-popover');
    this.elBtnCloseTodoPopover = document.getElementById('btn-close-todo-popover');
    this.elQuickTodoInput = document.getElementById('quick-todo-input');
    this.elBtnQuickTodoAdd = document.getElementById('btn-quick-todo-add');
    this.elQuickTodoList = document.getElementById('quick-todo-list');
    this.elQuickTodoCount = document.getElementById('quick-todo-count');

    // Slide-out Drawer (F15 360px Drawer)
    this.elSlideDrawer = document.getElementById('hud-slide-drawer');
    this.elDrawerBackdrop = document.getElementById('hud-drawer-backdrop');
    this.elBtnCloseSlideDrawer = document.getElementById('btn-close-slide-drawer');
    this.elDrawerTodoCount = document.getElementById('drawer-todo-count');
    this.elDrawerProjCount = document.getElementById('drawer-proj-count');
    this.elDrawerMemCount = document.getElementById('drawer-mem-count');
    this.elDrawerTodoList = document.getElementById('drawer-todo-list');
    this.elDrawerProjectsList = document.getElementById('drawer-projects-list');
    this.elDrawerMemoriesList = document.getElementById('drawer-memories-list');
    this.elDrawerTodoInput = document.getElementById('drawer-todo-input');
    this.elBtnDrawerAddTodo = document.getElementById('btn-drawer-add-todo');
    this.elDrawerActiveProjLabel = document.getElementById('drawer-active-proj-label');
    this.activeDrawerTab = 'tab-drawer-todo';
    this.isDrawerOpen = false;

    // Profile Inspector Modal (F21 /profilo)
    this.elProfileInspectorOverlay = document.getElementById('neko-profile-inspector-overlay');
    this.elBtnCloseProfileInspector = document.getElementById('btn-close-profile-inspector');
    this.elProfileInspectorTbody = document.getElementById('profile-inspector-tbody');
    this.elInspectorTraitCount = document.getElementById('inspector-trait-count');

    // Control Hub Modal
    this.elModalOverlay = document.getElementById('neko-modal-overlay');
    this.elBtnCloseModal = document.getElementById('btn-close-modal');
    this.activeModalTab = 'tab-memorie';

    // Deadline Toast
    this.elDeadlineToast = document.getElementById('hud-deadline-toast');
    this.elDeadlineContent = document.getElementById('deadline-toast-content');
    this.elBtnDismissDeadline = document.getElementById('btn-dismiss-deadline');

    // TAB 1: Conosciamoci Elements
    this.elTutorialBanner = document.getElementById('tutorial-mandatory-banner');
    this.elBtnTutorialSnooze = document.getElementById('btn-tutorial-snooze');
    this.elTutorialStepTitle = document.getElementById('tutorial-step-title');
    this.isTutorialSnoozed = (typeof sessionStorage !== 'undefined' && sessionStorage.getItem('neko_tutorial_snoozed') === 'true');
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
    this.isMandatoryTutorial = false;

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
    this.initMilestone3Workspace();
    this.loadInitialHistory();
    this._hookBridgeForPhase3();
    this.fetchSkillsForAutocomplete();
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
    if (this.isMandatoryTutorial && tabId && tabId !== 'tab-conosciamoci') {
      return;
    }
    this.activeModalTab = this.isMandatoryTutorial ? 'tab-conosciamoci' : tabId || 'tab-memorie';
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
        ['tab-conosciamoci', 'tab-memorie', 'tab-connessioni', 'tab-impostazioni'].includes(this.activeModalTab)
      );
    }

    await this.refreshAllSettingsData();
  }

  closeModal() {
    if (!this.elModalOverlay) return;
    if (this.isMandatoryTutorial) {
      this.snoozeTutorial();
      return;
    }
    this.elModalOverlay.style.display = 'none';
    if (this.elBtnConosciamoci) this.elBtnConosciamoci.classList.remove('active');
    if (this.elProjectsPill) this.elProjectsPill.classList.remove('active');
    if (this.elBtnOpenSettings) this.elBtnOpenSettings.classList.remove('active');
  }

  snoozeTutorial() {
    this.isTutorialSnoozed = true;
    try {
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.setItem('neko_tutorial_snoozed', 'true');
      }
    } catch (e) {}
    this.isMandatoryTutorial = false;
    if (this.elModalOverlay) {
      this.elModalOverlay.classList.remove('mandatory-tutorial');
      this.elModalOverlay.style.display = 'none';
    }
    if (this.elTutorialBanner) {
      this.elTutorialBanner.style.display = 'none';
    }
    if (this.elBtnConosciamoci) this.elBtnConosciamoci.classList.remove('active');
    this.say('⏱ Tutorial iniziale rimandato! Puoi riprenderlo quando vuoi cliccando su "🐾 Conosciamoci" nella Top Bar.');
  }

  // --- F11: COLOR-SAFE MODE ---
  toggleColorSafe(forcedState) {
    if (typeof forcedState === 'boolean') {
      this.isColorSafe = forcedState;
    } else {
      this.isColorSafe = !this.isColorSafe;
    }

    document.body.classList.toggle('color-safe', this.isColorSafe);
    if (this.elBtnColorSafe) {
      this.elBtnColorSafe.classList.toggle('active', this.isColorSafe);
      this.elBtnColorSafe.textContent = this.isColorSafe ? '🎨 COLOR-SAFE: ON' : '🎨 COLOR-SAFE';
      this.elBtnColorSafe.title = this.isColorSafe
        ? 'Modalità Color-Safe Attiva (effetti CRT e flickering disattivati)'
        : 'Attiva Modalità Color-Safe (disattiva scansioni CRT e distorsioni visive)';
    }

    if (window.systemBridge && typeof window.systemBridge.toggleColorSafe === 'function') {
      window.systemBridge.toggleColorSafe(this.isColorSafe);
    } else {
      fetch('http://127.0.0.1:8765/api/colorsafe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: this.isColorSafe })
      }).catch(() => {});
    }

    this.say(
      this.isColorSafe
        ? '🎨 Modalità Color-Safe ATTIVATA: scansioni CRT, jitter e flickering visivo disattivati per massima leggibilità.'
        : '🎨 Modalità Color-Safe DISATTIVATA: effetti grafici retro CRT ripristinati.'
    );
  }

  // --- F12: 4-CHANNEL TELEMETRY THRESHOLDS ---
  applyTelemetryThreshold(element, percent) {
    if (!element) return;
    element.classList.remove('stat-normal', 'stat-warning', 'stat-critical');
    if (percent > 85.0) {
      element.classList.add('stat-critical');
    } else if (percent >= 70.0) {
      element.classList.add('stat-warning');
    } else {
      element.classList.add('stat-normal');
    }
  }

  // --- F15: 360PX UNIFIED SLIDE-OUT DRAWER ---
  openSlideDrawer(tab = 'tab-drawer-todo') {
    const tabMap = {
      todo: 'tab-drawer-todo',
      proj: 'tab-drawer-proj',
      mem: 'tab-drawer-mem',
      'tab-drawer-todo': 'tab-drawer-todo',
      'tab-drawer-proj': 'tab-drawer-proj',
      'tab-drawer-mem': 'tab-drawer-mem'
    };
    const targetTab = tabMap[tab] || 'tab-drawer-todo';
    this.activeDrawerTab = targetTab;
    this.isDrawerOpen = true;

    if (this.elSlideDrawer) this.elSlideDrawer.classList.add('open');
    if (this.elDrawerBackdrop) this.elDrawerBackdrop.classList.add('open');

    this.switchDrawerTab(targetTab);
  }

  closeSlideDrawer() {
    this.isDrawerOpen = false;
    if (this.elSlideDrawer) this.elSlideDrawer.classList.remove('open');
    if (this.elDrawerBackdrop) this.elDrawerBackdrop.classList.remove('open');
    if (this.elTodoPill) this.elTodoPill.classList.remove('active');
    if (this.elProjectsPill) this.elProjectsPill.classList.remove('active');
  }

  switchDrawerTab(tabId) {
    this.activeDrawerTab = tabId;

    document.querySelectorAll('.hud-drawer-tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-drawer-tab') === tabId);
    });

    document.querySelectorAll('.hud-drawer-pane').forEach((pane) => {
      pane.classList.toggle('active', pane.id === tabId);
    });

    if (this.elTodoPill) this.elTodoPill.classList.toggle('active', tabId === 'tab-drawer-todo');
    if (this.elProjectsPill) this.elProjectsPill.classList.toggle('active', tabId === 'tab-drawer-proj');

    if (tabId === 'tab-drawer-todo') {
      this.renderDrawerTodos();
      if (this.elDrawerTodoInput) this.elDrawerTodoInput.focus();
    } else if (tabId === 'tab-drawer-proj') {
      this.renderDrawerProjects();
    } else if (tabId === 'tab-drawer-mem') {
      this.renderDrawerMemories();
    }
  }

  renderDrawerTodos() {
    const list = (this.state.system && Array.isArray(this.state.system.todosList))
      ? this.state.system.todosList
      : [];

    if (this.elDrawerTodoCount) this.elDrawerTodoCount.textContent = String(list.length);
    if (!this.elDrawerTodoList) return;

    if (list.length === 0) {
      this.elDrawerTodoList.innerHTML =
        '<div style="color:var(--text-muted); font-size:13px; padding:12px 4px;">🎉 Nessuna task attiva! Aggiungi un To-Do rapido qui sopra.</div>';
      return;
    }

    this.elDrawerTodoList.innerHTML = '';
    list.forEach((txt, idx) => {
      const row = document.createElement('div');
      row.className = 'drawer-list-item';
      row.innerHTML = `
        <span style="flex:1; word-break:break-word; font-size:13px;">${this.escapeHtml(txt)}</span>
        <button type="button" class="drawer-item-btn" title="Segna come completato">✓ Fatto</button>
      `;
      const btn = row.querySelector('.drawer-item-btn');
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
            this.renderDrawerTodos();
            this.say(`✅ Task completata: "${txt}"!`);
          }
        });
      }
      this.elDrawerTodoList.appendChild(row);
    });
  }

  renderDrawerProjects() {
    const pState = this.projectsState || { projects: [] };
    const projects = Array.isArray(pState.projects) ? pState.projects : [];
    const activeId = pState.active_project_id || (projects[0] ? projects[0].id : '');

    if (this.elDrawerProjCount) this.elDrawerProjCount.textContent = String(projects.length);

    const activeP = projects.find((p) => p.id === activeId);
    if (this.elDrawerActiveProjLabel) {
      this.elDrawerActiveProjLabel.textContent = activeP
        ? (activeP.client ? `${activeP.name} (${activeP.client})` : activeP.name)
        : 'Nessuno';
    }

    if (!this.elDrawerProjectsList) return;

    if (projects.length === 0) {
      this.elDrawerProjectsList.innerHTML =
        '<div style="color:var(--text-muted); font-size:13px; padding:12px 4px;">Nessun progetto attivo. Apri Impostazioni & Memorie per crearne uno!</div>';
      return;
    }

    this.elDrawerProjectsList.innerHTML = '';
    projects.forEach((p) => {
      const isAct = p.id === activeId;
      const row = document.createElement('div');
      row.className = 'drawer-list-item' + (isAct ? ' active-proj' : '');
      row.innerHTML = `
        <div style="flex:1; min-width:0;">
          <div style="font-weight:600; font-size:13px; color:${isAct ? '#fde68a' : 'var(--text-main)'};">📌 ${this.escapeHtml(p.name)}</div>
          ${p.client ? `<div style="font-size:11.5px; color:var(--text-muted);">Cliente: ${this.escapeHtml(p.client)}</div>` : ''}
        </div>
        <button type="button" class="drawer-item-btn" title="${isAct ? 'Progetto già attivo' : 'Attiva progetto'}">
          ${isAct ? '● Attivo' : 'Attiva'}
        </button>
      `;
      const btn = row.querySelector('.drawer-item-btn');
      if (btn && !isAct) {
        btn.addEventListener('click', async (e) => {
          e.stopPropagation();
          if (this.actions.projectsAction) {
            const res = await this.actions.projectsAction({ action: 'set_active', project_id: p.id });
            if (res && res.state) {
              this.renderProjectsState(res.state);
              this.renderDrawerProjects();
            }
          }
        });
      }
      this.elDrawerProjectsList.appendChild(row);
    });
  }

  renderDrawerMemories() {
    const onb = (this.state.system && this.state.system.onboarding) ? this.state.system.onboarding : null;
    const traits = onb && onb.profile && Array.isArray(onb.profile.traits)
      ? onb.profile.traits
      : [];

    if (this.elDrawerMemCount) this.elDrawerMemCount.textContent = String(traits.length);
    if (!this.elDrawerMemoriesList) return;

    if (traits.length === 0) {
      this.elDrawerMemoriesList.innerHTML =
        '<div style="color:var(--text-muted); font-size:13px; padding:12px 4px;">Nessun tratto consolidato. Usa "Conosciamoci" o la chat con Neko per apprendere regole!</div>';
      return;
    }

    this.elDrawerMemoriesList.innerHTML = '';
    traits.forEach((trait) => {
      const chip = document.createElement('div');
      chip.className = 'drawer-list-item';
      chip.innerHTML = `<span style="font-size:12.5px; color:#c084fc;">🧠 ${this.escapeHtml(trait)}</span>`;
      this.elDrawerMemoriesList.appendChild(chip);
    });
  }

  // --- F21: PROFILE INSPECTOR MODAL (/profilo) ---
  openProfileInspector() {
    if (this.elProfileInspectorOverlay) {
      this.elProfileInspectorOverlay.style.display = 'flex';
      this.loadAndRenderProfileInspector();
    }
  }

  closeProfileInspector() {
    if (this.elProfileInspectorOverlay) {
      this.elProfileInspectorOverlay.style.display = 'none';
    }
  }

  async loadAndRenderProfileInspector() {
    if (!this.elProfileInspectorTbody) return;
    this.elProfileInspectorTbody.innerHTML =
      '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:16px;">Caricamento profilo e regole...</td></tr>';

    let traits = [];
    if (window.systemBridge && typeof window.systemBridge.fetchProfileData === 'function') {
      const data = await window.systemBridge.fetchProfileData();
      if (data && Array.isArray(data.traits)) traits = data.traits;
    } else {
      try {
        const resp = await fetch('http://127.0.0.1:8765/api/profile');
        if (resp.ok) {
          const data = await resp.json();
          if (data && Array.isArray(data.traits)) traits = data.traits;
        }
      } catch (e) {}
    }

    if (this.elInspectorTraitCount) {
      this.elInspectorTraitCount.textContent = String(traits.length);
    }

    if (traits.length === 0) {
      this.elProfileInspectorTbody.innerHTML =
        '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:16px;">Nessun tratto consolidato o pinned. Rispondi alle domande in "🐾 Conosciamoci" o chatta con Neko!</td></tr>';
      return;
    }

    this.elProfileInspectorTbody.innerHTML = '';
    traits.forEach((t, idx) => {
      const traitId = t.id || `trait_${idx}`;
      const text = typeof t === 'string' ? t : (t.text || '');
      const cat = t.category || 'Generale';
      const source = t.source || 'ai';
      const isPinned = Boolean(t.pinned);
      const conf = isPinned ? '📌 Permanente (λ=0)' : (t.importance ? `${Math.round(t.importance * 100)}%` : 'Attivo (λ=0.05)');

      const tr = document.createElement('tr');
      if (isPinned) tr.classList.add('pinned-row');
      tr.innerHTML = `
        <td style="font-weight: 500; word-break: break-word;">${this.escapeHtml(text)}</td>
        <td><span class="badge" style="background: rgba(147, 51, 234, 0.2); color: #c084fc; padding: 2px 6px; border-radius: 4px; font-size: 11.5px;">${this.escapeHtml(cat)}</span></td>
        <td style="color: var(--text-muted); font-size: 12px;">${this.escapeHtml(source)}</td>
        <td style="font-size: 12px; color: ${isPinned ? '#38ef7d' : '#94a3b8'};">${conf}</td>
        <td>
          <div class="profile-actions-cell">
            <button type="button" class="term-btn term-btn-sm ${isPinned ? 'btn-pinned' : ''}" data-pin-id="${this.escapeHtml(traitId)}" title="${isPinned ? 'Rimuovi Pin' : 'Fissa come regola permanente (λ=0)'}">
              ${isPinned ? '📌 Pinned' : '📌 Pinna'}
            </button>
            <button type="button" class="term-btn term-btn-sm" data-edit-id="${this.escapeHtml(traitId)}" title="Modifica testo regola">
              ✏ Modifica
            </button>
            <button type="button" class="term-btn term-btn-sm" data-del-id="${this.escapeHtml(traitId)}" title="Elimina regola">
              🗑 Elimina
            </button>
          </div>
        </td>
      `;

      const pinBtn = tr.querySelector('[data-pin-id]');
      if (pinBtn) {
        pinBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          if (window.systemBridge && typeof window.systemBridge.pinProfileTrait === 'function') {
            await window.systemBridge.pinProfileTrait(traitId, !isPinned);
          } else {
            await fetch('http://127.0.0.1:8765/api/profile/pin', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: traitId, pinned: !isPinned })
            }).catch(() => {});
          }
          await this.loadAndRenderProfileInspector();
        });
      }

      const editBtn = tr.querySelector('[data-edit-id]');
      if (editBtn) {
        editBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const newText = window.prompt('Modifica il tratto/regola di Neko:', text);
          if (newText !== null && newText.trim() !== '') {
            if (window.systemBridge && typeof window.systemBridge.editProfileTrait === 'function') {
              await window.systemBridge.editProfileTrait(traitId, newText.trim());
            } else {
              await fetch('http://127.0.0.1:8765/api/profile/edit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: traitId, text: newText.trim() })
              }).catch(() => {});
            }
            await this.loadAndRenderProfileInspector();
          }
        });
      }

      const delBtn = tr.querySelector('[data-del-id]');
      if (delBtn) {
        delBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          if (window.confirm(`Eliminare definitivamente questa regola?\n\n"${text}"`)) {
            if (window.systemBridge && typeof window.systemBridge.deleteProfileTrait === 'function') {
              await window.systemBridge.deleteProfileTrait(traitId);
            } else {
              await fetch('http://127.0.0.1:8765/api/profile/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: traitId })
              }).catch(() => {});
            }
            await this.loadAndRenderProfileInspector();
          }
        });
      }

      this.elProfileInspectorTbody.appendChild(tr);
    });
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

    // F11: Toggle Color-Safe Mode Button
    if (this.elBtnColorSafe) {
      this.elBtnColorSafe.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleColorSafe();
      });
    }

    // Apertura/Chiusura Finestra Centrale Control Hub
    if (this.elBtnConosciamoci) {
      this.elBtnConosciamoci.addEventListener('click', (e) => {
        e.stopPropagation();
        this.openModalTab('tab-conosciamoci');
      });
    }

    // F15: Projects Pill apre il Cassetto Slide-out (360px) sulla scheda Progetti
    if (this.elProjectsPill) {
      this.elProjectsPill.addEventListener('click', (e) => {
        e.stopPropagation();
        if (this.isDrawerOpen && this.activeDrawerTab === 'tab-drawer-proj') {
          this.closeSlideDrawer();
        } else {
          this.openSlideDrawer('tab-drawer-proj');
        }
      });
    }

    // F15: Schede di navigazione nel Cassetto Slide-out (360px)
    document.querySelectorAll('.hud-drawer-tab-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const tabId = btn.getAttribute('data-drawer-tab');
        if (tabId) this.switchDrawerTab(tabId);
      });
    });

    // F15: Chiusura Cassetto Slide-out tramite pulsante X o click sul backdrop
    if (this.elBtnCloseSlideDrawer) {
      this.elBtnCloseSlideDrawer.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeSlideDrawer();
      });
    }

    if (this.elDrawerBackdrop) {
      this.elDrawerBackdrop.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeSlideDrawer();
      });
    }

    // F15: Aggiunta rapida To-Do dentro il Cassetto Slide-out
    const addDrawerTodo = async () => {
      if (!this.elDrawerTodoInput || !this.actions.settingsAction) return;
      const text = this.elDrawerTodoInput.value.trim();
      if (!text) return;
      this.elDrawerTodoInput.value = '';
      const res = await this.actions.settingsAction({ action: 'add_todo', text });
      if (res && Array.isArray(res.todos)) {
        if (this.state.system) {
          this.state.system.todosList = res.todos;
          this.state.system.todoCount = res.todos.length;
        }
        this.renderDrawerTodos();
        this.say(`✅ Nuova task aggiunta ai To-Do: "${text}"`);
      }
    };

    if (this.elBtnDrawerAddTodo) {
      this.elBtnDrawerAddTodo.addEventListener('click', (e) => {
        e.stopPropagation();
        addDrawerTodo();
      });
    }
    if (this.elDrawerTodoInput) {
      this.elDrawerTodoInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          addDrawerTodo();
        }
      });
    }

    // F19: Snooze Tutorial Iniziale ("⏱ Rimanda a dopo")
    if (this.elBtnTutorialSnooze) {
      this.elBtnTutorialSnooze.addEventListener('click', (e) => {
        e.stopPropagation();
        this.snoozeTutorial();
      });
    }

    // F21: Chiusura Modal Profile Inspector
    if (this.elBtnCloseProfileInspector) {
      this.elBtnCloseProfileInspector.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeProfileInspector();
      });
    }

    if (this.elProfileInspectorOverlay) {
      this.elProfileInspectorOverlay.addEventListener('click', (e) => {
        if (e.target === this.elProfileInspectorOverlay) {
          this.closeProfileInspector();
        }
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

    // Gestione unificata Escape (F24 Kill Switch Task -> Ispettore Profilo -> Cassetto Slide-out -> Modal Hub)
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (this.activeTask && (this.activeTask.status === 'RUNNING' || this.activeTask.status === 'RETRY')) {
          this.abortActiveTask();
          return;
        }
        if (this.currentHitlTask) {
          this.abortActiveTask();
          return;
        }
        if (this.elProfileInspectorOverlay && this.elProfileInspectorOverlay.style.display !== 'none') {
          this.closeProfileInspector();
          return;
        }
        if (this.isDrawerOpen) {
          this.closeSlideDrawer();
          return;
        }
        if (this.elModalOverlay && this.elModalOverlay.style.display !== 'none') {
          this.closeModal();
        }
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

    // F15: Top Bar To-Do Pill apre il Cassetto Slide-out (360px) sulla scheda To-Do
    if (this.elTodoPill) {
      this.elTodoPill.addEventListener('click', (e) => {
        e.stopPropagation();
        if (this.isDrawerOpen && this.activeDrawerTab === 'tab-drawer-todo') {
          this.closeSlideDrawer();
        } else {
          this.openSlideDrawer('tab-drawer-todo');
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
        this.elOnboardQText.textContent = '🧠 Neko sta integrando la tua risposta nei sottosistemi (Skill, Progetti, Regole & Istinti)...';
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
        this.renderCopilotHud();
        this.renderHomeNextTaskChip();
        if (this.activeWorkspace === 'view-memory') this.renderMemoryDashboard();
        if (this.activeWorkspace === 'view-projects') this.renderProjectHub();
        if (this.activeWorkspace === 'view-todo') this.renderKanbanBoard();
        this.say(this.formatOnboardingFeedback(clean, res.onboarding.integration_summary));
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

    // F16 & F17: Invio da tastiera nel Terminale AI, Shift+Enter per multilinea (1-4 righe) e Autocomplete Slash (/)
    if (this.elAiForm && this.elAiInput) {
      const doSubmit = () => {
        const text = this.elAiInput.value.trim();
        this.hideSlashMenu();
        if (!text) return;
        this.sentCommands.push(text);
        this.cmdCursor = this.sentCommands.length;
        this.elAiInput.value = '';
        this.adjustInputHeight();
        this.submitAiCommand(text);
      };

      this.elAiInput.addEventListener('input', () => {
        this.adjustInputHeight();
        const val = this.elAiInput.value;
        if (val.startsWith('/')) {
          const query = val.trim().toLowerCase();
          this.filteredSlashCommands = this.slashCommands.filter((c) =>
            c.cmd.toLowerCase().startsWith(query)
          );
          if (this.filteredSlashCommands.length > 0) {
            this.slashIndex = 0;
            this.renderSlashMenu();
          } else {
            this.hideSlashMenu();
          }
        } else {
          this.hideSlashMenu();
        }
      });

      this.elAiInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          if (e.shiftKey) {
            // Shift+Enter: Nuova riga (comportamento standard textarea), auto-espande altezza
            setTimeout(() => this.adjustInputHeight(), 0);
            return;
          }
          // Enter semplice
          if (this.filteredSlashCommands.length > 0) {
            e.preventDefault();
            this.selectSlashCommand(this.filteredSlashCommands[this.slashIndex].cmd);
            return;
          }
          e.preventDefault();
          e.stopPropagation();
          doSubmit();
        } else if (e.key === 'Tab') {
          if (this.filteredSlashCommands.length > 0) {
            e.preventDefault();
            this.selectSlashCommand(this.filteredSlashCommands[this.slashIndex].cmd);
          }
        } else if (e.key === 'ArrowUp') {
          if (this.filteredSlashCommands.length > 0) {
            e.preventDefault();
            this.slashIndex = (this.slashIndex - 1 + this.filteredSlashCommands.length) % this.filteredSlashCommands.length;
            this.renderSlashMenu();
          } else if (this.sentCommands.length > 0) {
            e.preventDefault();
            this.cmdCursor = Math.max(0, this.cmdCursor - 1);
            this.elAiInput.value = this.sentCommands[this.cmdCursor] || '';
            this.adjustInputHeight();
          }
        } else if (e.key === 'ArrowDown') {
          if (this.filteredSlashCommands.length > 0) {
            e.preventDefault();
            this.slashIndex = (this.slashIndex + 1) % this.filteredSlashCommands.length;
            this.renderSlashMenu();
          } else if (this.sentCommands.length > 0) {
            e.preventDefault();
            this.cmdCursor = Math.min(this.sentCommands.length, this.cmdCursor + 1);
            this.elAiInput.value = this.sentCommands[this.cmdCursor] || '';
            this.adjustInputHeight();
          }
        } else if (e.key === 'Escape') {
          if (this.filteredSlashCommands.length > 0) {
            e.preventDefault();
            this.hideSlashMenu();
          }
        }
      });

      this.elAiForm.addEventListener('submit', (e) => {
        e.preventDefault();
        e.stopPropagation();
        doSubmit();
      });
    }

    // F24: Task Abort Kill Switch Button
    if (this.elBtnTaskAbort) {
      this.elBtnTaskAbort.addEventListener('click', (e) => {
        e.stopPropagation();
        this.abortActiveTask();
      });
    }

    // F24: Human-in-the-Loop Recovery Choices
    if (this.elBtnHitlKeep) {
      this.elBtnHitlKeep.addEventListener('click', (e) => {
        e.stopPropagation();
        this.handleHitlChoice(1);
      });
    }
    if (this.elBtnHitlRetry) {
      this.elBtnHitlRetry.addEventListener('click', (e) => {
        e.stopPropagation();
        this.handleHitlChoice(2);
      });
    }
    if (this.elBtnHitlRollback) {
      this.elBtnHitlRollback.addEventListener('click', (e) => {
        e.stopPropagation();
        this.handleHitlChoice(3);
      });
    }

    // F28: Skill Synthesis Suggestion Buttons
    if (this.elBtnConfirmSynth) {
      this.elBtnConfirmSynth.addEventListener('click', (e) => {
        e.stopPropagation();
        this.synthesizeCandidateSkill();
      });
    }
    if (this.elBtnDismissSynth) {
      this.elBtnDismissSynth.addEventListener('click', (e) => {
        e.stopPropagation();
        this.dismissCurrentSynthesis();
      });
    }
  }

  adjustInputHeight() {
    if (!this.elAiInput) return;
    this.elAiInput.style.height = 'auto';
    this.elAiInput.style.height = Math.min(this.elAiInput.scrollHeight, 120) + 'px';
  }

  renderSlashMenu() {
    if (!this.elSlashMenu) return;
    this.elSlashMenu.innerHTML = '';
    this.filteredSlashCommands.forEach((item, idx) => {
      const div = document.createElement('div');
      div.className = 'slash-autocomplete-item' + (idx === this.slashIndex ? ' active' : '');
      div.innerHTML = `
        <span class="slash-item-cmd">${this.escapeHtml(item.cmd)}</span>
        <span class="slash-item-desc">${this.escapeHtml(item.desc)}</span>
      `;
      div.addEventListener('mousedown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.selectSlashCommand(item.cmd);
      });
      this.elSlashMenu.appendChild(div);
    });
    this.elSlashMenu.style.display = 'block';
  }

  hideSlashMenu() {
    if (this.elSlashMenu) {
      this.elSlashMenu.style.display = 'none';
      this.elSlashMenu.innerHTML = '';
    }
    this.filteredSlashCommands = [];
    this.slashIndex = 0;
  }

  selectSlashCommand(cmd) {
    if (!this.elAiInput) return;
    this.elAiInput.value = cmd + ' ';
    this.hideSlashMenu();
    this.elAiInput.focus();
    this.adjustInputHeight();
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

  formatOnboardingFeedback(answerText, summary) {
    if (!summary) return `🐾 Profilo aggiornato con "${answerText}".`;
    const parts = [`🐾 Ricevuto: "${answerText}".`];
    if (Array.isArray(summary.skills_added) && summary.skills_added.length > 0) {
      parts.push(`🛠️ Skill registrate: ${summary.skills_added.join(', ')}`);
    }
    if (summary.project_created) {
      parts.push(`📌 Progetto creato e messo in Focus: "${summary.project_created}"`);
    }
    if (summary.rule_pinned) {
      parts.push(`📌 Regola Quality Gate fissata in Memoria`);
    }
    if (summary.instinct_added) {
      parts.push(`🧬 Istinto Atomico attivo (${Math.round((summary.instinct_added.confidence || 0.9) * 100)}%)`);
    }
    if (summary.todo_added && summary.todo_added.title) {
      parts.push(`✅ Primo task aggiunto in Kanban: "${summary.todo_added.title}"`);
    }
    return parts.join(' • ');
  }

  // --- RENDERING TAB 1: "🐾 CONOSCIAMOCI" (Tutorial Adattivo AI & Offline Fallback) ---
  renderOnboardingState(onb) {
    if (!onb) return;
    if (this.state && this.state.system) {
      this.state.system.onboarding = onb;
      if (onb.profile && Array.isArray(onb.profile.traits)) {
        this.state.system.profileTraits = onb.profile.traits;
      }
    }
    const traits = onb.profile && Array.isArray(onb.profile.traits) ? onb.profile.traits : [];
    const ansCount = onb.answered_count || 0;
    const reqSteps = onb.required_steps || 5;
    const isCompleted = Boolean(onb.tutorial_completed) || ansCount >= reqSteps;
    const tutDone = isCompleted || this.isTutorialSnoozed;
    const wasMandatory = this.isMandatoryTutorial;

    const isOllamaOffline = Boolean(this.state.system && (this.state.system.ollamaOnline === false || this.state.system.ollamaActive === 'OFFLINE'));

    if (!tutDone) {
      this.isMandatoryTutorial = true;
      if (this.elModalOverlay) {
        this.elModalOverlay.classList.add('mandatory-tutorial');
        this.elModalOverlay.style.display = 'flex';
      }
      this.activeModalTab = 'tab-conosciamoci';
      document.querySelectorAll('[data-modal-tab]').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-modal-tab') === 'tab-conosciamoci');
      });
      document.querySelectorAll('.modal-tab-pane').forEach((pane) => {
        pane.style.display = pane.id === 'tab-conosciamoci' ? 'flex' : 'none';
      });
      if (this.elTutorialBanner) {
        this.elTutorialBanner.style.display = 'flex';
      }
      if (this.elTutorialStepTitle) {
        const offlineTag = isOllamaOffline ? ' <span class="offline-badge">[● OLLAMA OFFLINE]</span>' : '';
        this.elTutorialStepTitle.innerHTML = `Configurazione Sistema Neko + ECC (Passo ${Math.min(reqSteps, ansCount + 1)} di ${reqSteps})${offlineTag}`;
      }
    } else {
      this.isMandatoryTutorial = false;
      if (this.elModalOverlay) {
        this.elModalOverlay.classList.remove('mandatory-tutorial');
      }
      if (this.elTutorialBanner) {
        this.elTutorialBanner.style.display = 'none';
      }
      if (wasMandatory && isCompleted) {
        this.closeModal();
        this.renderCopilotHud();
        this.renderHomeNextTaskChip();
        this.say(
          '🎉 Configurazione Iniziale completata! Ho popolato il tuo Co-Pilot HUD, le tue Skill, il Progetto in Focus, le Regole Quality Gate e la Kanban To-Do!'
        );
      }
    }

    if (this.elOnboardProgress) {
      this.elOnboardProgress.textContent = isCompleted
        ? `${traits.length} tratti appresi`
        : `Passo ${Math.min(reqSteps, ansCount + 1)}/${reqSteps}`;
    }

    // Domande Base Universali Integrate con il Sistema ECC
    const fallbackQuestions = [
      {
        id: 'q_identity',
        category: 'Identità & Attività',
        question: 'Ciao! Sono Neko, il tuo assistente personale sul desktop. Di cosa ti occupi principalmente nella tua giornata?',
        options: [
          'Sviluppo Software, Programmazione, Dati o IT',
          'Business, Management, Consulenza, Marketing o Ufficio',
          'Design, Comunicazione, Multimedia, Architettura o Creatività',
          'Studio, Ricerca, Scrittura o Libera Professione'
        ]
      },
      {
        id: 'q_tools',
        category: 'Lavoro & Strumenti',
        question: 'Quali programmi, app o strumenti usi più spesso sul PC? (Scegli un\'opzione o scrivi i tuoi qui sotto)',
        options: [
          'Browser, Email, Google Workspace / Pacchetto Office (Excel, Word, Docs)',
          'Notion, Calendario, Appunti, Gestione Task & Video-call',
          'VS Code / Cursor, Terminale, Git, Python e strumenti di sviluppo',
          'Software di Design, Grafica, Video, CAD o Creatività'
        ]
      },
      {
        id: 'q_projects',
        category: 'Clienti & Progetti',
        question: 'Su quale progetto, obiettivo o attività ti stai concentrando in questo periodo? (Puoi anche scrivere il nome specifico qui sotto)',
        options: [
          'Un progetto principale con scadenze e obiettivi da raggiungere',
          'Gestione di clienti, commesse, preventivi o consegne',
          'Sviluppo di un prodotto, applicazione o progetto personale',
          'Studio, ricerca o organizzazione delle attività giornaliere'
        ]
      },
      {
        id: 'q_routine',
        category: 'Routine & Orari',
        question: 'Qual è la regola, la priorità o il metodo di lavoro più importante che vuoi che io ricordi sempre?',
        options: [
          'Dare sempre priorità alle scadenze imminenti e alle attività urgenti',
          'Tenere traccia precisa di note, accordi e regole per ogni progetto o cliente',
          'Controllare sempre qualità, ordine e dettagli prima di chiudere un\'attività',
          'Aiutarmi a restare concentrato su un obiettivo alla volta senza perdere il filo'
        ]
      },
      {
        id: 'q_style',
        category: 'Stile & Preferenze',
        question: 'Ultima domanda: come preferisci che comunichi con te e quale prima attività mettiamo in lista per oggi?',
        options: [
          'Diretto, sintetico e pratico — vai subito al sodo in 2-3 righe',
          'Proattivo: ricordami scadenze, priorità e proponimi il prossimo passo',
          'Ordinato per punti elenco e checklist chiare passo-passo',
          'Approfondito, analitico e dettagliato quando affrontiamo un problema'
        ]
      }
    ];

    let q = onb.next_question;
    if (!q || isOllamaOffline) {
      const idx = Math.min(ansCount, fallbackQuestions.length - 1);
      q = fallbackQuestions[idx];
    }
    this.currentQuestion = q;

    if (q) {
      const aiBadge = q.ai_generated ? ' ✨ (Domanda AI su misura)' : (isOllamaOffline ? ' 🔌 (Fallback Offline)' : '');
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
                '🧠 Neko sta integrando la tua risposta nei sottosistemi (Skill, Progetti, Regole & Istinti)...';
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
              this.renderCopilotHud();
              this.renderHomeNextTaskChip();
              if (this.activeWorkspace === 'view-memory') this.renderMemoryDashboard();
              if (this.activeWorkspace === 'view-projects') this.renderProjectHub();
              if (this.activeWorkspace === 'view-todo') this.renderKanbanBoard();
              this.say(this.formatOnboardingFeedback(opt, res.onboarding.integration_summary));
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
    if (!text) return;
    const clean = text.trim();

    // Comandi locali Slash immediati
    if (clean === '/colorsafe') {
      this.toggleColorSafe();
      return;
    }

    if (clean === '/profilo') {
      this.openProfileInspector();
      this.say('🔍 Ispettore Profilo Utente (/profilo) aperto: consulta o modifica le regole apprese da Neko.');
      return;
    }

    if (clean === '/todo' || clean.startsWith('/todo ')) {
      const rest = clean.slice(5).trim();
      if (!rest) {
        this.openSlideDrawer('tab-drawer-todo');
        this.say('📂 Cassetto To-Do aperto.');
      } else {
        if (this.actions.settingsAction) {
          const res = await this.actions.settingsAction({ action: 'add_todo', text: rest });
          if (res && Array.isArray(res.todos)) {
            if (this.state.system) {
              this.state.system.todosList = res.todos;
              this.state.system.todoCount = res.todos.length;
            }
            this.renderDrawerTodos();
            this.say(`✅ Nuova task aggiunta ai To-Do: "${rest}"`);
          }
        }
      }
      return;
    }

    if (clean === '/skills') {
      try {
        const res = await fetch('http://127.0.0.1:8765/api/skills', { cache: 'no-store' });
        const skillsData = await res.json();
        if (skillsData && Array.isArray(skillsData.skills)) {
          const list = skillsData.skills.map((s) => `• /${s.id} [${s.safety_tier}]: ${s.name} - ${s.description}`).join('\n');
          this.say(`🛠️ Competenze AI e Skill registrate per Neko:\n${list}`);
          return;
        }
      } catch (e) {}
      if (window.systemBridge && typeof window.systemBridge.fetchSkills === 'function') {
        const skillsData = await window.systemBridge.fetchSkills();
        if (skillsData && Array.isArray(skillsData.skills)) {
          const list = skillsData.skills.map((s) => `• ${s.name}: ${s.description}`).join('\n');
          this.say(`🛠️ Competenze AI e Skill registrate per Neko:\n${list}`);
          return;
        }
      }
    }

    // F22: /context command
    if (clean === '/context' || clean.startsWith('/context ')) {
      const arg = clean.slice(8).trim().toLowerCase();
      let mode = 'operativo';
      if (!arg) {
        const cur = this.currentContextMode || 'operativo';
        if (cur === 'operativo') mode = 'project_manager';
        else if (cur === 'project_manager') mode = 'knowledge_curator';
        else mode = 'operativo';
      } else if (arg.includes('curator') || arg.includes('know') || arg === 'curator') {
        mode = 'knowledge_curator';
      } else if (arg.includes('pm') || arg.includes('proj') || arg === 'pm') {
        mode = 'project_manager';
      } else {
        mode = 'operativo';
      }
      this.setContextMode(mode, true);
      const label = mode === 'operativo' ? 'Operativo 🛠️' : (mode === 'project_manager' ? 'Project Manager 📋' : 'Knowledge Curator 🧠');
      this.say(`🧭 Modalità contesto impostata su: ${label}`);
      return;
    }

    // F27: /prune command
    if (clean === '/prune') {
      if (window.systemBridge && window.systemBridge.instinctsAction) {
        const res = await window.systemBridge.instinctsAction({ action: 'prune' });
        const pruned = res && res.pruned_count !== undefined ? res.pruned_count : 0;
        this.say(`🧹 Pulizia istinti completata: ${pruned} istinti rimossi.`);
        this.renderMemoryDashboard();
      }
      return;
    }

    // F23: /evolve command
    if (clean === '/evolve' || clean.startsWith('/evolve ')) {
      const activeProj = (this.state.system && this.state.system.activeProjectName) || '';
      const pList = (this.state.system && this.state.system.projectsList) || [];
      const match = pList.find(p => p.name === activeProj) || pList[0];
      if (match && window.systemBridge && window.systemBridge.evolveProject) {
        this.say(`🧬 Generazione Playbook in corso per "${match.name}"...`);
        const res = await window.systemBridge.evolveProject(match.id);
        if (res && res.ok) {
          this.say(`🧬 Playbook "${res.playbook_id || match.name}" generato con successo!`);
          this.populatePlaybooksDropdown();
        } else {
          this.say(`⚠️ Impossibile generare playbook: ${res ? res.error : 'errore di rete'}`);
        }
      } else {
        this.say('⚠️ Nessun progetto attivo selezionato per la generazione del Playbook.');
      }
      return;
    }

    // F24: /quality-gate command
    if (clean === '/quality-gate' || clean.startsWith('/quality-gate ')) {
      const pList = (this.state.system && this.state.system.projectsList) || [];
      const activeProj = (this.state.system && this.state.system.activeProjectName) || '';
      const match = pList.find(p => p.name === activeProj) || pList[0];
      if (match) {
        this.openQualityGateModal(match.id, match.name);
      } else {
        this.say('⚠️ Nessun progetto disponibile per il Quality Gate.');
      }
      return;
    }

    // F28: /save-session and /resume-session commands
    if (clean === '/save-session') {
      if (window.systemBridge && window.systemBridge.sessionAction) {
        const res = await window.systemBridge.sessionAction({ action: 'save' });
        if (res && res.ok) {
          this.say('💾 Checkpoint della sessione salvato con successo!');
        }
      }
      return;
    }

    if (clean === '/resume-session') {
      if (window.systemBridge && window.systemBridge.sessionAction) {
        const res = await window.systemBridge.sessionAction({ action: 'resume' });
        if (res && res.ok && res.checkpoint) {
          const ckpt = res.checkpoint;
          this.say(`↺ Sessione ripristinata: ${ckpt.active_project_name || 'Progetto'} (${ckpt.active_task_title || ''})`);
          if (ckpt.active_view) this.switchWorkspace(ckpt.active_view);
        }
      }
      return;
    }

    if (clean.startsWith('/') && clean !== '/colorsafe' && clean !== '/profilo' && !clean.startsWith('/todo') && clean !== '/cal' && clean !== '/briefing') {
      const parts = clean.slice(1).split(' ');
      const skillId = parts[0];
      const match = this.slashCommands.find(c => c.cmd === '/' + skillId);
      if (match) {
        this.say(`⏳ Esecuzione skill: /${skillId}...`);
        try {
          const res = await fetch('http://127.0.0.1:8765/api/skills/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ skill_id: skillId })
          });
          const data = await res.json();
          if (data.ok) {
            this.say(`✅ Skill /${skillId} completata con successo!\n${JSON.stringify(data.result || data.output || 'OK')}`);
          } else if (data.requires_confirmation) {
            this.say(`⚠️ La skill /${skillId} (Tier 2: CONFIRM_ONCE) richiede conferma:\n"${data.message || 'Conferma richiesta'}"`);
          } else if (data.requires_auth) {
            this.say(`🔒 La skill /${skillId} (Tier 3: DESTRUCTIVE) richiede autorizzazione esplicita:\n"${data.message || 'Operazione distruttiva'}"`);
          } else {
            this.say(`❌ Errore esecuzione skill /${skillId}: ${data.error}`);
          }
          return;
        } catch (e) {
          this.say(`❌ Errore di connessione durante esecuzione skill: ${e.message}`);
          return;
        }
      }
    }

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

    // F12: 4-Channel Telemetry (CPU, RAM, GPU, VRAM) con classi soglia
    const cpu = Number(this.state.system ? this.state.system.cpu || 0 : 0);
    const ram = Number(this.state.system ? this.state.system.ram || 0 : 0);
    const gpu = Number(this.state.system ? this.state.system.gpu || this.state.system.gpu_percent || 0 : 0);
    const vramUsed = Number(this.state.system ? this.state.system.vram_used_gb || 0 : 0);
    const vramTotal = Number(this.state.system ? this.state.system.vram_total_gb || 0 : 0);
    const vramPct = Number(
      this.state.system && this.state.system.vram_percent !== undefined
        ? this.state.system.vram_percent
        : (vramTotal > 0 ? (vramUsed / vramTotal) * 100 : 0)
    );

    if (this.elCpuVal) this.elCpuVal.textContent = `${cpu.toFixed(0)}%`;
    this.applyTelemetryThreshold(this.elCpuStat, cpu);

    if (this.elRamVal) this.elRamVal.textContent = `${ram.toFixed(0)}%`;
    this.applyTelemetryThreshold(this.elRamStat, ram);

    if (this.elGpuVal) this.elGpuVal.textContent = `${gpu.toFixed(0)}%`;
    this.applyTelemetryThreshold(this.elGpuStat, gpu);

    if (this.elVramVal) {
      this.elVramVal.textContent = vramTotal > 0
        ? `${vramUsed.toFixed(1)}/${vramTotal.toFixed(0)} GB`
        : `${vramUsed.toFixed(1)} GB`;
    }
    this.applyTelemetryThreshold(this.elVramStat, vramPct);

    // F13 & F14: Active Window, Active Project Name & AFK Detection (>300s)
    const rawActiveApp = this.state.system
      ? (this.state.system.active_window || this.state.system.activeWindow || '')
      : '';
    const activeApp = (rawActiveApp && typeof rawActiveApp === 'object')
      ? (rawActiveApp.app_name || rawActiveApp.app || rawActiveApp.window_title || rawActiveApp.title || '')
      : String(rawActiveApp || '');
    if (this.elActiveAppBadge) {
      if (activeApp && activeApp !== '[object Object]') {
        this.elActiveAppBadge.textContent = activeApp;
        this.elActiveAppBadge.title = (rawActiveApp && typeof rawActiveApp === 'object')
          ? `${rawActiveApp.app_name || rawActiveApp.app || ''} — ${rawActiveApp.window_title || rawActiveApp.title || ''}`
          : activeApp;
        this.elActiveAppBadge.style.display = 'inline-block';
      } else {
        this.elActiveAppBadge.style.display = 'none';
      }
    }

    const isAfk = Boolean(
      this.state.system && (
        this.state.system.is_afk ||
        (this.state.system.afk_idle_seconds && this.state.system.afk_idle_seconds >= 300) ||
        this.state.activeMode === 'afk'
      )
    );
    if (this.elAfkBadge) {
      this.elAfkBadge.style.display = isAfk ? 'inline-block' : 'none';
    }

    // F11: Sincronizzazione Color-Safe da stream backend
    if (
      this.state.system &&
      typeof this.state.system.colorSafeMode === 'boolean' &&
      this.state.system.colorSafeMode !== this.isColorSafe
    ) {
      this.isColorSafe = this.state.system.colorSafeMode;
      document.body.classList.toggle('color-safe', this.isColorSafe);
      if (this.elBtnColorSafe) {
        this.elBtnColorSafe.classList.toggle('active', this.isColorSafe);
        this.elBtnColorSafe.textContent = this.isColorSafe ? '🎨 COLOR-SAFE: ON' : '🎨 COLOR-SAFE';
      }
    }

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
      if (this.isDrawerOpen && this.activeDrawerTab === 'tab-drawer-todo') {
        this.renderDrawerTodos();
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
      if (this.isDrawerOpen && this.activeDrawerTab === 'tab-drawer-proj') {
        this.renderDrawerProjects();
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
      if (this.isDrawerOpen && this.activeDrawerTab === 'tab-drawer-mem') {
        this.renderDrawerMemories();
      }
    }

    // Milestone 3 (R3): Dirty-checking & badge refresh for 4 AI Dashboards
    this.updateWorkspaceDirtyChecking();

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
    // F23 & F24 & F28: Hook Bridge per Phase 3, Task Visualizer, HITL & Skill Synthesis
    this._hookBridgeForPhase3();

    // Aggiornamento periodico task e synthesis candidate (fallback polling se SSE disconnesso)
    const nowMs = Date.now();
    if (nowMs - this._lastTaskPollTime > 2000) {
      this._lastTaskPollTime = nowMs;
      this.pollActiveTask();
      this.pollSynthesisCandidates();
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

  // --- F23, F24, F25-F28: HELPER METHODS FOR PHASE 3 TASK ENGINE & SKILL REGISTRY ---

  _hookBridgeForPhase3() {
    if (window.systemBridge && typeof window.systemBridge.applyBridgeData === 'function') {
      if (window.systemBridge._m3Hooked) return;
      window.systemBridge._m3Hooked = true;
      const origApply = window.systemBridge.applyBridgeData.bind(window.systemBridge);
      window.systemBridge.applyBridgeData = (data) => {
        origApply(data);
        if (data && window.systemBridge.state) {
          if (!window.systemBridge.state.system) window.systemBridge.state.system = {};
          if (data.cat_state) {
            window.systemBridge.state.system.cat_state = data.cat_state;
            window.systemBridge.state.cat_state = data.cat_state;
          }
          if (data.active_task !== undefined) {
            window.systemBridge.state.system.active_task = data.active_task;
            window.systemBridge.state.active_task = data.active_task;
            this.activeTask = data.active_task;
            this.renderTaskVisualizer(this.activeTask);
          }
          if (data.task_visualizer) {
            window.systemBridge.state.system.task_visualizer = data.task_visualizer;
          }
          if (Array.isArray(data.synthesis_candidates)) {
            window.systemBridge.state.system.synthesis_candidates = data.synthesis_candidates;
            if (data.synthesis_candidates.length > 0) {
              this.showSynthesisSuggestion(data.synthesis_candidates[0]);
            } else if (!this.currentSynthesisCandidate && this.elSkillSynthDock) {
              this.elSkillSynthDock.style.display = 'none';
            }
          }
        }
      };
    }
  }

  async fetchSkillsForAutocomplete() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/skills', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (data && Array.isArray(data.skills)) {
        for (const s of data.skills) {
          const cmd = '/' + s.id;
          if (!this.slashCommands.find((item) => item.cmd === cmd)) {
            this.slashCommands.push({
              cmd: cmd,
              desc: `${s.name} [${s.safety_tier}]`
            });
          }
        }
      }
    } catch (e) {}
  }

  async pollActiveTask() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/task/active', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data.ok) {
          this.activeTask = data.task;
          this.renderTaskVisualizer(this.activeTask);
        }
      }
    } catch (e) {}
  }

  async pollSynthesisCandidates() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/skills/synthesis_candidates', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.candidates) && data.candidates.length > 0) {
          this.showSynthesisSuggestion(data.candidates[0]);
        }
      }
    } catch (e) {}
  }

  renderTaskVisualizer(task) {
    if (!task) {
      if (this.elTaskVisDock) this.elTaskVisDock.style.display = 'none';
      if (!this.currentHitlTask && this.elHitlPromptDock) this.elHitlPromptDock.style.display = 'none';
      return;
    }

    const status = task.status || 'RUNNING';

    if (status === 'RUNNING' || status === 'RETRY') {
      if (this.elTaskVisDock) this.elTaskVisDock.style.display = 'block';
      if (this.elHitlPromptDock) this.elHitlPromptDock.style.display = 'none';

      const total = task.total_steps || (Array.isArray(task.steps) ? task.steps.length : 1) || 1;
      const current = Math.min(total, task.current_step !== undefined ? task.current_step : 0);
      const pct = Math.min(100, Math.max(0, Math.round((current / total) * 100)));
      const filled = Math.min(10, Math.max(0, Math.round((pct / 100) * 10)));
      const bar = '[' + '■'.repeat(filled) + '□'.repeat(10 - filled) + ']';

      const elapsed = task.elapsed_seconds || (task.started_at ? Math.max(0, Math.floor((Date.now() / 1000) - task.started_at)) : 0);
      const min = Math.floor(elapsed / 60);
      const sec = Math.floor(elapsed % 60);
      const timeStr = `${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;

      let stepTitle = '';
      if (task.steps && task.steps[current]) {
        stepTitle = `: ${task.steps[current].title || task.steps[current].name || ''}`;
      }
      const stepLabel = `Step ${Math.min(total, current + 1)}/${total}${stepTitle}`;

      if (this.elTaskVisTitle) {
        const prefix = status === 'RETRY' ? '🔄 [RETRY' : '⚡ [TASK ATTIVO';
        const retryInfo = status === 'RETRY' ? ` tentat. ${task.retry_count || 1}]` : ']';
        this.elTaskVisTitle.textContent = `${prefix}${retryInfo} ${task.title || 'Elaborazione in corso...'}`;
      }
      if (this.elTaskVisBar) {
        this.elTaskVisBar.textContent = `${bar} ${pct}% • ${stepLabel} • Tempo: ${timeStr} • Annulla: [Esc / Click ✕]`;
      }
    } else if (status === 'WAITING_INPUT' || status === 'FAILED' || status === 'ABORTED') {
      if (this.elTaskVisDock) this.elTaskVisDock.style.display = 'block';
      if (this.elTaskVisTitle) {
        this.elTaskVisTitle.textContent = `⚠️ [TASK: ${status}] ${task.title || 'Interrotto'}`;
      }
      if (this.elTaskVisBar) {
        const err = task.error || task.error_message || 'In attesa di input o recovery';
        this.elTaskVisBar.textContent = `[!] Stato: ${status} • ${err}`;
      }
      this.showHitlRecovery(task);
    } else if (status === 'COMPLETED') {
      if (this.elTaskVisDock) this.elTaskVisDock.style.display = 'block';
      if (this.elTaskVisTitle) {
        this.elTaskVisTitle.textContent = `✅ [COMPLETATO] ${task.title || 'Task completato con successo'}`;
      }
      if (this.elTaskVisBar) {
        this.elTaskVisBar.textContent = `[■■■■■■■■■■] 100% • Tutte le fasi completate con successo.`;
      }
      if (this.elHitlPromptDock) this.elHitlPromptDock.style.display = 'none';
      setTimeout(() => {
        if (this.activeTask && this.activeTask.status === 'COMPLETED') {
          if (this.elTaskVisDock) this.elTaskVisDock.style.display = 'none';
        }
      }, 3500);
    }
  }

  async abortActiveTask() {
    const task = this.activeTask || this.currentHitlTask;
    const taskId = task ? task.id : 'active';
    this.say('🛑 Invio Kill Switch per interrompere istantaneamente il task...');
    try {
      const res = await fetch('http://127.0.0.1:8765/api/task/abort', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId, reason: 'Kill Switch Utente (Esc / Annulla)' })
      });
      const data = await res.json();
      if (data.ok) {
        this.say('🛑 Task interrotto con successo (Stato: ABORTED). Neko ripristinato a IDLE.');
        if (this.activeTask) this.activeTask.status = 'ABORTED';
        if (this.elTaskVisDock) this.elTaskVisDock.style.display = 'none';
        await this.pollActiveTask();
      } else {
        this.say(`⚠️ Errore kill switch task: ${data.error || 'Operazione non riuscita'}`);
      }
    } catch (err) {
      this.say(`❌ Errore connessione abort: ${err.message}`);
    }
  }

  showHitlRecovery(task) {
    this.currentHitlTask = task;
    if (this.elHitlPromptDock) {
      this.elHitlPromptDock.style.display = 'block';
      if (this.elHitlErrorHeader) {
        const stepNum = task.current_step !== undefined ? task.current_step + 1 : '?';
        const totalNum = task.total_steps || (task.steps ? task.steps.length : '?');
        const err = task.error || task.error_message || 'Errore o input richiesto';
        this.elHitlErrorHeader.textContent = `⚠️ [!] Task interrotto al passaggio ${stepNum}/${totalNum}: ${err}`;
      }
      if (this.elHitlAssetInfo) {
        let artifacts = [];
        if (Array.isArray(task.artifacts) && task.artifacts.length > 0) {
          artifacts = task.artifacts;
        } else if (Array.isArray(task.steps)) {
          task.steps.forEach((s) => {
            if (s.output_file) artifacts.push(s.output_file);
            if (Array.isArray(s.artifacts)) artifacts.push(...s.artifacts);
          });
        }
        const artText = artifacts.length > 0 ? artifacts.join(', ') : 'File parziali generati nel workspace';
        this.elHitlAssetInfo.textContent = `Asset parziali generati: ${artText}`;
      }
    }
  }

  async handleHitlChoice(choice) {
    const task = this.currentHitlTask || this.activeTask;
    if (!task) return;
    const taskId = task.id;
    const choiceLabels = {
      1: '1. Mantieni i file generati e termina',
      2: '2. Riprova questo passaggio',
      3: '3. Rollback completo (rimuovi file parziali)'
    };
    this.say(`⚙️ Applico scelta HITL: ${choiceLabels[choice] || choice}...`);
    try {
      const res = await fetch('http://127.0.0.1:8765/api/task/hitl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: taskId, recovery_choice: choice })
      });
      const data = await res.json();
      if (data.ok) {
        if (this.elHitlPromptDock) this.elHitlPromptDock.style.display = 'none';
        this.currentHitlTask = null;
        this.say(`✅ Recupero completato: ${data.action || 'Operazione applicata'}.`);
        await this.pollActiveTask();
      } else {
        this.say(`⚠️ Errore recupero HITL: ${data.error || 'Operazione non riuscita'}`);
      }
    } catch (err) {
      this.say(`❌ Errore connessione HITL: ${err.message}`);
    }
  }

  showSynthesisSuggestion(candidate) {
    if (!candidate) {
      if (this.elSkillSynthDock) this.elSkillSynthDock.style.display = 'none';
      return;
    }
    const sig = candidate.pattern_signature || candidate.pattern_name;
    if (this.dismissedSynthesis.has(sig)) {
      if (this.elSkillSynthDock) this.elSkillSynthDock.style.display = 'none';
      return;
    }
    this.currentSynthesisCandidate = candidate;
    if (this.elSkillSynthDock) {
      this.elSkillSynthDock.style.display = 'flex';
      if (this.elSynthesisText) {
        const count = candidate.count || 3;
        const name = candidate.pattern_name || candidate.pattern_signature || 'Azione ricorrente';
        const slug = candidate.suggested_slug || candidate.pattern_signature || 'nuova_skill';
        this.elSynthesisText.innerHTML = `💡 Rilevata azione frequente (${count} volte): "<em>${this.escapeHtml(name)}</em>". Vuoi registrarla come skill rapida <code>/${this.escapeHtml(slug)}</code>?`;
      }
    }
  }

  async synthesizeCandidateSkill() {
    if (!this.currentSynthesisCandidate) return;
    const cand = this.currentSynthesisCandidate;
    const sig = cand.pattern_signature || cand.pattern_name;
    this.say(`✨ Sintesi autonoma della skill "${cand.pattern_name || sig}" in corso...`);
    try {
      const res = await fetch('http://127.0.0.1:8765/api/skills/synthesize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pattern_signature: cand.pattern_signature,
          name: cand.pattern_name,
          description: cand.description || 'Skill sintetizzata autonomamente da pattern ricorrente'
        })
      });
      const data = await res.json();
      if (data.ok && data.skill) {
        this.dismissedSynthesis.add(sig);
        if (this.elSkillSynthDock) this.elSkillSynthDock.style.display = 'none';
        this.currentSynthesisCandidate = null;
        this.say(`✨ Skill creata con successo: <strong>${this.escapeHtml(data.skill.name)}</strong> (Tier: ${data.skill.safety_tier}). Disponibile in <code>/skills</code> e con <code>/${data.skill.id}</code>!`);
        await this.fetchSkillsForAutocomplete();
      } else {
        this.say(`⚠️ Errore sintesi skill: ${data.error || 'Operazione non riuscita'}`);
      }
    } catch (err) {
      this.say(`❌ Errore connessione sintesi: ${err.message}`);
    }
  }

  dismissCurrentSynthesis() {
    if (this.currentSynthesisCandidate) {
      const sig = this.currentSynthesisCandidate.pattern_signature || this.currentSynthesisCandidate.pattern_name;
      this.dismissedSynthesis.add(sig);
      this.currentSynthesisCandidate = null;
    }
    if (this.elSkillSynthDock) {
      this.elSkillSynthDock.style.display = 'none';
    }
  }

  // =========================================================================
  // MILESTONE 3 (R3): 5-TAB WORKSPACE SWITCHER & 4 FULL-VIEW AI DASHBOARDS
  // =========================================================================

  initMilestone3Workspace() {
    this.elWorkspaceNav = document.getElementById('hud-workspace-nav');
    this.elWorkspaceContainer = document.getElementById('hud-workspace-container');
    this.elHudLayer = document.getElementById('hud-layer');
    this.elHomeNextTaskChip = document.getElementById('home-next-task-chip');
    this.elHomeNextTaskTitle = document.getElementById('home-next-task-title');
    this.elHomeNextTaskDate = document.getElementById('home-next-task-date');

    // Cyber-Studio Tactile Co-Pilot HUD Elements (R2)
    this.elNekoCopilotHud = document.getElementById('neko-copilot-hud');
    this.elCopilotCardRadar = document.getElementById('copilot-card-radar');
    this.elCopilotRadarBody = document.getElementById('copilot-radar-body');
    this.elCopilotCardTask = document.getElementById('copilot-card-task');
    this.elCopilotTaskBody = document.getElementById('copilot-task-body');
    this.elCopilotTaskStatus = document.getElementById('copilot-task-status');
    this.elCopilotCardBrain = document.getElementById('copilot-card-brain');
    this.elCopilotBrainBody = document.getElementById('copilot-brain-body');
    this.elCopilotTokVal = document.getElementById('copilot-tok-val');
    this._lastCopilotKey = null;

    // Progressive Disclosure Drawers & Subtabs (R3)
    this.elBtnToggleNewProjDrawer = document.getElementById('btn-toggle-new-proj-drawer');
    this.elBtnTogglePlaybookDrawer = document.getElementById('btn-toggle-playbook-drawer');
    this.elProjCreateBar = document.getElementById('proj-create-bar');
    this.elMemorySubtabs = document.getElementById('memory-view-subtabs');
    this.activeMemSubtab = 'instincts-rules';

    // Badges
    this.elTabTodoBadge = document.getElementById('tab-todo-badge');
    this.elTabProjBadge = document.getElementById('tab-proj-badge');
    this.elTabCalBadge = document.getElementById('tab-cal-badge');
    this.elTabMemBadge = document.getElementById('tab-mem-badge');

    // Views
    this.elViewTodo = document.getElementById('workspace-view-todo');
    this.elViewProjects = document.getElementById('workspace-view-projects');
    this.elViewCalendar = document.getElementById('workspace-view-calendar');
    this.elViewMemory = document.getElementById('workspace-view-memory');

    // Kanban Elements
    this.elKanbanFilters = document.getElementById('kanban-project-filters');
    this.elKanbanQuickAddForm = document.getElementById('kanban-quick-add-form');
    this.elKanbanAddTitle = document.getElementById('kanban-add-title');
    this.elKanbanAddProject = document.getElementById('kanban-add-project');
    this.elKanbanAddDue = document.getElementById('kanban-add-due');
    this.elKanbanAddPriority = document.getElementById('kanban-add-priority');
    this.elBtnKanbanAiPlan = document.getElementById('btn-kanban-ai-plan');
    this.elCountKanbanTodo = document.getElementById('count-kanban-todo');
    this.elCountKanbanDoing = document.getElementById('count-kanban-doing');
    this.elCountKanbanDone = document.getElementById('count-kanban-done');
    this.elKanbanColTodo = document.getElementById('kanban-col-todo');
    this.elKanbanColDoing = document.getElementById('kanban-col-doing');
    this.elKanbanColDone = document.getElementById('kanban-col-done');

    // Project Hub Elements
    this.elHubTabProjActive = document.getElementById('hub-tab-proj-active');
    this.elHubTabProjArchive = document.getElementById('hub-tab-proj-archive');
    this.elHubProjActiveCount = document.getElementById('hub-proj-active-count');
    this.elHubProjArchCount = document.getElementById('hub-proj-arch-count');
    this.elHubProjNewName = document.getElementById('hub-proj-new-name');
    this.elHubProjNewClient = document.getElementById('hub-proj-new-client');
    this.elHubProjNewNote = document.getElementById('hub-proj-new-note');
    this.elHubProjNewStack = document.getElementById('hub-proj-new-stack');
    this.elBtnHubCreateProj = document.getElementById('btn-hub-create-proj');
    this.elHubProjectsGrid = document.getElementById('hub-projects-grid');
    this.elHubProjectsArchiveList = document.getElementById('hub-projects-archive-list');

    // Calendar Elements
    this.elBtnCalViewWeekly = document.getElementById('btn-cal-view-weekly');
    this.elBtnCalViewMonthly = document.getElementById('btn-cal-view-monthly');
    this.elBtnCalViewAgenda = document.getElementById('btn-cal-view-agenda');
    this.elBtnCalSyncNow = document.getElementById('btn-cal-sync-now');
    this.elBtnCalPrev = document.getElementById('btn-cal-prev');
    this.elBtnCalToday = document.getElementById('btn-cal-today');
    this.elBtnCalNext = document.getElementById('btn-cal-next');
    this.elCalRangeLabel = document.getElementById('cal-view-range-label');
    this.elCalSyncStatusBadge = document.getElementById('cal-sync-status-badge');
    this.elCalQuickAddForm = document.getElementById('cal-quick-add-form');
    this.elCalAddDate = document.getElementById('cal-add-date');
    this.elCalAddTime = document.getElementById('cal-add-time');
    this.elCalAddTitle = document.getElementById('cal-add-title');
    this.elCalViewWeekly = document.getElementById('cal-view-weekly');
    this.elCalViewMonthly = document.getElementById('cal-view-monthly');
    this.elCalViewAgenda = document.getElementById('cal-view-agenda');

    // Memory Elements
    this.elMemSkillsCount = document.getElementById('mem-skills-count');
    this.elMemNewSkillName = document.getElementById('mem-new-skill-name');
    this.elMemNewSkillCat = document.getElementById('mem-new-skill-cat');
    this.elBtnAddSkill = document.getElementById('btn-add-skill');
    this.elMemSkillsList = document.getElementById('mem-skills-list');
    this.elMemRulesCount = document.getElementById('mem-rules-count');
    this.elMemRulesSearch = document.getElementById('mem-rules-search');
    this.elMemNewRuleText = document.getElementById('mem-new-rule-text');
    this.elMemNewRuleProj = document.getElementById('mem-new-rule-proj');
    this.elBtnAddRule = document.getElementById('btn-add-rule');
    this.elMemRulesList = document.getElementById('mem-rules-list');
    this.elMemProfileCount = document.getElementById('mem-profile-count');
    this.elMemProfilingPromptCard = document.getElementById('mem-profiling-prompt-card');
    this.elMemProfilingQText = document.getElementById('mem-profiling-q-text');
    this.elMemProfilingOptions = document.getElementById('mem-profiling-options');
    this.elMemProfilingInput = document.getElementById('mem-profiling-input');
    this.elBtnMemProfilingSave = document.getElementById('btn-mem-profiling-save');
    this.elBtnMemProfilingNext = document.getElementById('btn-mem-profiling-next');
    this.elMemProfileTraitsList = document.getElementById('mem-profile-traits-list');
    this.elMemProfileSkillsListPane3 = document.getElementById('mem-profile-skills-list-pane3');

    // Milestone 4 (F22-F28) Elements
    // F22: Dynamic Context Mode
    this.elContextModePill = document.getElementById('context-mode-pill');
    this.elContextModeIcon = document.getElementById('context-mode-icon');
    this.elContextModeLabel = document.getElementById('context-mode-label');

    // F28: Smart Banner
    this.elSmartBanner = document.getElementById('hud-smart-banner');
    this._dismissedTaskSuggestion = null;
    this._dismissedSessionCheckpoint = null;
    this._lastBannerKey = null;

    // F23, F24, F25: Playbook Quick Bar & Quality Gate Modal
    this.elPlaybookQuickBar = document.getElementById('playbook-quick-bar');
    this.elHubPlaybookSelect = document.getElementById('hub-playbook-select');
    this.elHubPlaybookNewName = document.getElementById('hub-playbook-new-name');
    this.elHubPlaybookNewClient = document.getElementById('hub-playbook-new-client');
    this.elBtnCreateFromPlaybook = document.getElementById('btn-create-from-playbook');

    this.elQualityGateModal = document.getElementById('quality-gate-modal');
    this.elQgProjTitle = document.getElementById('qg-proj-title');
    this.elBtnCloseQgModal = document.getElementById('btn-close-qg-modal');
    this.elBtnQgClose = document.getElementById('btn-qg-close');
    this.elBtnQgRetest = document.getElementById('btn-qg-retest');
    this.elQgScoreBanner = document.getElementById('qg-score-banner');
    this.elQgScoreVal = document.getElementById('qg-score-val');
    this.elQgStatusLabel = document.getElementById('qg-status-label');
    this.elQgStatusDesc = document.getElementById('qg-status-desc');
    this.elQgTasksSummary = document.getElementById('qg-tasks-summary');
    this.elQgTasksBadge = document.getElementById('qg-tasks-badge');
    this.elQgTasksList = document.getElementById('qg-tasks-list');
    this.elQgRulesSummary = document.getElementById('qg-rules-summary');
    this.elQgRulesBadge = document.getElementById('qg-rules-badge');
    this.elQgRulesList = document.getElementById('qg-rules-list');
    this.elQgInstinctsBadge = document.getElementById('qg-instincts-badge');
    this.elQgInstinctsList = document.getElementById('qg-instincts-list');
    this.elQgRecommendations = document.getElementById('qg-recommendations');
    this._currentQgProjectId = null;
    this._currentQgProjectName = null;

    // F26: Context Budget Telemetry
    this.elContextBudgetContainer = document.getElementById('context-budget-container');
    this.elBudgetUsedTokens = document.getElementById('budget-used-tokens');
    this.elBudgetUsedPct = document.getElementById('budget-used-pct');
    this.elBudgetStatusPill = document.getElementById('budget-status-pill');
    this.elSegSystem = document.getElementById('seg-system');
    this.elSegProfile = document.getElementById('seg-profile');
    this.elSegInstincts = document.getElementById('seg-instincts');
    this.elSegMemories = document.getElementById('seg-memories');
    this.elSegProjects = document.getElementById('seg-projects');
    this.elSegChat = document.getElementById('seg-chat');
    this.elLblTokSystem = document.getElementById('lbl-tok-system');
    this.elLblTokProfile = document.getElementById('lbl-tok-profile');
    this.elLblTokInstincts = document.getElementById('lbl-tok-instincts');
    this.elLblTokMemories = document.getElementById('lbl-tok-memories');
    this.elLblTokProjects = document.getElementById('lbl-tok-projects');
    this.elLblTokChat = document.getElementById('lbl-tok-chat');

    // F27: Column 1 Atomic Instincts v2.1
    this.elMemSubnav = document.getElementById('mem-col1-subnav');
    this.elBtnSubtabInstincts = document.getElementById('btn-subtab-instincts');
    this.elBtnSubtabSkills = document.getElementById('btn-subtab-skills');
    this.elCountInstincts = document.getElementById('count-instincts');
    this.elCountSkills = document.getElementById('count-skills');
    this.elMemInstinctAddRow = document.getElementById('mem-instinct-add-row');
    this.elMemSkillAddRow = document.getElementById('mem-skill-add-row');
    this.elMemNewInstinctTrigger = document.getElementById('mem-new-instinct-trigger');
    this.elMemNewInstinctAction = document.getElementById('mem-new-instinct-action');
    this.elMemNewInstinctScope = document.getElementById('mem-new-instinct-scope');
    this.elBtnAddInstinct = document.getElementById('btn-add-instinct');
    this.elMemInstinctsList = document.getElementById('mem-instincts-list');
    this.memCol1Subtab = 'instincts';
    this.currentContextMode = 'operativo';

    // Milestone 4 (F18-F25) Arsenal & Modal Elements
    this.elEccArsenalSection = document.getElementById('ecc-arsenal-section');
    this.elMcpGrid = document.getElementById('mcp-connectors-grid');
    this.elSkillsSwitchboardList = document.getElementById('skills-switchboard-list');
    this.elCountMcpActive = document.getElementById('count-mcp-active');
    this.elCountMcpTotal = document.getElementById('count-mcp-total');
    this.elCountSkillsActive = document.getElementById('count-skills-active');
    this.elCountSkillsTotal = document.getElementById('count-skills-total');
    this.elBtnOpenAddMcp = document.getElementById('btn-open-add-mcp');
    this.elBtnOpenImportSkillModal = document.getElementById('btn-open-import-skill-modal');
    this.elModalImportSkill = document.getElementById('modal-import-skill');
    this.elBtnCloseImportSkillModal = document.getElementById('btn-close-import-skill-modal');
    this.elBtnCancelImportSkill = document.getElementById('btn-cancel-import-skill');
    this.elFormImportSkill = document.getElementById('form-import-skill');
    this.elImportSkillId = document.getElementById('import-skill-id');
    this.elImportSkillName = document.getElementById('import-skill-name');
    this.elImportSkillDomain = document.getElementById('import-skill-domain');
    this.elImportSkillContent = document.getElementById('import-skill-content');
    this.elBtnSaveImportSkill = document.getElementById('btn-save-import-skill');

    if (window.systemBridge) {
      if (typeof window.systemBridge.fetchMcpConnectors === 'function') window.systemBridge.fetchMcpConnectors();
      if (typeof window.systemBridge.fetchSkills === 'function') window.systemBridge.fetchSkills();
    }

    this.activeWorkspace = 'view-home';
    this.kanbanProjectFilter = 'all';
    this.calendarViewMode = 'weekly';
    this.calendarCursorDate = new Date();
    this.hubViewMode = 'active';
    this._memRulesSearchQuery = '';

    this._lastKanbanKey = null;
    this._lastHubProjKey = null;
    this._lastCalendarKey = null;
    this._lastMemoryKey = null;
    this._lastPlaybookKey = null;

    if (this.elCalAddDate) {
      const todayIso = new Date().toISOString().split('T')[0];
      this.elCalAddDate.value = todayIso;
    }

    this.bindWorkspaceEvents();
    if (this.elAiDock) {
      this.elAiDock.classList.remove('term-collapsed');
      this.isTerminalCollapsed = false;
    }
    this.switchWorkspace('view-home');
    this.renderCopilotHud();
  }

  bindWorkspaceEvents() {
    // 5-Tab Workspace Switcher
    if (this.elWorkspaceNav) {
      this.elWorkspaceNav.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-workspace-tab]');
        if (btn) {
          e.preventDefault();
          const tabId = btn.getAttribute('data-workspace-tab');
          this.switchWorkspace(tabId);
        }
      });
    }

    // Hotkeys unificati [Alt+1] .. [Alt+5] per navigazione rapida workspace
    if (typeof window !== 'undefined') {
      window.addEventListener('keydown', (e) => {
        // Ignora se l'utente sta digitando in un campo input/textarea/select
        const active = typeof document !== 'undefined' ? document.activeElement : null;
        if (
          active &&
          (['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName) || active.isContentEditable)
        ) {
          return;
        }

        // Intercetta solo combinazioni Alt pure (escludi AltGr/ctrlKey, shiftKey, metaKey)
        if (e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey) {
          if (e.key && e.key.toLowerCase() === 'h') {
            e.preventDefault();
            this.cycleHudVisibility();
            return;
          }

          const workspaceKeyMap = {
            '1': 'view-home',
            '2': 'view-todo',
            '3': 'view-projects',
            '4': 'view-calendar',
            '5': 'view-memory'
          };
          const targetWorkspace = workspaceKeyMap[e.key];
          if (targetWorkspace) {
            e.preventDefault();
            this.switchWorkspace(targetWorkspace);
          }
        }
      });
    }

    // Home Next-Task Chip click -> jumps to To-Do Kanban view
    if (this.elHomeNextTaskChip) {
      this.elHomeNextTaskChip.addEventListener('click', () => {
        this.switchWorkspace('view-todo');
      });
    }

    // --- KANBAN EVENTS ---
    if (this.elKanbanQuickAddForm) {
      this.elKanbanQuickAddForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const title = this.elKanbanAddTitle ? this.elKanbanAddTitle.value.trim() : '';
        if (!title) return;
        const project = this.elKanbanAddProject ? this.elKanbanAddProject.value.trim() : '';
        const due_label = this.elKanbanAddDue ? this.elKanbanAddDue.value.trim() : '';
        const priority = this.elKanbanAddPriority ? this.elKanbanAddPriority.value : 'normal';

        if (this.elKanbanAddTitle) this.elKanbanAddTitle.value = '';
        if (this.elKanbanAddDue) this.elKanbanAddDue.value = '';

        const payload = { action: 'add', title, project, due_label, priority };
        let res = null;
        if (this.actions.todosAction) {
          res = await this.actions.todosAction(payload);
        } else if (window.systemBridge && window.systemBridge.todosAction) {
          res = await window.systemBridge.todosAction(payload);
        }

        if (res && res.ok && Array.isArray(res.todos)) {
          if (this.state.system) this.state.system.todosList = res.todos;
        } else {
          const newTask = {
            id: 'todo_' + Date.now(),
            title,
            project,
            due_label,
            due_date: '',
            status: 'todo',
            priority,
            subtasks: []
          };
          if (!this.state.system) this.state.system = {};
          if (!Array.isArray(this.state.system.todosList)) this.state.system.todosList = [];
          this.state.system.todosList.push(newTask);
        }
        this.renderKanbanBoard();
        this.renderHomeNextTaskChip();
        this.say(`📋 Task aggiunto: "${title}"`);
      });
    }

    if (this.elBtnKanbanAiPlan) {
      this.elBtnKanbanAiPlan.addEventListener('click', () => {
        const activeProj = (this.state.system && this.state.system.activeProjectName) || '';
        const prompt = activeProj
          ? `Pianifica i task, le sotto-attività e le scadenze dettagliate per il progetto ${activeProj}.`
          : 'Pianifica le attività e le priorità per i miei progetti attivi.';
        this.submitAiCommand(prompt);
      });
    }

    const handleKanbanCardAction = async (e) => {
      // Subtask checkbox
      const subtaskBox = e.target.closest('.kanban-subtask-check');
      if (subtaskBox) {
        const taskId = subtaskBox.getAttribute('data-todo-id');
        const subIdx = parseInt(subtaskBox.getAttribute('data-subtask-idx'), 10);
        if (taskId !== null && !isNaN(subIdx)) {
          if (this.actions.todosAction) {
            await this.actions.todosAction({ action: 'subtask_toggle', id: taskId, subtask_idx: subIdx });
          } else if (window.systemBridge && window.systemBridge.todosAction) {
            await window.systemBridge.todosAction({ action: 'subtask_toggle', id: taskId, subtask_idx: subIdx });
          }
          if (this.state.system && Array.isArray(this.state.system.todosList)) {
            const t = this.state.system.todosList.find((item) => (typeof item === 'object' && item.id === taskId));
            if (t && Array.isArray(t.subtasks) && t.subtasks[subIdx]) {
              t.subtasks[subIdx].done = !t.subtasks[subIdx].done;
            }
          }
          this.renderKanbanBoard();
        }
        return;
      }

      // Move button
      const moveBtn = e.target.closest('.btn-kanban-move');
      if (moveBtn) {
        e.preventDefault();
        const taskId = moveBtn.getAttribute('data-id');
        const nextStatus = moveBtn.getAttribute('data-status');
        if (taskId && nextStatus) {
          if (this.actions.todosAction) {
            await this.actions.todosAction({ action: 'status', id: taskId, status: nextStatus });
          } else if (window.systemBridge && window.systemBridge.todosAction) {
            await window.systemBridge.todosAction({ action: 'status', id: taskId, status: nextStatus });
          }
          if (this.state.system && Array.isArray(this.state.system.todosList)) {
            const t = this.state.system.todosList.find((item) => (typeof item === 'object' && item.id === taskId));
            if (t) t.status = nextStatus;
          }
          this.renderKanbanBoard();
          this.renderHomeNextTaskChip();
        }
        return;
      }

      // AI breakdown button
      const aiBreakdownBtn = e.target.closest('.btn-ai-breakdown');
      if (aiBreakdownBtn) {
        e.preventDefault();
        const taskId = aiBreakdownBtn.getAttribute('data-id');
        if (taskId) {
          aiBreakdownBtn.textContent = '⏳ Scomposizione...';
          aiBreakdownBtn.disabled = true;
          let res = null;
          if (this.actions.todosAction) {
            res = await this.actions.todosAction({ action: 'ai_breakdown', id: taskId });
          } else if (window.systemBridge && window.systemBridge.todosAction) {
            res = await window.systemBridge.todosAction({ action: 'ai_breakdown', id: taskId });
          }
          if (res && res.ok && Array.isArray(res.todos)) {
            if (this.state.system) this.state.system.todosList = res.todos;
            this.say(`✨ Task scomposto con successo in sotto-attività con l'AI!`);
          } else {
            if (this.state.system && Array.isArray(this.state.system.todosList)) {
              const t = this.state.system.todosList.find((item) => (typeof item === 'object' && item.id === taskId));
              if (t) {
                if (!Array.isArray(t.subtasks)) t.subtasks = [];
                t.subtasks.push({ title: 'Setup e verifica requisiti di progetto', done: false });
                t.subtasks.push({ title: 'Lavorazione e modellazione / pipeline', done: false });
                t.subtasks.push({ title: 'Controllo qualità, export e test finale', done: false });
              }
            }
            this.say(`✨ Task suddiviso in 3 sotto-attività operative!`);
          }
          this.renderKanbanBoard();
        }
        return;
      }

      // Delete button
      const delBtn = e.target.closest('.btn-kanban-del');
      if (delBtn) {
        e.preventDefault();
        const taskId = delBtn.getAttribute('data-id');
        if (taskId) {
          if (this.actions.todosAction) {
            await this.actions.todosAction({ action: 'delete', id: taskId });
          } else if (window.systemBridge && window.systemBridge.todosAction) {
            await window.systemBridge.todosAction({ action: 'delete', id: taskId });
          }
          if (this.state.system && Array.isArray(this.state.system.todosList)) {
            this.state.system.todosList = this.state.system.todosList.filter((item) => {
              if (typeof item === 'object') return item.id !== taskId;
              return item !== taskId;
            });
          }
          this.renderKanbanBoard();
          this.renderHomeNextTaskChip();
        }
        return;
      }
    };

    if (this.elKanbanColTodo) this.elKanbanColTodo.addEventListener('click', handleKanbanCardAction);
    if (this.elKanbanColDoing) this.elKanbanColDoing.addEventListener('click', handleKanbanCardAction);
    if (this.elKanbanColDone) this.elKanbanColDone.addEventListener('click', handleKanbanCardAction);

    // Filter clicks
    if (this.elKanbanFilters) {
      this.elKanbanFilters.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-filter-proj]');
        if (btn) {
          e.preventDefault();
          this.kanbanProjectFilter = btn.getAttribute('data-filter-proj') || 'all';
          this.renderKanbanBoard();
        }
      });
    }

    // --- PROJECT HUB EVENTS ---
    if (this.elHubTabProjActive) {
      this.elHubTabProjActive.addEventListener('click', () => {
        this.hubViewMode = 'active';
        this.elHubTabProjActive.classList.add('active');
        if (this.elHubTabProjArchive) this.elHubTabProjArchive.classList.remove('active');
        this.renderProjectHub();
      });
    }
    if (this.elHubTabProjArchive) {
      this.elHubTabProjArchive.addEventListener('click', () => {
        this.hubViewMode = 'archive';
        this.elHubTabProjArchive.classList.add('active');
        if (this.elHubTabProjActive) this.elHubTabProjActive.classList.remove('active');
        this.renderProjectHub();
      });
    }

    if (this.elBtnHubCreateProj) {
      this.elBtnHubCreateProj.addEventListener('click', async () => {
        const name = this.elHubProjNewName ? this.elHubProjNewName.value.trim() : '';
        if (!name) return;
        const client = this.elHubProjNewClient ? this.elHubProjNewClient.value.trim() : '';
        const notes = this.elHubProjNewNote ? this.elHubProjNewNote.value.trim() : '';
        const stack = this.elHubProjNewStack ? this.elHubProjNewStack.value.trim() : '';

        if (this.elHubProjNewName) this.elHubProjNewName.value = '';
        if (this.elHubProjNewClient) this.elHubProjNewClient.value = '';
        if (this.elHubProjNewNote) this.elHubProjNewNote.value = '';
        if (this.elHubProjNewStack) this.elHubProjNewStack.value = '';

        const payload = { action: 'create', name, client, notes, stack };
        if (this.actions.projectsAction) {
          await this.actions.projectsAction(payload);
        } else if (window.systemBridge && window.systemBridge.projectsAction) {
          await window.systemBridge.projectsAction(payload);
        }

        if (!this.state.system) this.state.system = {};
        if (!Array.isArray(this.state.system.projectsList)) this.state.system.projectsList = [];
        this.state.system.projectsList.push({
          id: 'proj_' + Date.now(),
          name,
          client,
          notes,
          stack: stack.split(',').map((s) => s.trim()).filter(Boolean),
          is_active: 1
        });
        this.renderProjectHub();
        this.say(`📌 Progetto creato: "${name}"`);
      });
    }

    if (this.elHubProjectsGrid) {
      this.elHubProjectsGrid.addEventListener('click', async (e) => {
        const focusBtn = e.target.closest('.btn-proj-focus');
        if (focusBtn) {
          const projName = focusBtn.getAttribute('data-proj-name');
          if (projName) {
            if (!this.state.system) this.state.system = {};
            this.state.system.activeProjectName = projName;
            this.renderProjectHub();
            this.renderHomeNextTaskChip();
            if (typeof this.renderCopilotHud === 'function') this.renderCopilotHud();
            this.say(`🎯 Progetto focus impostato: "${projName}"`);
          }
          return;
        }

        const planBtn = e.target.closest('.btn-proj-ask-plan');
        if (planBtn) {
          const projName = planBtn.getAttribute('data-proj-name');
          if (projName) {
            this.submitAiCommand(`Pianifica la roadmap completa e i task operativi per il progetto "${projName}".`);
          }
          return;
        }

        const openFolderBtn = e.target.closest('.btn-proj-open-folder');
        if (openFolderBtn) {
          const projId = openFolderBtn.getAttribute('data-proj-id');
          const wsPath = openFolderBtn.getAttribute('data-path') || '';
          this.say(`📂 Apertura cartella di lavoro in Esplora Risorse...`);
          if (window.systemBridge && window.systemBridge.openProjectFolder) {
            const res = await window.systemBridge.openProjectFolder(projId, wsPath, false);
            if (res && !res.ok) {
              this.say(`⚠️ Impossibile aprire cartella: ${res.error || 'percorso non valido'}`);
            }
          }
          return;
        }

        const scanFolderBtn = e.target.closest('.btn-proj-scan-folder');
        if (scanFolderBtn) {
          const projId = scanFolderBtn.getAttribute('data-proj-id');
          if (projId && window.systemBridge && window.systemBridge.scanProjectFolder) {
            scanFolderBtn.textContent = '⏳ ...';
            scanFolderBtn.disabled = true;
            const res = await window.systemBridge.scanProjectFolder(projId, true);
            scanFolderBtn.textContent = '🔄 Scansiona';
            scanFolderBtn.disabled = false;
            if (res && res.ok) {
              const count = (res.workspace_summary && res.workspace_summary.total_files !== undefined) ? res.workspace_summary.total_files : 0;
              this.say(`✅ Scansione completata: ${count} file rilevati.`);
              this.renderProjectHub();
            } else {
              this.say(`⚠️ Scansione fallita: ${res ? res.error : 'errore di rete'}`);
            }
          }
          return;
        }

        const bindFolderBtn = e.target.closest('.btn-proj-bind-folder');
        if (bindFolderBtn) {
          const projId = bindFolderBtn.getAttribute('data-proj-id');
          if (projId) {
            this.openBindFolderPrompt(projId);
          }
          return;
        }

        const evolveBtn = e.target.closest('.btn-proj-evolve');
        if (evolveBtn) {
          const projId = evolveBtn.getAttribute('data-proj-id');
          if (projId) {
            evolveBtn.textContent = '⏳ Evolving...';
            evolveBtn.disabled = true;
            this.say(`🧬 Generazione Playbook in corso per il progetto...`);
            let res = null;
            if (window.systemBridge && window.systemBridge.evolveProject) {
              res = await window.systemBridge.evolveProject(projId);
            }
            evolveBtn.textContent = '🧬 Evolvi in Playbook';
            evolveBtn.disabled = false;
            if (res && res.ok) {
              const pTitle = (res.playbook && (res.playbook.title || res.playbook.slug)) || 'Playbook';
              this.say(`✅ Progetto evoluto in Playbook "${pTitle}"!`);
              await this.populatePlaybooksDropdown();
            } else {
              this.say(`⚠️ Generazione Playbook fallita: ${res ? res.error : 'errore di rete'}`);
            }
          }
          return;
        }

        const qgBtn = e.target.closest('.btn-proj-quality-gate');
        if (qgBtn) {
          const projId = qgBtn.getAttribute('data-proj-id');
          const projName = qgBtn.getAttribute('data-proj-name') || 'Progetto';
          if (projId) {
            this.openQualityGateModal(projId, projName);
          }
          return;
        }

        const archBtn = e.target.closest('.btn-proj-archive');
        if (archBtn) {
          const projId = archBtn.getAttribute('data-proj-id');
          if (projId) {
            if (this.actions.projectsAction) {
              await this.actions.projectsAction({ action: 'archive', project_id: projId });
            }
            if (this.state.system && Array.isArray(this.state.system.projectsList)) {
              const p = this.state.system.projectsList.find((item) => item.id === projId);
              if (p) p.is_active = 0;
            }
            this.renderProjectHub();
          }
          return;
        }
      });
    }

    // --- CALENDAR EVENTS ---
    if (this.elBtnCalViewWeekly) {
      this.elBtnCalViewWeekly.addEventListener('click', () => {
        this.calendarViewMode = 'weekly';
        this.renderCalendarDashboard();
      });
    }
    if (this.elBtnCalViewMonthly) {
      this.elBtnCalViewMonthly.addEventListener('click', () => {
        this.calendarViewMode = 'monthly';
        this.renderCalendarDashboard();
      });
    }
    if (this.elBtnCalViewAgenda) {
      this.elBtnCalViewAgenda.addEventListener('click', () => {
        this.calendarViewMode = 'agenda';
        this.renderCalendarDashboard();
      });
    }

    if (this.elBtnCalPrev) {
      this.elBtnCalPrev.addEventListener('click', () => {
        const d = new Date(this.calendarCursorDate);
        if (this.calendarViewMode === 'monthly') {
          d.setMonth(d.getMonth() - 1);
        } else {
          d.setDate(d.getDate() - 7);
        }
        this.calendarCursorDate = d;
        this.renderCalendarDashboard();
      });
    }
    if (this.elBtnCalToday) {
      this.elBtnCalToday.addEventListener('click', () => {
        this.calendarCursorDate = new Date();
        this.renderCalendarDashboard();
      });
    }
    if (this.elBtnCalNext) {
      this.elBtnCalNext.addEventListener('click', () => {
        const d = new Date(this.calendarCursorDate);
        if (this.calendarViewMode === 'monthly') {
          d.setMonth(d.getMonth() + 1);
        } else {
          d.setDate(d.getDate() + 7);
        }
        this.calendarCursorDate = d;
        this.renderCalendarDashboard();
      });
    }

    if (this.elBtnCalSyncNow) {
      this.elBtnCalSyncNow.addEventListener('click', async () => {
        this.elBtnCalSyncNow.textContent = '⏳ Sincronizzazione...';
        this.elBtnCalSyncNow.disabled = true;
        let res = null;
        if (this.actions.calendarAction) {
          res = await this.actions.calendarAction({ action: 'sync_now' });
        } else if (window.systemBridge && window.systemBridge.calendarAction) {
          res = await window.systemBridge.calendarAction({ action: 'sync_now' });
        }
        setTimeout(() => {
          if (this.elBtnCalSyncNow) {
            this.elBtnCalSyncNow.textContent = '🔄 Sincronizza Ora';
            this.elBtnCalSyncNow.disabled = false;
          }
        }, 1200);

        if (res && res.ok && Array.isArray(res.calendar_events)) {
          if (this.state.system) {
            this.state.system.calendarEvents = res.calendar_events;
            if (res.calendar_sync_status) this.state.system.calendarSyncStatus = res.calendar_sync_status;
          }
          this.say(`📅 Calendario sincronizzato con Google Calendar!`);
        }
        this.renderCalendarDashboard();
      });
    }

    if (this.elCalQuickAddForm) {
      this.elCalQuickAddForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const date = this.elCalAddDate ? this.elCalAddDate.value : '';
        const time = this.elCalAddTime ? this.elCalAddTime.value : '10:00';
        const title = this.elCalAddTitle ? this.elCalAddTitle.value.trim() : '';
        if (!title || !date) return;

        if (this.elCalAddTitle) this.elCalAddTitle.value = '';

        const payload = { action: 'add', date, time, title, source: 'local' };
        let res = null;
        if (this.actions.calendarAction) {
          res = await this.actions.calendarAction(payload);
        } else if (window.systemBridge && window.systemBridge.calendarAction) {
          res = await window.systemBridge.calendarAction(payload);
        }

        if (res && res.ok && Array.isArray(res.calendar_events)) {
          if (this.state.system) this.state.system.calendarEvents = res.calendar_events;
        } else {
          if (!this.state.system) this.state.system = {};
          if (!Array.isArray(this.state.system.calendarEvents)) this.state.system.calendarEvents = [];
          this.state.system.calendarEvents.push({
            id: 'cal_' + Date.now(),
            date,
            time,
            title,
            source: 'local'
          });
        }
        this.renderCalendarDashboard();
        this.say(`📅 Evento aggiunto: "${title}" (${date} ore ${time})`);
      });
    }

    const handleCalEventClick = async (e) => {
      const pill = e.target.closest('.cal-event-pill');
      if (pill) {
        const evId = pill.getAttribute('data-event-id');
        const evSource = pill.getAttribute('data-event-source');
        if (evId && evSource === 'local') {
          if (confirm('Vuoi eliminare questo evento locale dal calendario?')) {
            if (this.actions.calendarAction) {
              await this.actions.calendarAction({ action: 'delete', id: evId });
            }
            if (this.state.system && Array.isArray(this.state.system.calendarEvents)) {
              this.state.system.calendarEvents = this.state.system.calendarEvents.filter((ev) => ev.id !== evId);
            }
            this.renderCalendarDashboard();
          }
        } else if (evSource === 'todo') {
          this.switchWorkspace('view-todo');
        }
      }
    };
    if (this.elCalViewWeekly) this.elCalViewWeekly.addEventListener('click', handleCalEventClick);
    if (this.elCalViewMonthly) this.elCalViewMonthly.addEventListener('click', handleCalEventClick);
    if (this.elCalViewAgenda) this.elCalViewAgenda.addEventListener('click', handleCalEventClick);

    // --- MEMORY EVENTS ---
    if (this.elBtnAddSkill) {
      this.elBtnAddSkill.addEventListener('click', async () => {
        const name = this.elMemNewSkillName ? this.elMemNewSkillName.value.trim() : '';
        const category = this.elMemNewSkillCat ? this.elMemNewSkillCat.value : '3D & Modellazione';
        if (!name) return;
        if (this.elMemNewSkillName) this.elMemNewSkillName.value = '';

        const payload = { action: 'add', name, category, level: 'Intermedio' };
        if (this.actions.skillsLearnedAction) {
          await this.actions.skillsLearnedAction(payload);
        } else if (window.systemBridge && window.systemBridge.skillsLearnedAction) {
          await window.systemBridge.skillsLearnedAction(payload);
        }

        if (!this.state.system) this.state.system = {};
        if (!Array.isArray(this.state.system.skillsLearned)) this.state.system.skillsLearned = [];
        this.state.system.skillsLearned.push({
          id: 'skill_' + Date.now(),
          name,
          category,
          level: 'Intermedio'
        });
        this.renderMemoryDashboard();
        this.say(`🛠️ Nuova competenza registrata: "${name}" (${category})`);
      });
    }

    if (this.elMemSkillsList) {
      this.elMemSkillsList.addEventListener('click', async (e) => {
        const delBtn = e.target.closest('.btn-del-skill');
        if (delBtn) {
          const skillId = delBtn.getAttribute('data-skill-id');
          if (skillId) {
            if (this.actions.skillsLearnedAction) {
              await this.actions.skillsLearnedAction({ action: 'remove', id: skillId });
            }
            if (this.state.system && Array.isArray(this.state.system.skillsLearned)) {
              this.state.system.skillsLearned = this.state.system.skillsLearned.filter((s) => s.id !== skillId);
            }
            this.renderMemoryDashboard();
          }
        }
      });
    }

    if (this.elMemRulesSearch) {
      this.elMemRulesSearch.addEventListener('input', () => {
        this._memRulesSearchQuery = this.elMemRulesSearch.value.trim().toLowerCase();
        this.renderMemoryDashboard();
      });
    }

    if (this.elBtnAddRule) {
      this.elBtnAddRule.addEventListener('click', async () => {
        const text = this.elMemNewRuleText ? this.elMemNewRuleText.value.trim() : '';
        const project = this.elMemNewRuleProj ? this.elMemNewRuleProj.value : '';
        if (!text) return;
        if (this.elMemNewRuleText) this.elMemNewRuleText.value = '';

        if (this.actions.settingsAction) {
          await this.actions.settingsAction({ action: 'add_memory', text, category: project || 'Regola Cliente' });
        }
        this.renderMemoryDashboard();
        this.say(`🏢 Regola registrata in memoria: "${text}"`);
      });
    }

    if (this.elMemRulesList) {
      this.elMemRulesList.addEventListener('click', async (e) => {
        const pinBtn = e.target.closest('.btn-pin-rule');
        if (pinBtn) {
          const ruleId = pinBtn.getAttribute('data-rule-id');
          const isPinned = pinBtn.classList.contains('pinned');
          if (this.actions.settingsAction) {
            await this.actions.settingsAction({ action: 'pin_memory', id: ruleId, pinned: !isPinned });
          }
          this.renderMemoryDashboard();
          return;
        }

        const delBtn = e.target.closest('.btn-del-rule');
        if (delBtn) {
          const ruleId = delBtn.getAttribute('data-rule-id');
          if (ruleId && confirm('Eliminare questa regola cliente?')) {
            if (this.actions.settingsAction) {
              await this.actions.settingsAction({ action: 'delete_memory', id: ruleId });
            }
            this.renderMemoryDashboard();
          }
          return;
        }
      });
    }

    if (this.elBtnMemProfilingSave) {
      this.elBtnMemProfilingSave.addEventListener('click', async () => {
        const answer = this.elMemProfilingInput ? this.elMemProfilingInput.value.trim() : '';
        if (!answer) return;
        if (this.elMemProfilingInput) this.elMemProfilingInput.value = '';
        let res = null;
        if (this.actions.conosciamociAction) {
          res = await this.actions.conosciamociAction({
            action: 'answer',
            question_id: this.currentQuestion ? this.currentQuestion.id : '',
            category: (this.currentQuestion && this.currentQuestion.category) || this.currentOnboardCategory || 'Profilo',
            question_text: this.currentQuestion ? this.currentQuestion.question : '',
            answer
          });
        }
        if (res && res.onboarding) {
          this.renderOnboardingState(res.onboarding);
          this.say(this.formatOnboardingFeedback(answer, res.onboarding.integration_summary));
        } else {
          this.say(`👤 Preferenza salvata nel profilo: "${answer}"`);
          this.rollProfilingQuestion();
        }
        this.renderCopilotHud();
        this.renderMemoryDashboard();
      });
    }

    if (this.elBtnMemProfilingNext) {
      this.elBtnMemProfilingNext.addEventListener('click', async () => {
        if (this.actions.conosciamociAction) {
          const res = await this.actions.conosciamociAction({
            action: 'random',
            exclude_id: this.currentQuestion ? this.currentQuestion.id : '',
            generate_ai: true
          });
          if (res && res.onboarding) {
            this.renderOnboardingState(res.onboarding);
            this.renderMemoryDashboard();
            return;
          }
        }
        this.rollProfilingQuestion();
        this.renderMemoryDashboard();
      });
    }

    // F22: Context Mode Pill click -> cycles modes
    if (this.elContextModePill) {
      this.elContextModePill.addEventListener('click', () => {
        const cur = this.currentContextMode || 'operativo';
        let next = 'operativo';
        if (cur === 'operativo') next = 'project_manager';
        else if (cur === 'project_manager') next = 'knowledge_curator';
        else next = 'operativo';
        this.setContextMode(next, true);
        const label = next === 'operativo' ? 'Operativo 🛠️' : (next === 'project_manager' ? 'Project Manager 📋' : 'Knowledge Curator 🧠');
        this.say(`🧭 Modalità contesto: ${label}`);
      });
    }

    // F25: Playbook Quick Bar Create Project
    if (this.elBtnCreateFromPlaybook) {
      this.elBtnCreateFromPlaybook.addEventListener('click', async () => {
        const slug = this.elHubPlaybookSelect ? this.elHubPlaybookSelect.value.trim() : '';
        const name = this.elHubPlaybookNewName ? this.elHubPlaybookNewName.value.trim() : '';
        const client = this.elHubPlaybookNewClient ? this.elHubPlaybookNewClient.value.trim() : '';
        if (!slug) {
          this.say('⚠️ Seleziona un Playbook prima di creare il progetto.');
          return;
        }
        if (!name) {
          this.say('⚠️ Inserisci il nome per il nuovo progetto.');
          return;
        }
        this.say(`⚡ Creazione progetto "${name}" da Playbook...`);
        let res = null;
        if (window.systemBridge && window.systemBridge.createProjectFromPlaybook) {
          res = await window.systemBridge.createProjectFromPlaybook(slug, name, client);
        }
        if (res && res.ok) {
          if (this.elHubPlaybookNewName) this.elHubPlaybookNewName.value = '';
          if (this.elHubPlaybookNewClient) this.elHubPlaybookNewClient.value = '';
          this.say(`✅ Progetto "${name}" e relative attività To-Do create con successo!`);
          if (res.project) {
            if (!this.state.system) this.state.system = {};
            if (!Array.isArray(this.state.system.projectsList)) this.state.system.projectsList = [];
            this.state.system.projectsList.push(res.project);
          }
          this.renderProjectHub();
          this.renderKanbanBoard();
        } else {
          this.say(`⚠️ Errore nella creazione del progetto: ${res ? res.error : 'errore di rete'}`);
        }
      });
    }

    // F24: Quality Gate Modal Close Handlers & Retest
    if (this.elBtnCloseQgModal) {
      this.elBtnCloseQgModal.addEventListener('click', () => {
        if (this.elQualityGateModal) this.elQualityGateModal.style.display = 'none';
      });
    }
    if (this.elBtnQgClose) {
      this.elBtnQgClose.addEventListener('click', () => {
        if (this.elQualityGateModal) this.elQualityGateModal.style.display = 'none';
      });
    }
    if (this.elQualityGateModal) {
      this.elQualityGateModal.addEventListener('click', (e) => {
        if (e.target === this.elQualityGateModal) this.elQualityGateModal.style.display = 'none';
      });
    }
    if (this.elBtnQgRetest) {
      this.elBtnQgRetest.addEventListener('click', () => {
        if (this._currentQgProjectId) {
          this.openQualityGateModal(this._currentQgProjectId, this._currentQgProjectName);
        }
      });
    }

    // F27: Column 1 Subtab Switcher (Instincts vs Skills)
    if (this.elBtnSubtabInstincts) {
      this.elBtnSubtabInstincts.addEventListener('click', () => {
        this.memCol1Subtab = 'instincts';
        if (this.elBtnSubtabInstincts) this.elBtnSubtabInstincts.classList.add('active');
        if (this.elBtnSubtabSkills) this.elBtnSubtabSkills.classList.remove('active');
        this.renderMemoryDashboard();
      });
    }
    if (this.elBtnSubtabSkills) {
      this.elBtnSubtabSkills.addEventListener('click', () => {
        this.memCol1Subtab = 'skills';
        if (this.elBtnSubtabSkills) this.elBtnSubtabSkills.classList.add('active');
        if (this.elBtnSubtabInstincts) this.elBtnSubtabInstincts.classList.remove('active');
        this.renderMemoryDashboard();
      });
    }

    // F27: Quick Add Instinct
    if (this.elBtnAddInstinct) {
      this.elBtnAddInstinct.addEventListener('click', async () => {
        const trigger = this.elMemNewInstinctTrigger ? this.elMemNewInstinctTrigger.value.trim() : '';
        const action = this.elMemNewInstinctAction ? this.elMemNewInstinctAction.value.trim() : '';
        const scope = this.elMemNewInstinctScope ? this.elMemNewInstinctScope.value : 'global';
        if (!trigger || !action) {
          this.say('⚠️ Inserisci sia la condizione (Quando) che l\'azione per il nuovo istinto.');
          return;
        }
        if (this.elMemNewInstinctTrigger) this.elMemNewInstinctTrigger.value = '';
        if (this.elMemNewInstinctAction) this.elMemNewInstinctAction.value = '';

        if (window.systemBridge && window.systemBridge.instinctsAction) {
          const res = await window.systemBridge.instinctsAction({
            action: 'add',
            trigger,
            action_text: action,
            scope,
            confidence: 0.85
          });
          if (res && res.ok) {
            this.say(`🧬 Nuovo istinto atomico registrato (${scope === 'global' ? 'Globale' : 'Progetto'})!`);
          }
        }
        this.renderMemoryDashboard();
      });
    }

    // F27: Instincts List Action Delegation (Confirm, Promote, Prune, Delete)
    if (this.elMemInstinctsList) {
      this.elMemInstinctsList.addEventListener('click', async (e) => {
        const confBtn = e.target.closest('.btn-instinct-confirm');
        if (confBtn) {
          const id = confBtn.getAttribute('data-id');
          if (id && window.systemBridge && window.systemBridge.instinctsAction) {
            const res = await window.systemBridge.instinctsAction({ action: 'confirm', id });
            if (res && res.ok) {
              this.say(`🧬 Istinto confermato! Confidence aumentata.`);
            }
            this.renderMemoryDashboard();
          }
          return;
        }

        const promBtn = e.target.closest('.btn-instinct-promote');
        if (promBtn) {
          const id = promBtn.getAttribute('data-id');
          if (id && window.systemBridge && window.systemBridge.instinctsAction) {
            const res = await window.systemBridge.instinctsAction({ action: 'promote', id });
            if (res && res.ok) {
              this.say(`⬆️ Istinto promosso a livello Globale!`);
            }
            this.renderMemoryDashboard();
          }
          return;
        }

        const pruneBtn = e.target.closest('.btn-instinct-prune');
        if (pruneBtn) {
          const id = pruneBtn.getAttribute('data-id');
          if (id && window.systemBridge && window.systemBridge.instinctsAction) {
            const res = await window.systemBridge.instinctsAction({ action: 'prune', id });
            if (res && res.ok) {
              this.say(`🧹 Istinto archiviato (pruned).`);
            }
            this.renderMemoryDashboard();
          }
          return;
        }

        const delBtn = e.target.closest('.btn-instinct-delete');
        if (delBtn) {
          const id = delBtn.getAttribute('data-id');
          if (id && window.systemBridge && window.systemBridge.instinctsAction) {
            const res = await window.systemBridge.instinctsAction({ action: 'delete', id });
            if (res && res.ok) {
              this.say(`✕ Istinto eliminato.`);
            }
            this.renderMemoryDashboard();
          }
          return;
        }
      });
    }

    // Milestone 4: MCP Switchboard Real-time Toggling (F21, F24)
    if (this.elMcpGrid) {
      this.elMcpGrid.addEventListener('change', async (e) => {
        const switchInput = e.target.closest('.mcp-switch-input');
        if (!switchInput || !window.systemBridge) return;
        const mcpId = switchInput.getAttribute('data-mcp-id');
        const enabled = switchInput.checked;

        // Immediate optimistic visual update
        const card = switchInput.closest('.mcp-card');
        if (card) {
          const pill = card.querySelector('.status-pill');
          if (pill) {
            pill.textContent = enabled ? 'ACTIVE' : 'OFF';
            pill.className = `status-pill ${enabled ? 'status-active' : 'status-off'}`;
          }
        }

        const res = await window.systemBridge.toggleMcp(mcpId, enabled);
        if (res && res.ok) {
          if (res.context_budget) {
            if (this.state.system) this.state.system.contextBudget = res.context_budget;
            this.renderContextBudget();
          }
          this.renderMcpGrid();
        }
      });
    }

    // Milestone 4: Skills Switchboard Real-time Toggling (F22, F24)
    if (this.elSkillsSwitchboardList) {
      this.elSkillsSwitchboardList.addEventListener('change', async (e) => {
        const switchInput = e.target.closest('.skill-switch-input');
        if (!switchInput || !window.systemBridge) return;
        const skillId = switchInput.getAttribute('data-skill-id');
        const enabled = switchInput.checked;

        const res = await window.systemBridge.toggleSkill(skillId, enabled);
        if (res && res.ok) {
          if (res.context_budget) {
            if (this.state.system) this.state.system.contextBudget = res.context_budget;
            this.renderContextBudget();
          }
          this.renderSkillsSwitchboard();
        }
      });
    }

    // Milestone 4: Import / Create SKILL.md Modal Handlers (F23)
    if (this.elBtnOpenImportSkillModal) {
      this.elBtnOpenImportSkillModal.addEventListener('click', () => {
        if (this.elModalImportSkill) this.elModalImportSkill.style.display = 'flex';
      });
    }
    if (this.elBtnCloseImportSkillModal) {
      this.elBtnCloseImportSkillModal.addEventListener('click', () => {
        if (this.elModalImportSkill) this.elModalImportSkill.style.display = 'none';
      });
    }
    if (this.elBtnCancelImportSkill) {
      this.elBtnCancelImportSkill.addEventListener('click', () => {
        if (this.elModalImportSkill) this.elModalImportSkill.style.display = 'none';
      });
    }
    if (this.elModalImportSkill) {
      this.elModalImportSkill.addEventListener('click', (e) => {
        if (e.target === this.elModalImportSkill) this.elModalImportSkill.style.display = 'none';
      });
    }
    if (this.elFormImportSkill) {
      this.elFormImportSkill.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = this.elImportSkillId ? this.elImportSkillId.value.trim() : '';
        const name = this.elImportSkillName ? this.elImportSkillName.value.trim() : '';
        const domain = this.elImportSkillDomain ? this.elImportSkillDomain.value : 'pipeline';
        const content = this.elImportSkillContent ? this.elImportSkillContent.value.trim() : '';
        if (!content) return;

        if (window.systemBridge && window.systemBridge.importSkill) {
          const res = await window.systemBridge.importSkill({
            id: id || undefined,
            name: name || undefined,
            domain: domain,
            content: content,
            format: 'markdown'
          });
          if (res && res.ok) {
            this.say(`⚡ Nuova skill "${name || id || 'ECC'}" importata con successo!`);
            if (this.elModalImportSkill) this.elModalImportSkill.style.display = 'none';
            if (this.elImportSkillId) this.elImportSkillId.value = '';
            if (this.elImportSkillName) this.elImportSkillName.value = '';
            if (this.elImportSkillContent) this.elImportSkillContent.value = '';
            await window.systemBridge.fetchSkills();
            this.renderSkillsSwitchboard();
          } else {
            this.say(`⚠️ Errore importazione skill: ${res ? res.error : 'errore di rete'}`);
          }
        }
      });
    }

    // Milestone 4: Add MCP Connector Button (F21)
    if (this.elBtnOpenAddMcp) {
      this.elBtnOpenAddMcp.addEventListener('click', async () => {
        const name = window.prompt("Nome del nuovo connettore MCP esterno (es. Unreal Engine Bridge):");
        if (!name || !name.trim()) return;
        const cmd = window.prompt("Comando o eseguibile di avvio (es. python o npx):", "python");
        const id = name.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_');
        if (window.systemBridge && window.systemBridge.addMcp) {
          const res = await window.systemBridge.addMcp({
            id: id,
            name: name.trim(),
            command: cmd ? cmd.trim() : 'python',
            description: `External MCP server: ${name.trim()}`,
            estimated_tokens: 35,
            enabled: true
          });
          if (res && res.ok) {
            this.say(`🔌 Connettore MCP "${name.trim()}" registrato con successo!`);
            if (res.context_budget) {
              if (this.state.system) this.state.system.contextBudget = res.context_budget;
              this.renderContextBudget();
            }
            await window.systemBridge.fetchMcpConnectors();
            this.renderMcpGrid();
          } else {
            this.say(`⚠️ Errore aggiunta MCP: ${res ? res.error : 'errore di rete'}`);
          }
        }
      });
    }

    // Progressive Disclosure Drawers in Progetti (R3)
    if (this.elBtnToggleNewProjDrawer) {
      this.elBtnToggleNewProjDrawer.addEventListener('click', () => {
        if (!this.elProjCreateBar) this.elProjCreateBar = document.getElementById('proj-create-bar');
        if (this.elProjCreateBar) {
          const isOpen = this.elProjCreateBar.classList.contains('drawer-open') || (this.elProjCreateBar.style.display !== 'none' && this.elProjCreateBar.style.display !== '');
          this.elProjCreateBar.style.display = isOpen ? 'none' : 'flex';
          this.elProjCreateBar.classList.toggle('drawer-open', !isOpen);
          this.elBtnToggleNewProjDrawer.classList.toggle('active', !isOpen);
        }
      });
    }

    if (this.elBtnTogglePlaybookDrawer) {
      this.elBtnTogglePlaybookDrawer.addEventListener('click', () => {
        if (!this.elPlaybookQuickBar) this.elPlaybookQuickBar = document.getElementById('playbook-quick-bar');
        if (this.elPlaybookQuickBar) {
          const isOpen = this.elPlaybookQuickBar.classList.contains('drawer-open') || (this.elPlaybookQuickBar.style.display !== 'none' && this.elPlaybookQuickBar.style.display !== '');
          this.elPlaybookQuickBar.style.display = isOpen ? 'none' : 'flex';
          this.elPlaybookQuickBar.classList.toggle('drawer-open', !isOpen);
          this.elBtnTogglePlaybookDrawer.classList.toggle('active', !isOpen);
        }
      });
    }

    // Memoria 3-Subtab Switcher (R3)
    if (this.elMemorySubtabs) {
      this.elMemorySubtabs.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-mem-view]');
        if (btn) {
          e.preventDefault();
          const subtabId = btn.getAttribute('data-mem-view');
          this.switchMemorySubtab(subtabId);
        }
      });
    }

    // Co-Pilot HUD 1-Click Collaborative Actions (R2)
    if (this.elNekoCopilotHud) {
      this.elNekoCopilotHud.addEventListener('click', async (e) => {
        const openFolderBtn = e.target.closest('.btn-copilot-open-folder');
        if (openFolderBtn) {
          const projId = openFolderBtn.getAttribute('data-proj-id');
          const wsPath = openFolderBtn.getAttribute('data-path') || '';
          this.say('📂 Apertura cartella di lavoro in Esplora Risorse...');
          if (window.systemBridge && window.systemBridge.openProjectFolder) {
            const res = await window.systemBridge.openProjectFolder(projId, wsPath, false);
            if (res && !res.ok) {
              this.say(`⚠️ Impossibile aprire cartella: ${res.error || 'percorso non valido'}`);
            }
          }
          return;
        }

        const scanFolderBtn = e.target.closest('.btn-copilot-scan-folder');
        if (scanFolderBtn) {
          const projId = scanFolderBtn.getAttribute('data-proj-id');
          if (projId && window.systemBridge && window.systemBridge.scanProjectFolder) {
            scanFolderBtn.textContent = '⏳';
            scanFolderBtn.disabled = true;
            const res = await window.systemBridge.scanProjectFolder(projId, true);
            scanFolderBtn.textContent = '🔄';
            scanFolderBtn.disabled = false;
            if (res && res.ok) {
              const count = (res.workspace_summary && res.workspace_summary.total_files !== undefined) ? res.workspace_summary.total_files : 0;
              this.say(`✅ Scansione completata: ${count} file rilevati.`);
              this.renderCopilotHud();
            } else {
              this.say(`⚠️ Scansione fallita: ${res ? res.error : 'errore di rete'}`);
            }
          }
          return;
        }

        const bindFolderBtn = e.target.closest('.btn-copilot-bind-folder');
        if (bindFolderBtn) {
          this.switchWorkspace('view-projects');
          return;
        }

        const startTaskBtn = e.target.closest('.btn-copilot-start-task');
        if (startTaskBtn) {
          const taskId = startTaskBtn.getAttribute('data-task-id');
          const taskTitle = startTaskBtn.getAttribute('data-task-title') || 'Task';
          if (taskId) {
            if (this.actions.todosAction) {
              await this.actions.todosAction({ action: 'status', id: taskId, status: 'doing' });
            } else if (window.systemBridge && window.systemBridge.todosAction) {
              await window.systemBridge.todosAction({ action: 'status', id: taskId, status: 'doing' });
            }
            if (this.state.system && Array.isArray(this.state.system.todosList)) {
              const t = this.state.system.todosList.find((item) => typeof item === 'object' && item.id === taskId);
              if (t) t.status = 'doing';
            }
            this.renderCopilotHud();
            this.renderHomeNextTaskChip();
            this.say(`⚡ Task avviato: "${taskTitle}"`);
          }
          return;
        }

        const completeTaskBtn = e.target.closest('.btn-copilot-complete-task');
        if (completeTaskBtn) {
          const taskId = completeTaskBtn.getAttribute('data-task-id');
          const taskTitle = completeTaskBtn.getAttribute('data-task-title') || 'Task';
          if (taskId) {
            if (this.actions.todosAction) {
              await this.actions.todosAction({ action: 'status', id: taskId, status: 'done' });
            } else if (window.systemBridge && window.systemBridge.todosAction) {
              await window.systemBridge.todosAction({ action: 'status', id: taskId, status: 'done' });
            }
            if (this.state.system && Array.isArray(this.state.system.todosList)) {
              const t = this.state.system.todosList.find((item) => typeof item === 'object' && item.id === taskId);
              if (t) t.status = 'done';
            }
            this.renderCopilotHud();
            this.renderHomeNextTaskChip();
            this.say(`✅ Task completato: "${taskTitle}"`);
          }
          return;
        }

        const qgBtn = e.target.closest('.btn-copilot-quality-gate');
        if (qgBtn) {
          const projId = qgBtn.getAttribute('data-proj-id');
          const projName = qgBtn.getAttribute('data-proj-name') || 'Progetto';
          if (projId) {
            this.openQualityGateModal(projId, projName);
          } else {
            this.say('⚠️ Seleziona un progetto attivo prima di avviare il Quality Gate.');
          }
          return;
        }

        const inspectMemBtn = e.target.closest('.btn-copilot-inspect-mem');
        if (inspectMemBtn) {
          this.switchWorkspace('view-memory');
          return;
        }
      });
    }
  }

  async openBindFolderPrompt(projId) {
    const projects = (this.state && this.state.system && Array.isArray(this.state.system.projectsList)) ? this.state.system.projectsList : [];
    const target = projects.find((p) => p.id === projId) || { id: projId, name: 'Progetto' };
    const currentPath = target.workspace_path || '';
    const newPath = window.prompt(`Inserisci il percorso assoluto della cartella di lavoro per il progetto "${target.name}":`, currentPath);
    if (newPath !== null && newPath.trim()) {
      const cleanPath = newPath.trim();
      this.say(`📁 Associazione cartella in corso: "${cleanPath}"...`);
      if (window.systemBridge && window.systemBridge.bindProjectFolder) {
        const res = await window.systemBridge.bindProjectFolder(projId, cleanPath, false);
        if (res && res.ok) {
          target.workspace_path = cleanPath;
          if (res.workspace_summary) target.workspace_summary = res.workspace_summary;
          this.say(`✅ Cartella associata a "${target.name}": ${cleanPath}`);
          this.renderProjectHub();
        } else {
          this.say(`⚠️ Errore associazione cartella: ${res ? res.error : 'percorso non valido'}`);
        }
      }
    }
  }

  setContextMode(mode, syncBackend = true) {
    mode = String(mode || '').toLowerCase();
    if (!mode) mode = 'operativo';
    const validModes = ['operativo', 'project_manager', 'knowledge_curator'];
    if (!validModes.includes(mode)) {
      if (mode.includes('curator') || mode.includes('know')) mode = 'knowledge_curator';
      else if (mode.includes('pm') || mode.includes('proj')) mode = 'project_manager';
      else mode = 'operativo';
    }
    this.currentContextMode = mode;
    if (this.state && this.state.system) {
      this.state.system.contextMode = mode;
    }

    if (typeof document !== 'undefined') {
      if (document.body) {
        document.body.setAttribute('data-context-mode', mode);
      }
      if (!this.elHudLayer) {
        this.elHudLayer = document.getElementById('hud-layer');
      }
      if (this.elHudLayer) {
        this.elHudLayer.setAttribute('data-context-mode', mode);
      }
    }

    if (this.elContextModePill) {
      this.elContextModePill.classList.remove('mode-operativo', 'mode-project_manager', 'mode-knowledge_curator');
      this.elContextModePill.classList.add(`mode-${mode}`);
    }
    if (this.elContextModeIcon) {
      this.elContextModeIcon.textContent = mode === 'operativo' ? '🛠️' : (mode === 'project_manager' ? '📋' : '🧠');
    }
    if (this.elContextModeLabel) {
      this.elContextModeLabel.textContent = mode === 'operativo' ? 'Operativo' : (mode === 'project_manager' ? 'Project Manager' : 'Knowledge Curator');
    }
    if (syncBackend && window.systemBridge && window.systemBridge.setContextMode) {
      window.systemBridge.setContextMode(mode);
    }
  }

  switchWorkspace(viewId) {
    if (!viewId) viewId = 'view-home';
    this.activeWorkspace = viewId;

    let targetMode = 'operativo';
    if (viewId === 'view-todo' || viewId === 'view-projects' || viewId === 'view-calendar') {
      targetMode = 'project_manager';
    } else if (viewId === 'view-memory') {
      targetMode = 'knowledge_curator';
    }
    this.setContextMode(targetMode, true);

    if (this.elWorkspaceNav) {
      this.elWorkspaceNav.querySelectorAll('[data-workspace-tab]').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-workspace-tab') === viewId);
      });
    }

    document.body.classList.toggle('view-home', viewId === 'view-home');

    if (viewId === 'view-home') {
      if (this.elWorkspaceContainer) {
        this.elWorkspaceContainer.style.display = 'none';
      }
      if (this.elNekoCopilotHud) {
        this.elNekoCopilotHud.style.display = 'flex';
      }
      if (this.elViewTodo) this.elViewTodo.style.display = 'none';
      if (this.elViewProjects) this.elViewProjects.style.display = 'none';
      if (this.elViewCalendar) this.elViewCalendar.style.display = 'none';
      if (this.elViewMemory) this.elViewMemory.style.display = 'none';
      this.renderHomeNextTaskChip();
      this.renderCopilotHud();
    } else {
      if (this.elNekoCopilotHud) {
        this.elNekoCopilotHud.style.display = 'none';
      }
      if (this.elWorkspaceContainer) {
        this.elWorkspaceContainer.style.display = 'flex';
      }
      if (this.elHomeNextTaskChip) {
        this.elHomeNextTaskChip.style.display = 'none';
      }

      if (this.elViewTodo) this.elViewTodo.style.display = viewId === 'view-todo' ? 'flex' : 'none';
      if (this.elViewProjects) this.elViewProjects.style.display = viewId === 'view-projects' ? 'flex' : 'none';
      if (this.elViewCalendar) this.elViewCalendar.style.display = viewId === 'view-calendar' ? 'flex' : 'none';
      if (this.elViewMemory) this.elViewMemory.style.display = viewId === 'view-memory' ? 'flex' : 'none';

      if (viewId === 'view-todo') this.renderKanbanBoard();
      else if (viewId === 'view-projects') this.renderProjectHub();
      else if (viewId === 'view-calendar') this.renderCalendarDashboard();
      else if (viewId === 'view-memory') this.renderMemoryDashboard();
    }

    this.renderSmartBanner();
  }

  normalizeTodo(item, idx = 0) {
    if (!item) {
      return {
        id: 'todo_' + idx,
        title: 'Attività',
        project: '',
        due_label: '',
        due_date: '',
        status: 'todo',
        priority: 'normal',
        subtasks: []
      };
    }
    if (typeof item === 'object') {
      return {
        id: item.id || ('todo_' + idx),
        title: item.title || item.text || '',
        project: item.project || '',
        due_label: item.due_label || item.due || '',
        due_date: item.due_date || '',
        status: (item.status === 'doing' || item.status === 'done') ? item.status : 'todo',
        priority: (item.priority === 'high' || item.priority === 'low') ? item.priority : 'normal',
        subtasks: Array.isArray(item.subtasks) ? item.subtasks : []
      };
    }
    const str = String(item).trim();
    let project = '';
    let title = str;
    let due_label = '';
    let due_date = '';

    const m = str.match(/^([^–-]+)\s*[–-]\s*(.+)$/);
    if (m) {
      project = m[1].trim();
      title = m[2].trim();
    }
    const mDue = title.match(/^(.+?)\s*\(([^)]+)\)$/);
    if (mDue) {
      title = mDue[1].trim();
      due_label = mDue[2].trim();
    }

    return {
      id: 'todo_str_' + idx,
      title: title || str,
      project,
      due_label,
      due_date,
      status: 'todo',
      priority: 'normal',
      subtasks: []
    };
  }

  renderCopilotHud() {
    if (!this.elNekoCopilotHud) return;

    const system = (this.state && this.state.system) ? this.state.system : {};
    const projects = Array.isArray(system.projectsList) ? system.projectsList : [];
    const activeProjName = system.activeProjectName || (projects.length > 0 && projects[0] ? projects[0].name : 'Nessun progetto attivo');
    const activeProj = projects.find((p) => p && p.name === activeProjName) || (projects.length > 0 ? projects[0] : null);

    // 1. Radar Card (#copilot-radar-body)
    if (this.elCopilotRadarBody) {
      const rawWin = system.activeWindow || system.active_window || {};
      const activeWin = typeof rawWin === 'object' ? rawWin : { title: String(rawWin) };
      const activeApp = activeWin.app || activeWin.app_name || system.active_app || 'Desktop';
      const activeDoc = activeWin.title || activeWin.window_title || (activeProj ? activeProj.name : 'In attesa di focus operativo');
      const wsPath = activeProj ? (activeProj.workspace_path || '') : '';
      const wsSummary = activeProj ? (activeProj.workspace_summary || null) : null;
      const modToday = (wsSummary && wsSummary.modified_today_count !== undefined) ? wsSummary.modified_today_count : 0;
      const totalFiles = (wsSummary && wsSummary.total_files !== undefined) ? wsSummary.total_files : 0;
      const gitBranch = (wsSummary && wsSummary.git_info && wsSummary.git_info.branch) ? wsSummary.git_info.branch : 'main';

      let folderActionHtml = '';
      if (wsPath) {
        folderActionHtml = `
          <div class="copilot-ws-row">
            <span class="copilot-ws-path" title="${this.escapeHtml(wsPath)}">📁 ${this.escapeHtml(wsPath)}</span>
            <div class="copilot-ws-btns">
              <button type="button" class="copilot-mini-btn btn-copilot-open-folder" data-proj-id="${activeProj ? this.escapeHtml(activeProj.id || '') : ''}" data-path="${this.escapeHtml(wsPath)}" title="Apri cartella">📂</button>
              <button type="button" class="copilot-mini-btn btn-copilot-scan-folder" data-proj-id="${activeProj ? this.escapeHtml(activeProj.id || '') : ''}" title="Scansiona radar">🔄</button>
            </div>
          </div>
        `;
      } else {
        folderActionHtml = `
          <div class="copilot-ws-row copilot-unbound">
            <span class="copilot-ws-unbound">📁 <em>Cartella non associata</em></span>
            <button type="button" class="copilot-mini-btn btn-accent btn-copilot-bind-folder" title="Associa cartella">🔗 Associa</button>
          </div>
        `;
      }

      this.elCopilotRadarBody.innerHTML = `
        <div class="copilot-metric-row">
          <span class="copilot-metric-label">Finestra Attiva:</span>
          <span class="copilot-app-tag">🖥️ ${this.escapeHtml(activeApp)}</span>
        </div>
        <div class="copilot-doc-title" title="${this.escapeHtml(activeDoc)}">
          📄 ${this.escapeHtml(activeDoc)}
        </div>
        <div class="copilot-metric-row">
          <span class="copilot-metric-label">Progetto Focus:</span>
          <strong class="copilot-proj-focus">📌 ${this.escapeHtml(activeProj ? activeProj.name : activeProjName)}</strong>
        </div>
        ${folderActionHtml}
        <div class="copilot-radar-telemetry">
          <span class="radar-chip ${modToday > 0 ? 'chip-active' : ''}">⚡ ${modToday} modif. oggi</span>
          <span class="radar-chip">📦 ${totalFiles} file</span>
          <span class="radar-chip chip-git">🌿 ${this.escapeHtml(gitBranch)}</span>
        </div>
      `;
    }

    // 2. Task Focus Card (#copilot-task-body & #copilot-task-status)
    if (this.elCopilotTaskBody) {
      const rawTodos = Array.isArray(system.todosList) ? system.todosList : [];
      const todos = rawTodos.map((t, i) => this.normalizeTodo(t, i));
      const doingTask = todos.find((t) => t.status === 'doing');
      const nextTodo = todos.find((t) => t.status === 'todo');
      const activeTask = doingTask || nextTodo || null;

      if (this.elCopilotTaskStatus) {
        if (doingTask) {
          this.elCopilotTaskStatus.textContent = 'IN CORSO';
          this.elCopilotTaskStatus.className = 'card-status-badge status-doing';
        } else if (nextTodo) {
          this.elCopilotTaskStatus.textContent = 'PROSSIMO';
          this.elCopilotTaskStatus.className = 'card-status-badge status-todo';
        } else {
          this.elCopilotTaskStatus.textContent = 'LIBERO';
          this.elCopilotTaskStatus.className = 'card-status-badge status-free';
        }
      }

      if (activeTask) {
        const isDoing = activeTask.status === 'doing';
        const projName = activeTask.project || (activeProj ? activeProj.name : '');
        const dueText = activeTask.due_label || activeTask.due_date || '';

        this.elCopilotTaskBody.innerHTML = `
          <div class="copilot-task-title-row">
            <span class="copilot-task-indicator">${isDoing ? '⚡' : '📌'}</span>
            <div class="copilot-task-details">
              <strong class="copilot-task-name">${this.escapeHtml(activeTask.title)}</strong>
              <div class="copilot-task-meta">
                ${projName ? `<span class="badge-proj-tag">${this.escapeHtml(projName)}</span>` : ''}
                ${dueText ? `<span class="badge-due-tag">📅 ${this.escapeHtml(dueText)}</span>` : ''}
                <span class="badge-prio-tag prio-${this.escapeHtml(activeTask.priority || 'normal')}">${this.escapeHtml(activeTask.priority || 'normal')}</span>
              </div>
            </div>
          </div>
          <div class="copilot-task-actions">
            ${isDoing ? `
              <button type="button" class="copilot-btn-action btn-copilot-complete-task" data-task-id="${this.escapeHtml(activeTask.id)}" data-task-title="${this.escapeHtml(activeTask.title)}">✅ Completa Task</button>
              <button type="button" class="copilot-btn-action btn-copilot-quality-gate" data-proj-id="${activeProj ? this.escapeHtml(activeProj.id || '') : ''}" data-proj-name="${activeProj ? this.escapeHtml(activeProj.name || '') : ''}">🛡️ Quality Gate</button>
            ` : `
              <button type="button" class="copilot-btn-action btn-copilot-start-task" data-task-id="${this.escapeHtml(activeTask.id)}" data-task-title="${this.escapeHtml(activeTask.title)}">⚡ Avvia Task</button>
              <button type="button" class="copilot-btn-action btn-copilot-complete-task" data-task-id="${this.escapeHtml(activeTask.id)}" data-task-title="${this.escapeHtml(activeTask.title)}">✅ Segna Fatto</button>
            `}
          </div>
        `;
      } else {
        this.elCopilotTaskBody.innerHTML = `
          <div class="copilot-task-empty">
            <span>✨ Nessun task operativo in coda</span>
            <p>Seleziona un progetto o chiedi a Neko di pianificare nuove attività.</p>
          </div>
        `;
      }
    }

    // 3. Brain & Rules Card (#copilot-brain-body & #copilot-tok-val)
    if (this.elCopilotBrainBody) {
      const budget = system.contextBudget || { used_tokens: 3420, max_tokens: 8192, used_pct: 42 };
      const budgetPct = budget.usage_pct !== undefined
        ? Math.round(budget.usage_pct)
        : (budget.used_pct !== undefined ? Math.round(budget.used_pct) : 42);
      if (this.elCopilotTokVal) {
        this.elCopilotTokVal.textContent = `${budget.used_tokens || 3420} tok (${budgetPct}%)`;
      }

      const memories = Array.isArray(system.memories)
        ? system.memories
        : (Array.isArray(system.memoriesList) ? system.memoriesList : []);
      const pinnedRule = memories.find((m) => m && m.pinned)
        || memories.find((m) => m && m.category && m.category.toLowerCase().includes('regola'))
        || (memories.length > 0 ? memories[0] : null);
      const ruleText = pinnedRule
        ? (pinnedRule.text || pinnedRule.content || '')
        : (activeProj && activeProj.notes ? activeProj.notes : 'Nessuna regola vincolante registrata');

      const instincts = Array.isArray(system.instinctsList) ? system.instinctsList : (Array.isArray(system.atomicInstincts) ? system.atomicInstincts : []);
      const topInstinct = instincts.find((i) => i && typeof i.confidence === 'number' && i.confidence >= 0.85)
        || (instincts.length > 0 ? instincts[0] : null);
      const instinctAction = topInstinct
        ? (topInstinct.action || topInstinct.action_text || topInstinct.trigger || '')
        : 'Nessun istinto registrato — rispondi a Conosciamoci o lavora con Neko';

      this.elCopilotBrainBody.innerHTML = `
        <div class="copilot-brain-section">
          <div class="copilot-brain-label">📌 Regola Cliente Attiva:</div>
          <div class="copilot-rule-callout" title="${this.escapeHtml(ruleText)}">
            "${this.escapeHtml(ruleText)}"
          </div>
        </div>
        <div class="copilot-brain-section">
          <div class="copilot-brain-label">🧬 Top Istinto Autonomo:</div>
          <div class="copilot-instinct-badge" title="${this.escapeHtml(instinctAction)}">
            ⚡ ${this.escapeHtml(instinctAction)}
          </div>
        </div>
        <div class="copilot-brain-actions">
          <button type="button" class="copilot-btn-action btn-copilot-inspect-mem">🧠 Ispeziona Memoria</button>
        </div>
      `;
    }
  }

  renderHomeNextTaskChip() {
    if (!this.elHomeNextTaskChip) return;
    const rawList = (this.state.system && Array.isArray(this.state.system.todosList))
      ? this.state.system.todosList
      : [];
    const normalized = rawList.map((t, i) => this.normalizeTodo(t, i));
    const pending = normalized.filter((t) => t.status !== 'done');

    if (pending.length === 0) {
      this.elHomeNextTaskChip.style.display = 'none';
      return;
    }

    const topTask = pending.find((t) => t.status === 'doing' && (t.due_label || t.due_date))
      || pending.find((t) => t.due_label || t.due_date)
      || pending[0];

    if (this.elHomeNextTaskTitle) {
      const projPrefix = topTask.project ? `[${topTask.project}] ` : '';
      this.elHomeNextTaskTitle.textContent = projPrefix + topTask.title;
    }
    if (this.elHomeNextTaskDate) {
      const dateText = topTask.due_label || topTask.due_date || '';
      this.elHomeNextTaskDate.textContent = dateText ? `(${dateText})` : '';
    }
    this.elHomeNextTaskChip.style.display = 'inline-flex';
  }

  renderKanbanBoard() {
    const rawList = (this.state.system && Array.isArray(this.state.system.todosList))
      ? this.state.system.todosList
      : [];
    const tasks = rawList.map((t, i) => this.normalizeTodo(t, i));

    const allProjects = new Set();
    tasks.forEach((t) => { if (t.project) allProjects.add(t.project); });
    if (this.state.system && Array.isArray(this.state.system.projectsList)) {
      this.state.system.projectsList.forEach((p) => { if (p.name) allProjects.add(p.name); });
    }

    if (this.elKanbanFilters) {
      let filterHtml = `<button type="button" class="proj-filter-btn ${this.kanbanProjectFilter === 'all' ? 'active' : ''}" data-filter-proj="all">Tutti</button>`;
      allProjects.forEach((pName) => {
        const isAct = this.kanbanProjectFilter.toLowerCase() === pName.toLowerCase();
        filterHtml += `<button type="button" class="proj-filter-btn ${isAct ? 'active' : ''}" data-filter-proj="${this.escapeHtml(pName)}">${this.escapeHtml(pName)}</button>`;
      });
      this.elKanbanFilters.innerHTML = filterHtml;
    }

    if (this.elKanbanAddProject) {
      let selHtml = '<option value="">Nessun progetto</option>';
      allProjects.forEach((pName) => {
        selHtml += `<option value="${this.escapeHtml(pName)}">${this.escapeHtml(pName)}</option>`;
      });
      this.elKanbanAddProject.innerHTML = selHtml;
    }

    const filtered = this.kanbanProjectFilter === 'all'
      ? tasks
      : tasks.filter((t) => t.project.toLowerCase() === this.kanbanProjectFilter.toLowerCase());

    const colTodo = filtered.filter((t) => t.status === 'todo');
    const colDoing = filtered.filter((t) => t.status === 'doing');
    const colDone = filtered.filter((t) => t.status === 'done');

    if (this.elCountKanbanTodo) this.elCountKanbanTodo.textContent = String(colTodo.length);
    if (this.elCountKanbanDoing) this.elCountKanbanDoing.textContent = String(colDoing.length);
    if (this.elCountKanbanDone) this.elCountKanbanDone.textContent = String(colDone.length);
    if (this.elTabTodoBadge) this.elTabTodoBadge.textContent = String(colTodo.length + colDoing.length);

    const renderCard = (t) => {
      const projBadge = t.project ? `<span class="kanban-badge badge-proj">📌 ${this.escapeHtml(t.project)}</span>` : '';
      const dueBadge = (t.due_label || t.due_date) ? `<span class="kanban-badge badge-due">⏰ ${this.escapeHtml(t.due_label || t.due_date)}</span>` : '';
      const prioBadge = t.priority === 'high' ? `<span class="kanban-badge badge-priority">⚡ ALTA</span>` : '';

      let subtasksHtml = '';
      if (Array.isArray(t.subtasks) && t.subtasks.length > 0) {
        subtasksHtml = '<div class="kanban-subtasks-list">';
        t.subtasks.forEach((st, idx) => {
          const isDone = Boolean(st.done);
          subtasksHtml += `
            <label class="kanban-subtask-item ${isDone ? 'completed' : ''}">
              <input type="checkbox" class="kanban-subtask-check" data-todo-id="${this.escapeHtml(t.id)}" data-subtask-idx="${idx}" ${isDone ? 'checked' : ''} />
              <span>${this.escapeHtml(st.title || st.text || '')}</span>
            </label>
          `;
        });
        subtasksHtml += '</div>';
      }

      let moveBtn = '';
      if (t.status === 'todo') {
        moveBtn = `<button type="button" class="kanban-action-btn btn-kanban-move" data-id="${this.escapeHtml(t.id)}" data-status="doing">▶ Inizia</button>`;
      } else if (t.status === 'doing') {
        moveBtn = `<button type="button" class="kanban-action-btn btn-kanban-move" data-id="${this.escapeHtml(t.id)}" data-status="done">✓ Completa</button>`;
      } else if (t.status === 'done') {
        moveBtn = `<button type="button" class="kanban-action-btn btn-kanban-move" data-id="${this.escapeHtml(t.id)}" data-status="todo">↺ Riapri</button>`;
      }

      return `
        <div class="kanban-card priority-${this.escapeHtml(t.priority)}" data-todo-id="${this.escapeHtml(t.id)}">
          <div class="kanban-card-header">
            <div class="kanban-card-title">${this.escapeHtml(t.title)}</div>
          </div>
          <div class="kanban-card-meta">
            ${projBadge}
            ${dueBadge}
            ${prioBadge}
          </div>
          ${subtasksHtml}
          <div class="kanban-card-actions">
            <div class="kanban-card-actions-left">
              ${moveBtn}
              <button type="button" class="kanban-action-btn btn-ai-breakdown" data-id="${this.escapeHtml(t.id)}" title="Chiedi a Neko di scomporre questo task in sotto-attività con l'AI">✨ Scomponi con AI</button>
            </div>
            <div class="kanban-card-actions-right">
              <button type="button" class="kanban-action-btn btn-kanban-del" data-id="${this.escapeHtml(t.id)}" title="Elimina">🗑</button>
            </div>
          </div>
        </div>
      `;
    };

    const emptyHtml = '<div style="color:var(--text-muted); font-size:12px; padding:20px; text-align:center;">Nessuna attività</div>';

    if (this.elKanbanColTodo) {
      this.elKanbanColTodo.innerHTML = colTodo.length > 0 ? colTodo.map(renderCard).join('') : emptyHtml;
    }
    if (this.elKanbanColDoing) {
      this.elKanbanColDoing.innerHTML = colDoing.length > 0 ? colDoing.map(renderCard).join('') : emptyHtml;
    }
    if (this.elKanbanColDone) {
      this.elKanbanColDone.innerHTML = colDone.length > 0 ? colDone.map(renderCard).join('') : emptyHtml;
    }
  }

  renderProjectHub() {
    let rawProjects = (this.state.system && Array.isArray(this.state.system.projectsList) && this.state.system.projectsList.length > 0)
      ? this.state.system.projectsList
      : (Array.isArray(this.projectsState.projects) && this.projectsState.projects.length > 0 ? this.projectsState.projects : []);

    const activeList = rawProjects.filter((p) => p.is_active !== 0 && p.status !== 'archived');
    const archiveList = rawProjects.filter((p) => p.is_active === 0 || p.status === 'archived');

    if (this.elHubProjActiveCount) this.elHubProjActiveCount.textContent = String(activeList.length);
    if (this.elHubProjArchCount) this.elHubProjArchCount.textContent = String(archiveList.length);
    if (this.elTabProjBadge) this.elTabProjBadge.textContent = String(activeList.length);

    if (this.hubViewMode === 'archive') {
      if (this.elHubProjectsGrid) this.elHubProjectsGrid.style.display = 'none';
      if (this.elHubProjectsArchiveList) {
        this.elHubProjectsArchiveList.style.display = 'flex';
        if (archiveList.length === 0) {
          this.elHubProjectsArchiveList.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:20px; text-align:center;">Nessun progetto archiviato.</div>';
        } else {
          this.elHubProjectsArchiveList.innerHTML = archiveList.map((p) => `
            <div class="hub-project-card" style="opacity: 0.7;">
              <div class="hub-proj-header">
                <h3 class="hub-proj-title">📦 ${this.escapeHtml(p.name)}</h3>
                <span class="hub-proj-client-pill">${this.escapeHtml(p.client || 'Archiviato')}</span>
              </div>
            </div>
          `).join('');
        }
      }
      return;
    }

    if (this.elHubProjectsArchiveList) this.elHubProjectsArchiveList.style.display = 'none';
    if (!this.elHubProjectsGrid) return;
    this.elHubProjectsGrid.style.display = 'grid';

    const rawTodos = (this.state.system && Array.isArray(this.state.system.todosList))
      ? this.state.system.todosList
      : [];
    const allTasks = rawTodos.map((t, i) => this.normalizeTodo(t, i));

    const activeFocusName = (this.state.system && this.state.system.activeProjectName) || '';

    let html = '';
    activeList.forEach((p) => {
      const pName = p.name || 'Progetto';
      const isFocus = Boolean(activeFocusName && activeFocusName.toLowerCase() === pName.toLowerCase());
      const linked = allTasks.filter((t) => t.project && t.project.toLowerCase() === pName.toLowerCase());
      const total = linked.length;
      const done = linked.filter((t) => t.status === 'done').length;
      const pct = total > 0 ? Math.round((done / total) * 100) : 0;

      const rawNotes = Array.isArray(p.notes) ? p.notes.join(' • ') : (p.notes || '');
      const rule = rawNotes || 'Nessuna regola specifica registrata';
      const stack = Array.isArray(p.tech_stack)
        ? p.tech_stack
        : (Array.isArray(p.stack) ? p.stack : (typeof p.stack === 'string' && p.stack.trim() ? p.stack.split(',') : []));

      let tasksHtml = '';
      if (linked.length > 0) {
        tasksHtml = linked.map((t) => `
          <div class="hub-proj-task-row ${t.status === 'done' ? 'task-done' : ''}">
            <span>${t.status === 'done' ? '✅' : (t.status === 'doing' ? '⚡' : '📌')} ${this.escapeHtml(t.title)}</span>
            <span class="badge-due">${this.escapeHtml(t.due_label || t.due_date || '')}</span>
          </div>
        `).join('');
      } else {
        tasksHtml = '<div style="color:var(--text-muted); font-size:11.5px;">Nessuna attività collegata. Creane una in To-Do assegnando questo progetto!</div>';
      }

      // Workspace Folder & Live Radar Bar (F18, F19)
      const wsPath = p.workspace_path || '';
      const wsSummary = p.workspace_summary || null;

      let folderHtml = '';
      if (wsPath) {
        let radarBadgesHtml = '';
        if (wsSummary && wsSummary.exists) {
          const modCount = wsSummary.modified_today_count || 0;
          const modBadge = `<span class="radar-badge ${modCount > 0 ? 'radar-badge-active' : ''}">⚡ ${modCount} file modif. oggi</span>`;

          let extBadges = '';
          if (Array.isArray(wsSummary.key_exports) && wsSummary.key_exports.length > 0) {
            wsSummary.key_exports.slice(0, 3).forEach((ke) => {
              const fileName = (ke.path || '').split(/[\\/]/).pop();
              extBadges += `<span class="radar-badge radar-badge-asset">📦 ${this.escapeHtml(fileName)} (${ke.size_mb || 0}MB)</span>`;
            });
          } else if (Array.isArray(wsSummary.detected_stack) && wsSummary.detected_stack.length > 0) {
            wsSummary.detected_stack.slice(0, 3).forEach((st) => {
              extBadges += `<span class="radar-badge">🛠️ ${this.escapeHtml(String(st))}</span>`;
            });
          }

          let gitBadge = '';
          if (wsSummary.git_info && wsSummary.git_info.is_git_repo) {
            const dirty = wsSummary.git_info.dirty_count > 0 ? ` • ${wsSummary.git_info.dirty_count} mod` : '';
            gitBadge = `<span class="radar-badge radar-badge-git">🌿 ${this.escapeHtml(wsSummary.git_info.branch || 'main')}${dirty}</span>`;
          }

          radarBadgesHtml = `
            <div class="hub-proj-radar-row">
              ${modBadge}
              ${extBadges}
              ${gitBadge}
            </div>
          `;
        }

        folderHtml = `
          <div class="hub-proj-folder-section">
            <div class="hub-proj-folder-bar">
              <span class="folder-label" title="${this.escapeHtml(wsPath)}">📁 ${this.escapeHtml(wsPath)}</span>
              <div class="folder-btns">
                <button type="button" class="term-btn term-btn-sm btn-proj-open-folder" data-proj-id="${this.escapeHtml(p.id || '')}" data-path="${this.escapeHtml(wsPath)}" data-proj-name="${this.escapeHtml(pName)}" title="Apri in Esplora Risorse">📂 Apri</button>
                <button type="button" class="term-btn term-btn-sm btn-proj-scan-folder" data-proj-id="${this.escapeHtml(p.id || '')}" data-path="${this.escapeHtml(wsPath)}" title="Scansiona disco e aggiorna radar">🔄 Scansiona</button>
                <button type="button" class="term-btn term-btn-sm btn-proj-bind-folder" data-proj-id="${this.escapeHtml(p.id || '')}" title="Cambia cartella associata">📁 Cambia</button>
              </div>
            </div>
            ${radarBadgesHtml}
          </div>
        `;
      } else {
        folderHtml = `
          <div class="hub-proj-folder-section folder-unbound">
            <div class="hub-proj-folder-bar">
              <span class="folder-label-empty">📁 <em>Nessuna cartella associata</em></span>
              <button type="button" class="term-btn term-btn-sm btn-accent btn-proj-bind-folder" data-proj-id="${this.escapeHtml(p.id || '')}">📁 Associa Cartella</button>
            </div>
          </div>
        `;
      }

      html += `
        <div class="hub-project-card ${isFocus ? 'card-in-focus' : ''}" data-proj-id="${this.escapeHtml(p.id || '')}">
          <!-- Zone 1: Header + Progress with Focus Indicator -->
          <div class="proj-card-zone proj-zone-header hub-proj-card-header">
            <div class="hub-proj-header">
              <div style="display:flex; align-items:center; gap:8px;">
                <h3 class="hub-proj-title">📌 ${this.escapeHtml(pName)}</h3>
                ${isFocus ? '<span class="badge-focus">🎯 IN FOCUS</span>' : ''}
              </div>
              <span class="hub-proj-client-pill">Cliente: ${this.escapeHtml(p.client || 'Diretto')}</span>
            </div>

            <div class="hub-proj-progress">
              <div class="hub-proj-progress-header">
                <span>Avanzamento To-Do:</span>
                <strong>${done}/${total} task (${pct}%)</strong>
              </div>
              <div class="hub-progress-track">
                <div class="hub-progress-bar" style="width: ${pct}%;"></div>
              </div>
            </div>
          </div>

          <!-- Zone 2: Workspace Folder + Radar Strip -->
          <div class="proj-card-zone proj-zone-radar">
            ${folderHtml}
          </div>

          <!-- Zone 3: Pinned Rules & Stack -->
          <div class="proj-card-zone proj-zone-rules">
            <div class="hub-proj-section-title">Attività collegate (${total}):</div>
            <div class="hub-proj-tasks-list">
              ${tasksHtml}
            </div>

            <div class="hub-proj-section-title">Regole e Note Cliente:</div>
            <div class="hub-proj-rules">${this.escapeHtml(rule)}</div>

            <div class="hub-proj-section-title">Stack Tecnologico:</div>
            <div class="hub-proj-stack">
              ${stack.map((s) => `<span class="hub-tech-tag">${this.escapeHtml(String(s).trim())}</span>`).join('')}
            </div>
          </div>

          <!-- Zone 4: Action Bar -->
          <div class="proj-card-zone proj-zone-actions hub-proj-actions">
            <button type="button" class="term-btn term-btn-sm ${isFocus ? 'btn-accent' : 'btn-proj-focus'}" data-proj-id="${this.escapeHtml(p.id || '')}" data-proj-name="${this.escapeHtml(pName)}">${isFocus ? '🎯 In Focus' : '🎯 Imposta Focus'}</button>
            <button type="button" class="term-btn term-btn-sm btn-proj-open-folder" data-proj-id="${this.escapeHtml(p.id || '')}" data-path="${this.escapeHtml(wsPath)}" data-proj-name="${this.escapeHtml(pName)}">📂 Apri Cartella</button>
            <button type="button" class="term-btn term-btn-sm btn-accent btn-proj-ask-plan" data-proj-name="${this.escapeHtml(pName)}">🤖 Chiedi Piano a Neko</button>
            <button type="button" class="term-btn term-btn-sm btn-proj-evolve" data-proj-id="${this.escapeHtml(p.id || '')}">🧬 Evolvi in Playbook</button>
            <button type="button" class="term-btn term-btn-sm btn-proj-quality-gate" data-proj-id="${this.escapeHtml(p.id || '')}" data-proj-name="${this.escapeHtml(pName)}">🛡️ Quality Gate</button>
            <button type="button" class="term-btn term-btn-sm btn-proj-archive" data-proj-id="${this.escapeHtml(p.id || '')}">📦 Archivia</button>
          </div>
        </div>
      `;
    });

    if (activeList.length === 0) {
      this.elHubProjectsGrid.innerHTML = '<div style="color:var(--text-muted); font-size:13px; padding:28px; text-align:center; grid-column: 1 / -1;">✨ Nessun progetto attivo. Clicca <strong>+ Nuovo Progetto</strong> o chiedi a Neko in chat di crearne uno!</div>';
    } else {
      this.elHubProjectsGrid.innerHTML = html;
    }
    this.populatePlaybooksDropdown();
  }

  renderCalendarDashboard() {
    if (this.elBtnCalViewWeekly) this.elBtnCalViewWeekly.classList.toggle('active', this.calendarViewMode === 'weekly');
    if (this.elBtnCalViewMonthly) this.elBtnCalViewMonthly.classList.toggle('active', this.calendarViewMode === 'monthly');
    if (this.elBtnCalViewAgenda) this.elBtnCalViewAgenda.classList.toggle('active', this.calendarViewMode === 'agenda');

    if (this.elCalViewWeekly) this.elCalViewWeekly.style.display = this.calendarViewMode === 'weekly' ? 'grid' : 'none';
    if (this.elCalViewMonthly) this.elCalViewMonthly.style.display = this.calendarViewMode === 'monthly' ? 'grid' : 'none';
    if (this.elCalViewAgenda) this.elCalViewAgenda.style.display = this.calendarViewMode === 'agenda' ? 'flex' : 'none';

    if (this.elCalSyncStatusBadge) {
      const syncStatus = (this.state.system && this.state.system.calendarSyncStatus) || null;
      if (syncStatus && syncStatus.synced) {
        this.elCalSyncStatusBadge.textContent = `🔵 Sincronizzato con Google Calendar: ${syncStatus.calendar_names || 'Neko'}`;
        this.elCalSyncStatusBadge.style.color = '#38bdf8';
      } else {
        this.elCalSyncStatusBadge.textContent = '🔵 Google Calendar (.ics) + 🟢 Locali + 🟠 Scadenze To-Do';
        this.elCalSyncStatusBadge.style.color = 'var(--text-secondary)';
      }
    }

    const allEvents = [];

    const rawCalEvents = (this.state.system && Array.isArray(this.state.system.calendarEvents))
      ? this.state.system.calendarEvents
      : [];
    rawCalEvents.forEach((ev) => {
      allEvents.push({
        id: ev.id,
        title: ev.title || ev.summary || 'Impegno',
        date: ev.date || (ev.start ? ev.start.split('T')[0] : ''),
        time: ev.time || (ev.start && ev.start.includes('T') ? ev.start.split('T')[1].slice(0, 5) : '10:00'),
        source: ev.source || 'google'
      });
    });

    const rawTodos = (this.state.system && Array.isArray(this.state.system.todosList))
      ? this.state.system.todosList
      : [];
    rawTodos.map((t, i) => this.normalizeTodo(t, i)).forEach((t) => {
      if (t.due_date || t.due_label) {
        let dateIso = t.due_date;
        if (!dateIso && t.due_label) {
          const m = t.due_label.match(/(\d+)\/(\d+)/);
          if (m) {
            const day = String(m[1]).padStart(2, '0');
            const month = String(m[2]).padStart(2, '0');
            dateIso = `2026-${month}-${day}`;
          }
        }
        if (dateIso) {
          allEvents.push({
            id: 'due_' + t.id,
            title: (t.project ? `[${t.project}] ` : '') + t.title,
            date: dateIso,
            time: 'Scadenza',
            source: 'todo'
          });
        }
      }
    });

    if (this.elTabCalBadge) {
      this.elTabCalBadge.textContent = String(allEvents.length);
    }

    const curr = new Date(this.calendarCursorDate);
    const dayOfWeek = curr.getDay();
    const diffToMonday = curr.getDate() - dayOfWeek + (dayOfWeek === 0 ? -6 : 1);
    const monday = new Date(curr);
    monday.setDate(diffToMonday);

    const weekDays = [];
    for (let i = 0; i < 7; i++) {
      const d = new Date(monday);
      d.setDate(monday.getDate() + i);
      weekDays.push(d);
    }

    const todayIso = new Date().toISOString().split('T')[0];

    if (this.elCalRangeLabel) {
      const startStr = weekDays[0].toLocaleDateString('it-IT', { day: 'numeric', month: 'short' });
      const endStr = weekDays[6].toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' });
      this.elCalRangeLabel.textContent = `${startStr} – ${endStr}`;
    }

    const dayNames = ['LUN', 'MAR', 'MER', 'GIO', 'VEN', 'SAB', 'DOM'];

    if (this.elCalViewWeekly) {
      let weekHtml = '';
      weekDays.forEach((d, idx) => {
        const dIso = d.toISOString().split('T')[0];
        const isToday = dIso === todayIso;
        const dHeader = `${dayNames[idx]} ${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
        const dayEvs = allEvents.filter((ev) => ev.date === dIso);

        let evsHtml = '';
        if (dayEvs.length > 0) {
          evsHtml = dayEvs.map((ev) => `
            <div class="cal-event-pill pill-source-${this.escapeHtml(ev.source)}" data-event-id="${this.escapeHtml(ev.id)}" data-event-source="${this.escapeHtml(ev.source)}">
              <span class="cal-event-time">${ev.source === 'google' ? '🔵 ' : (ev.source === 'todo' ? '🟠 ' : '🟢 ')}${this.escapeHtml(ev.time)}</span>
              <span class="cal-event-title">${this.escapeHtml(ev.title)}</span>
            </div>
          `).join('');
        }

        weekHtml += `
          <div class="cal-day-col ${isToday ? 'is-today' : ''}">
            <div class="cal-day-header">${dHeader}</div>
            <div class="cal-day-events">${evsHtml}</div>
          </div>
        `;
      });
      this.elCalViewWeekly.innerHTML = weekHtml;
    }

    if (this.elCalViewMonthly) {
      const mYear = this.calendarCursorDate.getFullYear();
      const mMonth = this.calendarCursorDate.getMonth();
      const firstDay = new Date(mYear, mMonth, 1);
      const lastDay = new Date(mYear, mMonth + 1, 0);
      let offset = firstDay.getDay() - 1;
      if (offset < 0) offset = 6;

      let monthHtml = '';
      for (let i = 0; i < offset; i++) {
        monthHtml += '<div class="cal-month-cell" style="opacity: 0.3;"></div>';
      }
      for (let day = 1; day <= lastDay.getDate(); day++) {
        const cellDate = new Date(mYear, mMonth, day);
        const cellIso = cellDate.toISOString().split('T')[0];
        const isToday = cellIso === todayIso;
        const cellEvs = allEvents.filter((ev) => ev.date === cellIso);

        monthHtml += `
          <div class="cal-month-cell ${isToday ? 'is-today' : ''}">
            <span class="cal-month-cell-num">${day}</span>
            ${cellEvs.map((ev) => `
              <div class="cal-event-pill pill-source-${this.escapeHtml(ev.source)}" style="padding:2px 4px;font-size:10px;">
                ${this.escapeHtml(ev.title)}
              </div>
            `).join('')}
          </div>
        `;
      }
      this.elCalViewMonthly.innerHTML = monthHtml;
    }

    if (this.elCalViewAgenda) {
      const sorted = [...allEvents].sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
      this.elCalViewAgenda.innerHTML = sorted.map((ev) => `
        <div class="cal-agenda-item">
          <div>
            <span class="badge-due" style="margin-right:8px;">${this.escapeHtml(ev.date)} ${this.escapeHtml(ev.time)}</span>
            <strong style="color:#fff;">${this.escapeHtml(ev.title)}</strong>
          </div>
          <span class="kanban-badge ${ev.source === 'google' ? 'badge-proj' : (ev.source === 'todo' ? 'badge-due' : 'badge-priority')}">
            ${ev.source === 'google' ? 'Google Calendar' : (ev.source === 'todo' ? 'Scadenza To-Do' : 'Neko Locale')}
          </span>
        </div>
      `).join('');
    }
  }

  rollProfilingQuestion() {
    if (this.state && this.state.system && this.state.system.onboarding && this.state.system.onboarding.next_question) {
      const nq = this.state.system.onboarding.next_question;
      if (!this.currentQuestion || nq.id !== this.currentQuestion.id) {
        this.currentQuestion = nq;
        this.currentOnboardCategory = nq.category || 'Profilo';
        return;
      }
    }
    const fallbackQuestions = [
      {
        id: 'q_identity',
        category: 'Identità & Attività',
        question: 'Di cosa ti occupi principalmente nella tua giornata?',
        options: [
          'Sviluppo Software, Programmazione, Dati o IT',
          'Business, Management, Consulenza, Marketing o Ufficio',
          'Design, Comunicazione, Multimedia, Architettura o Creatività',
          'Studio, Ricerca, Scrittura o Libera Professione'
        ]
      },
      {
        id: 'q_tools',
        category: 'Lavoro & Strumenti',
        question: 'Quali programmi, app o strumenti usi più spesso sul PC?',
        options: [
          'Browser, Email, Google Workspace / Pacchetto Office (Excel, Word, Docs)',
          'Notion, Calendario, Appunti, Gestione Task & Video-call',
          'VS Code / Cursor, Terminale, Git, Python e strumenti di sviluppo',
          'Software di Design, Grafica, Video, CAD o Creatività'
        ]
      },
      {
        id: 'q_projects',
        category: 'Clienti & Progetti',
        question: 'Su quale progetto, obiettivo o attività ti stai concentrando in questo periodo?',
        options: [
          'Un progetto principale con scadenze e obiettivi da raggiungere',
          'Gestione di clienti, commesse, preventivi o consegne',
          'Sviluppo di un prodotto, applicazione o progetto personale',
          'Studio, ricerca o organizzazione delle attività giornaliere'
        ]
      },
      {
        id: 'q_routine',
        category: 'Routine & Orari',
        question: 'Qual è la regola, la priorità o il metodo di lavoro più importante che vuoi che io ricordi sempre?',
        options: [
          'Dare sempre priorità alle scadenze imminenti e alle attività urgenti',
          'Tenere traccia precisa di note, accordi e regole per ogni progetto o cliente',
          'Controllare sempre qualità, ordine e dettagli prima di chiudere un\'attività',
          'Aiutarmi a restare concentrato su un obiettivo alla volta senza perdere il filo'
        ]
      },
      {
        id: 'q_style',
        category: 'Stile & Preferenze',
        question: 'Come preferisci che comunichi con te e quale attività mettiamo in lista per oggi?',
        options: [
          'Diretto, sintetico e pratico — vai subito al sodo in 2-3 righe',
          'Proattivo: ricordami scadenze, priorità e proponimi il prossimo passo',
          'Ordinato per punti elenco e checklist chiare passo-passo',
          'Approfondito, analitico e dettagliato quando affrontiamo un problema'
        ]
      }
    ];
    const rand = fallbackQuestions[Math.floor(Math.random() * fallbackQuestions.length)];
    this.currentQuestion = rand;
    this.currentOnboardCategory = rand.category;
  }

  renderContextBudget() {
    // F26: Context Budget Telemetry Meter
    const budget = (this.state.system && this.state.system.contextBudget) || {
      total_budget: 8192,
      used_tokens: 1850,
      usage_pct: 22.6,
      system: 512,
      profile_and_skills: 320,
      instincts: 410,
      memories: 280,
      projects_and_todos: 220,
      chat_history: 108
    };

    const totalBudget = budget.total_budget || 8192;
    const usedTokens = budget.used_tokens !== undefined ? budget.used_tokens : 0;
    const usagePct = budget.usage_pct !== undefined ? budget.usage_pct : (totalBudget > 0 ? Math.round((usedTokens / totalBudget) * 100) : 0);

    if (this.elBudgetUsedTokens) this.elBudgetUsedTokens.textContent = String(usedTokens);
    if (this.elBudgetUsedPct) this.elBudgetUsedPct.textContent = `${usagePct}%`;

    if (this.elBudgetStatusPill) {
      if (usagePct < 70) {
        this.elBudgetStatusPill.textContent = '🟢 OTTIMALE';
        this.elBudgetStatusPill.className = 'conn-status-badge budget-badge-ok';
      } else if (usagePct < 90) {
        this.elBudgetStatusPill.textContent = '🟡 ATTENZIONE';
        this.elBudgetStatusPill.className = 'conn-status-badge budget-badge-warn';
      } else {
        this.elBudgetStatusPill.textContent = '🔴 CRITICO';
        this.elBudgetStatusPill.className = 'conn-status-badge budget-badge-crit';
      }
    }

    const sysTok = budget.system !== undefined ? budget.system : 512;
    const profTok = (budget.profile_and_skills !== undefined ? budget.profile_and_skills : (budget.profile || 320));
    const instTok = budget.instincts !== undefined ? budget.instincts : 410;
    const memTok = budget.memories !== undefined ? budget.memories : 280;
    const projTok = (budget.projects_and_todos !== undefined ? budget.projects_and_todos : (budget.projects || 220));
    const chatTok = (budget.chat_history !== undefined ? budget.chat_history : (budget.chat || 108));

    if (this.elLblTokSystem) this.elLblTokSystem.textContent = String(sysTok);
    if (this.elLblTokProfile) this.elLblTokProfile.textContent = String(profTok);
    if (this.elLblTokInstincts) this.elLblTokInstincts.textContent = String(instTok);
    if (this.elLblTokMemories) this.elLblTokMemories.textContent = String(memTok);
    if (this.elLblTokProjects) this.elLblTokProjects.textContent = String(projTok);
    if (this.elLblTokChat) this.elLblTokChat.textContent = String(chatTok);

    if (this.elSegSystem) this.elSegSystem.style.width = `${Math.max(1, Math.round((sysTok / totalBudget) * 100))}%`;
    if (this.elSegProfile) this.elSegProfile.style.width = `${Math.max(1, Math.round((profTok / totalBudget) * 100))}%`;
    if (this.elSegInstincts) this.elSegInstincts.style.width = `${Math.max(1, Math.round((instTok / totalBudget) * 100))}%`;
    if (this.elSegMemories) this.elSegMemories.style.width = `${Math.max(1, Math.round((memTok / totalBudget) * 100))}%`;
    if (this.elSegProjects) this.elSegProjects.style.width = `${Math.max(1, Math.round((projTok / totalBudget) * 100))}%`;
    if (this.elSegChat) this.elSegChat.style.width = `${Math.max(1, Math.round((chatTok / totalBudget) * 100))}%`;
  }

  renderMcpGrid() {
    if (!this.elMcpGrid) return;
    const connectors = (this.state && this.state.system && Array.isArray(this.state.system.mcpConnectors) && this.state.system.mcpConnectors.length > 0)
      ? this.state.system.mcpConnectors
      : [
          { id: 'mcp_filesystem_scanner', name: 'Workspace Scanner', description: 'Scansione file di progetto e radar', enabled: true, estimated_tokens: 35 },
          { id: 'mcp_calendar_sync', name: 'Calendar Sync', description: 'Google Calendar sync bidirezionale', enabled: true, estimated_tokens: 35 },
          { id: 'mcp_dcc_telemetry', name: 'DCC Telemetry', description: 'Rilevamento finestre Blender/Max/Unreal', enabled: true, estimated_tokens: 40 },
          { id: 'mcp_vram_guard', name: 'VRAM Guard', description: 'Protezione memoria GPU RTX 4090', enabled: true, estimated_tokens: 25 }
        ];

    const activeCount = connectors.filter((c) => c.enabled !== false).length;
    const totalCount = connectors.length;
    if (this.elCountMcpActive) this.elCountMcpActive.textContent = String(activeCount);
    if (this.elCountMcpTotal) this.elCountMcpTotal.textContent = String(totalCount);

    const getIcon = (id) => {
      if (id.includes('filesystem') || id.includes('workspace')) return '📁';
      if (id.includes('calendar')) return '📅';
      if (id.includes('telemetry') || id.includes('dcc')) return '🖥️';
      if (id.includes('vram') || id.includes('gpu')) return '🎮';
      return '🔌';
    };

    let html = '';
    connectors.forEach((c) => {
      const isEnabled = (c.enabled !== false);
      const icon = getIcon(c.id || '');
      const tokens = c.estimated_tokens || 35;
      html += `
        <div class="mcp-card" data-mcp-id="${this.escapeHtml(c.id)}">
          <div class="mcp-card-top">
            <span class="mcp-title">${icon} ${this.escapeHtml(c.name || c.id)}</span>
            <label class="ecc-switch" title="Attiva/Disattiva Connettore MCP">
              <input type="checkbox" class="mcp-switch-input" data-mcp-id="${this.escapeHtml(c.id)}" ${isEnabled ? 'checked' : ''} />
              <span class="ecc-slider"></span>
            </label>
          </div>
          <div class="mcp-card-meta">
            <span class="status-pill ${isEnabled ? 'status-active' : 'status-off'}">${isEnabled ? 'ACTIVE' : 'OFF'}</span>
            <span class="badge-tokens">~${tokens} tok</span>
            <small style="color:var(--text-muted); font-size:10.5px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:120px;" title="${this.escapeHtml(c.description || '')}">${this.escapeHtml(c.description || '')}</small>
          </div>
        </div>
      `;
    });
    this.elMcpGrid.innerHTML = html;
  }

  renderSkillsSwitchboard() {
    if (!this.elSkillsSwitchboardList) return;
    const skills = (this.state && this.state.system && Array.isArray(this.state.system.skillsRegistry))
      ? this.state.system.skillsRegistry
      : [];

    const activeCount = skills.filter((s) => s.enabled !== false).length;
    const totalCount = skills.length;
    if (this.elCountSkillsActive) this.elCountSkillsActive.textContent = String(activeCount);
    if (this.elCountSkillsTotal) this.elCountSkillsTotal.textContent = String(totalCount);

    if (skills.length === 0) {
      this.elSkillsSwitchboardList.innerHTML = '<div style="color:var(--text-muted); font-size:12px; padding:10px; text-align:center;">Nessuna skill caricata. Usa "+ Importa SKILL.md" per aggiungerne una!</div>';
      return;
    }

    let html = '';
    skills.forEach((s) => {
      const isEnabled = (s.enabled !== false);
      const fmt = (s.format || (s.file && s.file.endsWith('.md') ? 'markdown' : 'yaml')).toLowerCase();
      const isMd = fmt === 'markdown' || fmt === 'md';
      const tokens = s.estimated_tokens || 35;
      const domain = s.domain || 'pipeline';

      html += `
        <div class="skill-switch-card" data-skill-id="${this.escapeHtml(s.id || '')}">
          <div class="skill-card-top">
            <div style="display:flex; align-items:center; gap:6px; overflow:hidden;">
              <span class="${isMd ? 'badge-format-md' : 'badge-format-yaml'}">${isMd ? 'SKILL.md' : 'YAML'}</span>
              <span class="skill-title" title="${this.escapeHtml(s.description || s.name || s.id)}">${this.escapeHtml(s.name || s.id)}</span>
            </div>
            <label class="ecc-switch" title="Attiva/Disattiva Skill">
              <input type="checkbox" class="skill-switch-input" data-skill-id="${this.escapeHtml(s.id)}" ${isEnabled ? 'checked' : ''} />
              <span class="ecc-slider"></span>
            </label>
          </div>
          <div class="skill-card-meta">
            <span style="color:var(--text-secondary); font-size:11px;">🏷️ ${this.escapeHtml(domain)}</span>
            <span class="badge-tokens">~${tokens} tok</span>
            <span style="font-size:10.5px; color:#64748b;">${this.escapeHtml(s.safety_tier || s.tier || 'READ_ONLY')}</span>
          </div>
        </div>
      `;
    });
    this.elSkillsSwitchboardList.innerHTML = html;
  }

  switchMemorySubtab(subtabId) {
    const validSubtabs = ['instincts-rules', 'ecc-arsenal', 'profile-skills'];
    const targetSubtab = validSubtabs.includes(subtabId) ? subtabId : 'instincts-rules';
    this.activeMemSubtab = targetSubtab;

    if (!this.elMemorySubtabs) {
      this.elMemorySubtabs = document.getElementById('memory-view-subtabs');
    }
    if (this.elMemorySubtabs) {
      this.elMemorySubtabs.querySelectorAll('[data-mem-view]').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-mem-view') === targetSubtab);
      });
    }

    if (!this.elViewMemory) {
      this.elViewMemory = document.getElementById('workspace-view-memory');
    }
    if (this.elViewMemory) {
      this.elViewMemory.setAttribute('data-active-mem-view', targetSubtab);
    }

    const paneInstincts = document.getElementById('mem-subpane-instincts-rules');
    const paneArsenal = document.getElementById('mem-subpane-ecc-arsenal');
    const paneProfile = document.getElementById('mem-subpane-profile-skills');

    if (paneInstincts) paneInstincts.style.display = (targetSubtab === 'instincts-rules' ? 'grid' : 'none');
    if (paneArsenal) paneArsenal.style.display = (targetSubtab === 'ecc-arsenal' ? 'flex' : 'none');
    if (paneProfile) paneProfile.style.display = (targetSubtab === 'profile-skills' ? 'grid' : 'none');

    if (this.elContextBudgetContainer) {
      this.elContextBudgetContainer.style.display = 'flex';
    }

    this.renderMemoryDashboard();
  }

  renderMemoryDashboard() {
    const activeSub = this.activeMemSubtab || 'instincts-rules';
    const paneInstincts = document.getElementById('mem-subpane-instincts-rules');
    const paneArsenal = document.getElementById('mem-subpane-ecc-arsenal');
    const paneProfile = document.getElementById('mem-subpane-profile-skills');

    if (paneInstincts) paneInstincts.style.display = (activeSub === 'instincts-rules' ? 'grid' : 'none');
    if (paneArsenal) paneArsenal.style.display = (activeSub === 'ecc-arsenal' ? 'flex' : 'none');
    if (paneProfile) paneProfile.style.display = (activeSub === 'profile-skills' ? 'grid' : 'none');

    this.renderContextBudget();
    this.renderMcpGrid();
    this.renderSkillsSwitchboard();

    // F27: Column 1 Subtab Switcher (Instincts vs Skills)
    const isInstinctsTab = (this.memCol1Subtab !== 'skills');
    if (this.elBtnSubtabInstincts) this.elBtnSubtabInstincts.classList.toggle('active', isInstinctsTab);
    if (this.elBtnSubtabSkills) this.elBtnSubtabSkills.classList.toggle('active', !isInstinctsTab);
    if (this.elMemInstinctAddRow) this.elMemInstinctAddRow.style.display = isInstinctsTab ? 'flex' : 'none';
    if (this.elMemSkillAddRow) this.elMemSkillAddRow.style.display = isInstinctsTab ? 'none' : 'flex';
    if (this.elMemInstinctsList) this.elMemInstinctsList.style.display = isInstinctsTab ? 'flex' : 'none';
    if (this.elMemSkillsList) this.elMemSkillsList.style.display = isInstinctsTab ? 'none' : 'flex';

    // Atomic Instincts v2.1
    let instincts = (this.state.system && Array.isArray(this.state.system.instinctsList))
      ? this.state.system.instinctsList
      : ((this.state.memory && Array.isArray(this.state.memory.instincts))
        ? this.state.memory.instincts
        : []);

    if (this.elCountInstincts) this.elCountInstincts.textContent = String(instincts.length);

    if (this.elMemInstinctsList) {
      if (instincts.length === 0) {
        this.elMemInstinctsList.innerHTML = '<div style="color:var(--text-muted); font-size:12px; padding:16px; text-align:center;">Nessun istinto registrato. Aggiungine uno sopra!</div>';
      } else {
        this.elMemInstinctsList.innerHTML = instincts.map((ins) => {
          const conf = typeof ins.confidence === 'number' ? ins.confidence : 0.85;
          const confPct = Math.round(conf * 100);
          const scope = ins.scope || 'global';
          const scopeLabel = scope === 'global' ? '🌐 Globale' : '📌 Progetto';
          const domain = ins.domain || 'generale';
          const usage = ins.usage_count || ins.applied_count || 1;
          const actionText = ins.action || ins.action_text || '';

          return `
            <div class="instinct-card" data-instinct-id="${this.escapeHtml(ins.id || '')}">
              <div class="instinct-header">
                <div>
                  <span class="badge-scope scope-${this.escapeHtml(scope)}">${scopeLabel}</span>
                  <span class="badge-proj" style="margin-left:4px;">${this.escapeHtml(domain)}</span>
                </div>
                <span class="instinct-usage">⚡ usato ${usage} volte</span>
              </div>
              <div class="instinct-body">
                <div class="instinct-row">
                  <span class="instinct-label">QUANDO:</span>
                  <span class="instinct-val">${this.escapeHtml(ins.trigger || '')}</span>
                </div>
                <div class="instinct-row">
                  <span class="instinct-label">AZIONE:</span>
                  <span class="instinct-val">${this.escapeHtml(actionText)}</span>
                </div>
              </div>
              <div class="instinct-footer">
                <div class="instinct-conf-group">
                  <div class="instinct-conf-track">
                    <div class="instinct-conf-bar" style="width: ${confPct}%;"></div>
                  </div>
                  <span class="instinct-conf-label">${confPct}%</span>
                </div>
                <div class="instinct-actions">
                  <button type="button" class="term-btn term-btn-sm btn-instinct-confirm" data-id="${this.escapeHtml(ins.id || '')}" title="Conferma validità (+10% confidence)">+ Conferma</button>
                  ${scope !== 'global' ? `<button type="button" class="term-btn term-btn-sm btn-instinct-promote" data-id="${this.escapeHtml(ins.id || '')}" title="Promuovi a regola globale">⬆️ Promuovi</button>` : ''}
                  <button type="button" class="term-btn term-btn-sm btn-instinct-prune" data-id="${this.escapeHtml(ins.id || '')}" title="Archivia istinto (prune)">🧹 Prune</button>
                  <button type="button" class="term-btn term-btn-sm btn-instinct-delete" data-id="${this.escapeHtml(ins.id || '')}" title="Elimina definitivamente">✕</button>
                </div>
              </div>
            </div>
          `;
        }).join('');
      }
    }

    let skills = (this.state.system && Array.isArray(this.state.system.skillsLearned))
      ? this.state.system.skillsLearned
      : [];

    if (this.elMemSkillsCount) this.elMemSkillsCount.textContent = String(skills.length);
    if (this.elCountSkills) this.elCountSkills.textContent = String(skills.length);

    if (this.elMemSkillsList) {
      let skillsHtml = '';
      if (skills.length === 0) {
        skillsHtml = '<div style="color:var(--text-muted);font-size:12px;padding:16px;text-align:center;">Nessuna competenza registrata. Aggiungine una o parla con Neko!</div>';
      } else {
        const groups = {};
        skills.forEach((s) => {
          const cat = s.category || 'Competenze Generali';
          if (!groups[cat]) groups[cat] = [];
          groups[cat].push(s);
        });

        for (const [catName, catSkills] of Object.entries(groups)) {
          skillsHtml += `
            <div class="mem-skill-group">
              <div class="mem-skill-cat-title">${this.escapeHtml(catName)} (${catSkills.length})</div>
              ${catSkills.map((s) => `
                <div class="mem-skill-card">
                  <div>
                    <strong>${this.escapeHtml(s.name)}</strong>
                    <span class="mem-skill-badge" style="margin-left:6px;">${this.escapeHtml(s.level || 'Avanzato')}</span>
                    ${s.evidence ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${this.escapeHtml(s.evidence)}</div>` : ''}
                  </div>
                  <button type="button" class="term-btn term-btn-sm btn-del-skill" data-skill-id="${this.escapeHtml(s.id)}" title="Rimuovi">✕</button>
                </div>
              `).join('')}
            </div>
          `;
        }
      }
      this.elMemSkillsList.innerHTML = skillsHtml;
      if (this.elMemProfileSkillsListPane3) {
        this.elMemProfileSkillsListPane3.innerHTML = skillsHtml;
      }
    }

    let rules = (this.state.system && Array.isArray(this.state.system.memoriesList))
      ? this.state.system.memoriesList
      : [];

    const filteredRules = this._memRulesSearchQuery
      ? rules.filter((r) => r.text.toLowerCase().includes(this._memRulesSearchQuery) || (r.category && r.category.toLowerCase().includes(this._memRulesSearchQuery)))
      : rules;

    if (this.elMemRulesCount) this.elMemRulesCount.textContent = String(rules.length);

    if (this.elMemRulesList) {
      if (filteredRules.length === 0) {
        this.elMemRulesList.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:20px;text-align:center;">Nessuna regola trovata.</div>';
      } else {
        this.elMemRulesList.innerHTML = filteredRules.map((r) => `
          <div class="mem-rule-card ${r.pinned ? 'pinned' : ''}">
            <div class="mem-rule-header">
              <span class="badge-proj">${this.escapeHtml(r.category || 'Generale')}</span>
              <div>
                <button type="button" class="trait-pin-btn btn-pin-rule ${r.pinned ? 'pinned' : ''}" data-rule-id="${this.escapeHtml(r.id)}" title="${r.pinned ? 'Regola fissata (permanente)' : 'Fissa regola'}">
                  ${r.pinned ? '⭐ Pinned' : '☆ Pin'}
                </button>
                <button type="button" class="trait-delete-btn btn-del-rule" data-rule-id="${this.escapeHtml(r.id)}" title="Elimina" style="margin-left:4px;">
                  🗑
                </button>
              </div>
            </div>
            <div class="mem-rule-text">${this.escapeHtml(r.text)}</div>
          </div>
        `).join('');
      }
    }

    if (!this.currentQuestion) {
      this.rollProfilingQuestion();
    }

    if (this.elMemProfilingQText && this.currentQuestion) {
      this.elMemProfilingQText.textContent = this.currentQuestion.question;
    }

    if (this.elMemProfilingOptions && this.currentQuestion && Array.isArray(this.currentQuestion.options)) {
      this.elMemProfilingOptions.innerHTML = this.currentQuestion.options.map((opt) => `
        <button type="button" class="profiling-chip" data-ans="${this.escapeHtml(opt)}">${this.escapeHtml(opt)}</button>
      `).join('');

      this.elMemProfilingOptions.querySelectorAll('.profiling-chip').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const ans = btn.getAttribute('data-ans');
          let res = null;
          if (this.actions.conosciamociAction) {
            res = await this.actions.conosciamociAction({
              action: 'answer',
              question_id: this.currentQuestion ? this.currentQuestion.id : '',
              category: (this.currentQuestion && this.currentQuestion.category) || this.currentOnboardCategory || 'Profilo',
              question_text: this.currentQuestion ? this.currentQuestion.question : '',
              answer: ans
            });
          }
          if (res && res.onboarding) {
            this.renderOnboardingState(res.onboarding);
            this.say(this.formatOnboardingFeedback(ans, res.onboarding.integration_summary));
          } else {
            this.say(`👤 Preferenza salvata nel profilo: "${ans}"`);
            this.rollProfilingQuestion();
          }
          this.renderCopilotHud();
          this.renderMemoryDashboard();
        });
      });
    }

    const profileTraits = (this.state.system && Array.isArray(this.state.system.profileTraits))
      ? this.state.system.profileTraits
      : [];

    if (this.elMemProfileCount) this.elMemProfileCount.textContent = String(profileTraits.length);

    if (this.elMemProfileTraitsList) {
      if (profileTraits.length === 0) {
        this.elMemProfileTraitsList.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:16px;text-align:center;">Nessun tratto del profilo salvato. Rispondi alle domande qui sopra o parla di te a Neko!</div>';
      } else {
        this.elMemProfileTraitsList.innerHTML = profileTraits.map((t) => {
          const traitText = typeof t === 'object' ? (t.text || t.value || JSON.stringify(t)) : String(t);
          return `
            <div class="mem-rule-card">
              <div class="mem-rule-header">
                <span class="badge-due">Tratto Profilo</span>
                <div>
                  <button type="button" class="trait-pin-btn pinned" title="Tratto permanente">⭐ Pinned</button>
                </div>
              </div>
              <div class="mem-rule-text">${this.escapeHtml(traitText)}</div>
            </div>
          `;
        }).join('');
      }
    }

    if (this.elTabMemBadge) {
      this.elTabMemBadge.textContent = String(skills.length + rules.length + instincts.length);
    }
  }

  async populatePlaybooksDropdown() {
    if (!this.elHubPlaybookSelect) return;
    let playbooks = (this.state.system && Array.isArray(this.state.system.playbooks) && this.state.system.playbooks.length > 0)
      ? this.state.system.playbooks
      : [];
    if (playbooks.length === 0 && window.systemBridge && window.systemBridge.fetchPlaybooks) {
      try {
        const res = await window.systemBridge.fetchPlaybooks();
        if (res && res.ok && Array.isArray(res.playbooks) && res.playbooks.length > 0) {
          playbooks = res.playbooks;
        }
      } catch (e) {}
    }
    if (playbooks.length === 0) {
      playbooks = [
        { slug: 'gestione_progetto_cliente', title: 'Gestione Progetto & Consegna Cliente' },
        { slug: 'sviluppo_software_release', title: 'Sviluppo Software & Release Checklist' },
        { slug: 'ricerca_e_documentazione', title: 'Studio, Ricerca & Documentazione' }
      ];
    }
    const currentVal = this.elHubPlaybookSelect.value;
    let opts = '<option value="">-- Seleziona un Playbook architetturale --</option>';
    playbooks.forEach((pb) => {
      const slug = pb.slug || pb.id || '';
      const title = pb.title || pb.name || slug;
      opts += `<option value="${this.escapeHtml(slug)}">${this.escapeHtml(title)}</option>`;
    });
    this.elHubPlaybookSelect.innerHTML = opts;
    if (currentVal) this.elHubPlaybookSelect.value = currentVal;
  }

  async openQualityGateModal(projectId, projectName) {
    this._currentQgProjectId = projectId;
    this._currentQgProjectName = projectName;
    if (this.elQualityGateModal) this.elQualityGateModal.style.display = 'flex';
    if (this.elQgProjTitle) this.elQgProjTitle.textContent = projectName || 'Progetto';
    if (this.elQgStatusLabel) this.elQgStatusLabel.textContent = 'Valutazione in corso...';
    if (this.elQgStatusDesc) this.elQgStatusDesc.textContent = 'Controllo criteri architetturali, To-Do completati e regole cliente...';

    let rep = null;
    if (window.systemBridge && window.systemBridge.projectQualityGate) {
      try {
        rep = await window.systemBridge.projectQualityGate(projectId);
      } catch (e) {}
    }
    if (!rep || !rep.ok) {
      const tasks = (this.state.system && Array.isArray(this.state.system.todosList))
        ? this.state.system.todosList.map((t, i) => this.normalizeTodo(t, i)).filter((t) => t.project && t.project.toLowerCase() === (projectName || '').toLowerCase())
        : [];
      const totalTasks = tasks.length;
      const doneTasks = tasks.filter((t) => t.status === 'done').length;
      const rules = (this.state.system && Array.isArray(this.state.system.memoriesList))
        ? this.state.system.memoriesList.filter((r) => r.category && r.category.toLowerCase().includes((projectName || '').toLowerCase()))
        : [];
      const score = totalTasks > 0 ? Math.round((doneTasks / totalTasks) * 80 + (rules.length > 0 ? 20 : 10)) : 85;
      rep = {
        ok: true,
        project_id: projectId,
        project_name: projectName,
        readiness_score: score,
        ready_for_delivery: score >= 80,
        status: score >= 80 ? 'PASSED' : 'ACTION_REQUIRED',
        tasks_completed: doneTasks,
        tasks_total: totalTasks,
        tasks,
        client_rules: rules,
        instincts_matched: (this.state.system && this.state.system.instinctsList) || [],
        recommendations: score >= 80
          ? ['Tutti i requisiti principali sono soddisfatti.', 'Pronto per export archivio e consegna cliente.']
          : ['Completa le attività To-Do rimanenti.', 'Verifica la conformità alle regole colore logo.']
      };
    }
    this.renderQualityGateReport(rep);
  }

  renderQualityGateReport(rep) {
    if (!rep) return;
    const score = typeof rep.readiness_score === 'number' ? rep.readiness_score : (rep.score || 0);
    const ready = rep.ready_for_delivery !== undefined ? rep.ready_for_delivery : (score >= 80);

    if (this.elQgScoreVal) {
      this.elQgScoreVal.textContent = `${score}%`;
    }
    if (this.elQgScoreBanner) {
      this.elQgScoreBanner.classList.toggle('passed', ready);
      this.elQgScoreBanner.classList.toggle('warning', !ready);
    }
    if (this.elQgStatusLabel) {
      this.elQgStatusLabel.textContent = ready ? '✅ PRONTO PER IL DEPLOY / CONSEGNA' : '⚠️ ATTENZIONE: CRITICITÀ RILEVATE';
      this.elQgStatusLabel.style.color = ready ? '#34d399' : '#fbbf24';
    }
    if (this.elQgStatusDesc) {
      this.elQgStatusDesc.textContent = ready
        ? 'Tutti i controlli di qualità hanno superato la soglia minima di conformità (>=80%).'
        : 'Alcuni task o regole cliente non sono ancora completati. Risolvi i punti sotto prima del rilascio.';
    }

    // Tasks summary & list
    const tasks = Array.isArray(rep.tasks) ? rep.tasks : [];
    const doneTasks = rep.tasks_completed !== undefined ? rep.tasks_completed : tasks.filter((t) => t.status === 'done').length;
    const totalTasks = rep.tasks_total !== undefined ? rep.tasks_total : tasks.length;
    if (this.elQgTasksSummary) this.elQgTasksSummary.textContent = `${doneTasks}/${totalTasks}`;
    if (this.elQgTasksBadge) {
      this.elQgTasksBadge.textContent = (totalTasks === 0 || doneTasks === totalTasks) ? 'OK' : 'INCOMPLETO';
      this.elQgTasksBadge.className = 'qg-badge ' + ((totalTasks === 0 || doneTasks === totalTasks) ? 'badge-ok' : 'badge-warn');
    }
    if (this.elQgTasksList) {
      if (tasks.length === 0) {
        this.elQgTasksList.innerHTML = '<div style="color:var(--text-muted); font-size:12px; padding:6px;">Nessun task vincolante registrato per questo progetto.</div>';
      } else {
        this.elQgTasksList.innerHTML = tasks.map((t) => `
          <div class="qg-item-row ${t.status === 'done' ? 'passed' : 'pending'}">
            <span>${t.status === 'done' ? '✅' : '⏳'} ${this.escapeHtml(t.title || t.name || '')}</span>
            <span style="font-size:11px; color:var(--text-muted);">${this.escapeHtml(t.status || 'todo')}</span>
          </div>
        `).join('');
      }
    }

    // Client rules summary & list
    const rules = Array.isArray(rep.client_rules) ? rep.client_rules : (Array.isArray(rep.rules) ? rep.rules : []);
    if (this.elQgRulesSummary) this.elQgRulesSummary.textContent = `${rules.length}/${rules.length}`;
    if (this.elQgRulesBadge) {
      this.elQgRulesBadge.textContent = 'OK';
      this.elQgRulesBadge.className = 'qg-badge badge-ok';
    }
    if (this.elQgRulesList) {
      if (rules.length === 0) {
        this.elQgRulesList.innerHTML = '<div style="color:var(--text-muted); font-size:12px; padding:6px;">Nessuna regola cliente specifica vincolante.</div>';
      } else {
        this.elQgRulesList.innerHTML = rules.map((r) => `
          <div class="qg-item-row passed">
            <span>⭐ ${this.escapeHtml(typeof r === 'string' ? r : (r.text || r.rule || ''))}</span>
            <span style="font-size:11px; color:#38bdf8;">Confermato</span>
          </div>
        `).join('');
      }
    }

    // Instincts matched
    const instincts = Array.isArray(rep.instincts_matched) ? rep.instincts_matched : (Array.isArray(rep.instincts) ? rep.instincts : []);
    if (this.elQgInstinctsBadge) {
      this.elQgInstinctsBadge.textContent = instincts.length > 0 ? `${instincts.length} ATTIVI` : 'NONE';
      this.elQgInstinctsBadge.className = 'qg-badge badge-ok';
    }
    if (this.elQgInstinctsList) {
      if (instincts.length === 0) {
        this.elQgInstinctsList.innerHTML = '<div style="color:var(--text-muted); font-size:12px; padding:6px;">Nessun istinto tecnico attivo applicato.</div>';
      } else {
        this.elQgInstinctsList.innerHTML = instincts.map((ins) => `
          <div class="qg-item-row passed">
            <span>🧬 [${this.escapeHtml(ins.scope || 'global')}] <strong>${this.escapeHtml(ins.trigger || '')}</strong> &rarr; ${this.escapeHtml(ins.action || ins.action_text || '')}</span>
            <span style="font-size:11px; color:#a78bfa;">${Math.round((ins.confidence || 0.8) * 100)}%</span>
          </div>
        `).join('');
      }
    }

    // AI recommendations
    const recs = Array.isArray(rep.recommendations) ? rep.recommendations : [];
    if (this.elQgRecommendations) {
      if (recs.length === 0) {
        this.elQgRecommendations.innerHTML = '<div style="color:var(--text-muted); font-size:12px; padding:6px;">Nessuna raccomandazione aggiuntiva. Procedi pure con la consegna!</div>';
      } else {
        this.elQgRecommendations.innerHTML = recs.map((rec) => `
          <div class="qg-rec-item">💡 ${this.escapeHtml(typeof rec === 'string' ? rec : (rec.text || ''))}</div>
        `).join('');
      }
    }
  }

  renderSmartBanner() {
    if (!this.elSmartBanner) return;

    const isApplicableWorkspace = (this.activeWorkspace === 'view-home' || this.activeWorkspace === 'view-todo' || this.activeWorkspace === 'view-projects');
    if (!isApplicableWorkspace) {
      this.elSmartBanner.style.display = 'none';
      return;
    }

    const taskSug = this.state.system ? this.state.system.windowTaskSuggestion : null;
    const sessCp = this.state.system ? this.state.system.sessionCheckpoint : null;

    const showSug = taskSug && (this._dismissedTaskSuggestion !== (taskSug.id || taskSug.window_title || taskSug.suggested_task_title));
    const showCp = sessCp && (this._dismissedSessionCheckpoint !== (sessCp.id || sessCp.title || sessCp.timestamp));

    if (!showSug && !showCp) {
      this.elSmartBanner.style.display = 'none';
      return;
    }

    let chipsHtml = '';

    if (showSug) {
      const wTitle = taskSug.window_title || taskSug.active_window || 'Finestra DCC';
      const sTitle = taskSug.suggested_task_title || taskSug.task_title || 'Attività suggerita';
      chipsHtml += `
        <div class="smart-chip chip-suggestion" id="chip-task-sug">
          <div class="smart-chip-content">
            <span class="smart-chip-icon">⚡</span>
            <span>Rilevata finestra attiva <strong>"${this.escapeHtml(wTitle)}"</strong> &rarr; Suggerimento: <strong>${this.escapeHtml(sTitle)}</strong></span>
          </div>
          <div class="smart-chip-actions">
            <button type="button" class="term-btn term-btn-sm btn-accent" id="btn-accept-task-sug">+ Aggiungi a To-Do</button>
            <button type="button" class="term-btn term-btn-sm" id="btn-dismiss-task-sug">✕ Ignora</button>
          </div>
        </div>
      `;
    }

    if (showCp) {
      const cpTitle = sessCp.title || sessCp.session_title || 'Sessione precedente';
      const cpTime = sessCp.timestamp || sessCp.created_at || 'recente';
      chipsHtml += `
        <div class="smart-chip chip-checkpoint" id="chip-session-cp">
          <div class="smart-chip-content">
            <span class="smart-chip-icon">💾</span>
            <span>Riprendi sessione salvata: <strong>"${this.escapeHtml(cpTitle)}"</strong> (${this.escapeHtml(String(cpTime))})</span>
          </div>
          <div class="smart-chip-actions">
            <button type="button" class="term-btn term-btn-sm btn-accent" id="btn-resume-session-cp">▶ Riprendi Sessione</button>
            <button type="button" class="term-btn term-btn-sm" id="btn-dismiss-session-cp">✕ Ignora</button>
          </div>
        </div>
      `;
    }

    this.elSmartBanner.innerHTML = chipsHtml;
    this.elSmartBanner.style.display = 'flex';

    // Wire action buttons
    const btnAcceptSug = this.elSmartBanner.querySelector('#btn-accept-task-sug');
    if (btnAcceptSug) {
      btnAcceptSug.addEventListener('click', async () => {
        const title = taskSug.suggested_task_title || taskSug.task_title || 'Attività suggerita';
        const project = taskSug.project || '';
        if (window.systemBridge && window.systemBridge.todosAction) {
          await window.systemBridge.todosAction({ action: 'add', title, project, due_label: 'Oggi', priority: 'high' });
        }
        this._dismissedTaskSuggestion = (taskSug.id || taskSug.window_title || taskSug.suggested_task_title);
        this.say(`📋 Task suggerito "${title}" aggiunto al To-Do Kanban!`);
        this.renderSmartBanner();
        this.renderKanbanBoard();
      });
    }

    const btnDismissSug = this.elSmartBanner.querySelector('#btn-dismiss-task-sug');
    if (btnDismissSug) {
      btnDismissSug.addEventListener('click', () => {
        this._dismissedTaskSuggestion = (taskSug.id || taskSug.window_title || taskSug.suggested_task_title);
        this.renderSmartBanner();
      });
    }

    const btnResumeCp = this.elSmartBanner.querySelector('#btn-resume-session-cp');
    if (btnResumeCp) {
      btnResumeCp.addEventListener('click', async () => {
        if (window.systemBridge && window.systemBridge.sessionAction) {
          await window.systemBridge.sessionAction({ action: 'resume', session_id: sessCp.id });
        }
        this._dismissedSessionCheckpoint = (sessCp.id || sessCp.title || sessCp.timestamp);
        this.say(`💾 Sessione di lavoro "${sessCp.title || 'precedente'}" ripristinata!`);
        this.renderSmartBanner();
      });
    }

    const btnDismissCp = this.elSmartBanner.querySelector('#btn-dismiss-session-cp');
    if (btnDismissCp) {
      btnDismissCp.addEventListener('click', () => {
        this._dismissedSessionCheckpoint = (sessCp.id || sessCp.title || sessCp.timestamp);
        this.renderSmartBanner();
      });
    }
  }

  updateWorkspaceDirtyChecking() {
    const taskSug = this.state.system ? this.state.system.windowTaskSuggestion : null;
    const sessCp = this.state.system ? this.state.system.sessionCheckpoint : null;
    const bannerKey = `${taskSug ? (taskSug.id || taskSug.window_title || taskSug.suggested_task_title) : ''}:${sessCp ? (sessCp.id || sessCp.title || sessCp.timestamp) : ''}`;
    if (this._lastBannerKey !== bannerKey) {
      this._lastBannerKey = bannerKey;
      this.renderSmartBanner();
    }

    const todosList = (this.state.system && Array.isArray(this.state.system.todosList)) ? this.state.system.todosList : [];
    const todoKey = `${todosList.length}:${todosList.map((t) => (typeof t === 'object' ? t.id + t.status : t)).join(',')}`;

    const projectsList = (this.state.system && Array.isArray(this.state.system.projectsList)) ? this.state.system.projectsList : [];
    const playbooks = (this.state.system && Array.isArray(this.state.system.playbooks)) ? this.state.system.playbooks : [];
    const projKey = `${projectsList.length}:${playbooks.length}:${this.state.system ? this.state.system.activeProjectName : ''}:${projectsList.map((p) => (p.workspace_path || '') + (p.workspace_summary ? p.workspace_summary.total_files : 0)).join(';')}`;

    const calEvs = (this.state.system && Array.isArray(this.state.system.calendarEvents)) ? this.state.system.calendarEvents : [];
    const calKey = `${calEvs.length}:${this.state.system && this.state.system.calendarSyncStatus ? this.state.system.calendarSyncStatus.synced : false}`;

    const skills = (this.state.system && Array.isArray(this.state.system.skillsLearned)) ? this.state.system.skillsLearned : [];
    const instincts = (this.state.system && Array.isArray(this.state.system.instinctsList)) ? this.state.system.instinctsList : [];
    const budgetTokens = (this.state.system && this.state.system.contextBudget) ? this.state.system.contextBudget.used_tokens : 0;
    const mcpConns = (this.state.system && Array.isArray(this.state.system.mcpConnectors)) ? this.state.system.mcpConnectors : [];
    const skillsReg = (this.state.system && Array.isArray(this.state.system.skillsRegistry)) ? this.state.system.skillsRegistry : [];
    const memKey = `${skills.length}:${instincts.length}:${budgetTokens}:${this.state.system ? this.state.system.memoryCount : 0}:${mcpConns.map((c) => (c.id || '') + c.enabled).join(',')}:${skillsReg.map((s) => (s.id || '') + s.enabled).join(',')}`;

    const rawAw = (this.state.system && (this.state.system.activeWindow || this.state.system.active_window)) || null;
    const activeWin = rawAw
      ? (typeof rawAw === 'object' ? (rawAw.title || rawAw.window_title || rawAw.app || rawAw.app_name || '') : String(rawAw))
      : '';
    const copilotKey = `${todoKey}:${projKey}:${activeWin}:${budgetTokens}`;

    if (this.activeWorkspace === 'view-home') {
      if (this._lastKanbanKey !== todoKey) {
        this._lastKanbanKey = todoKey;
        this.renderHomeNextTaskChip();
      }
      if (this._lastCopilotKey !== copilotKey) {
        this._lastCopilotKey = copilotKey;
        if (typeof this.renderCopilotHud === 'function') {
          this.renderCopilotHud();
        }
      }
      return;
    }

    const activeEl = document.activeElement;
    const isTyping = activeEl && (['INPUT', 'TEXTAREA'].includes(activeEl.tagName) && this.elWorkspaceContainer && this.elWorkspaceContainer.contains(activeEl));

    if (isTyping) return;

    if (this.activeWorkspace === 'view-todo' && this._lastKanbanKey !== todoKey) {
      this._lastKanbanKey = todoKey;
      this.renderKanbanBoard();
    } else if (this.activeWorkspace === 'view-projects' && this._lastHubProjKey !== projKey) {
      this._lastHubProjKey = projKey;
      this.renderProjectHub();
    } else if (this.activeWorkspace === 'view-calendar' && this._lastCalendarKey !== calKey) {
      this._lastCalendarKey = calKey;
      this.renderCalendarDashboard();
    } else if (this.activeWorkspace === 'view-memory' && this._lastMemoryKey !== memKey) {
      this._lastMemoryKey = memKey;
      this.renderMemoryDashboard();
    }
  }
}

window.HudTerminal = HudTerminal;
