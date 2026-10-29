/**
 * SystemBridge - Gestisce il collegamento real-time (25-30 FPS), la sincronizzazione Multi-Monitor
 * e la comunicazione con il bridge locale (Ollama, Cronologia Chat, Google Calendar, Azioni PC).
 */
class SystemBridge {
  constructor(state, onAiStateChange, onRemoteCommand) {
    this.state = state;
    window.systemBridge = this;
    this.onAiStateChange = onAiStateChange || (() => {});
    this.onRemoteCommand = onRemoteCommand || (() => {});

    this.clientId = 'scr_' + Math.random().toString(36).slice(2, 9);
    this.streamUrl = `http://127.0.0.1:8765/stream?client_id=${this.clientId}`;
    this.statusUrl = `http://127.0.0.1:8765/status?client_id=${this.clientId}`;
    this.syncUrl = 'http://127.0.0.1:8765/cat_sync';

    this.bridgeConnected = false;
    this.sseActive = false;
    this.lastAiWorking = false;
    this.lastCmdId = 0;

    this.demoAudioActive = false;
    this.spectrum = new Float32Array(24);
    this.state.spectrum = this.spectrum;
    this.rollingAvg = 0.05;

    if (!this.state.system) this.state.system = {};
    this.state.system.memoryCount = 0;
    this.state.system.todoCount = 0;
    this.state.system.calendarEvents = [];
    this.state.system.todoDeadlines = [];
    this.state.system.calendarSyncStatus = null;
    this.state.system.skillsLearned = [];
    this.state.system.projectsList = [];
    this.state.system.contextMode = 'operativo';
    this.state.system.contextBudget = null;
    this.state.system.instinctsList = [];
    this.state.system.windowTaskSuggestion = null;
    this.state.system.sessionCheckpoint = null;
    this.state.system.playbooks = [];
    this.state.system.mcpConnectors = [];
    this.state.system.skillsRegistry = [];
    if (!this.state.memory) this.state.memory = {};
    this.state.memory.instincts = [];
    this.state.system.calendar = {
      configured: false,
      ical_urls: [],
      today_count: 0,
      upcoming_count: 0,
      next_event: null,
      imminent_events: [],
      upcoming_events: []
    };

    // Canale Broadcast istantaneo se i due monitor condividono il processo browser
    try {
      this.bc = new BroadcastChannel('neko_multi_screen_v1');
      this.bc.onmessage = (ev) => {
        const msg = ev.data;
        if (!msg) return;
        if (msg.type === 'cat_state' && this.state.screenIndex !== 0) {
          this.state.sharedCat = msg.payload;
        } else if (msg.type === 'remote_cmd' && this.state.screenIndex === 0) {
          this.onRemoteCommand(msg.command);
        }
      };
    } catch (e) {
      this.bc = null;
    }

    this.initNativeHooks();
    this.connectRealtimeBridge();
  }

  initNativeHooks() {
    window.livelySystemInformation = (data) => {
      try {
        const info = typeof data === 'string' ? JSON.parse(data) : data;
        if (info.CurrentCpu !== undefined) this.state.system.cpu = Math.round(info.CurrentCpu);
        if (info.CurrentRam !== undefined && info.TotalRam) {
          this.state.system.ram = Math.round((info.CurrentRam / info.TotalRam) * 100);
        }
      } catch (e) {}
    };

    window.livelyAudioListener = (audioArray) => {
      if (!audioArray || !audioArray.length) return;
      this.processExternalSpectrum(audioArray);
    };

    if (window.wallpaperRegisterAudioListener) {
      window.wallpaperRegisterAudioListener((audioArray) => {
        if (!audioArray || !audioArray.length) return;
        this.processExternalSpectrum(audioArray);
      });
    }
  }

  processExternalSpectrum(arr) {
    let sum = 0;
    const step = Math.max(1, Math.floor(arr.length / 24));
    for (let i = 0; i < 24; i++) {
      const val = Math.min(1.0, Math.max(0, arr[i * step] || 0));
      sum += val;
    }
    const rawPeak = sum / 24;

    this.extSustained = this.extSustained || 0;
    if (rawPeak > 0.02) {
      this.extSustained = Math.min(10, this.extSustained + 0.05);
    } else {
      this.extSustained = Math.max(0, this.extSustained - 0.15);
    }

    if (this.extSustained < 2.0) {
      this.state.audioEnergy *= 0.85;
      this.state.audioBeat = 0;
      for (let i = 0; i < 24; i++) this.spectrum[i] *= 0.85;
      return;
    }

    for (let i = 0; i < 24; i++) {
      const val = Math.min(1.0, Math.max(0, arr[i * step] || 0));
      this.spectrum[i] = this.spectrum[i] * 0.75 + val * 0.25;
    }
    const peak = Math.min(0.85, rawPeak * 1.3);
    const diff = peak - this.rollingAvg;
    this.rollingAvg = this.rollingAvg * 0.9 + peak * 0.1;

    this.state.audioEnergy = this.state.audioEnergy * 0.8 + peak * 0.2;
    this.state.audioBeat = peak > 0.05 ? Math.min(0.7, Math.max(0, diff * 2.0)) : 0;
  }

  publishCatState(catPayload) {
    if (this.bc) {
      try {
        this.bc.postMessage({ type: 'cat_state', payload: catPayload });
      } catch (e) {}
    }
    const now = performance.now();
    if (!this._lastPostTime || now - this._lastPostTime > 55) {
      this._lastPostTime = now;
      fetch(this.syncUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(catPayload)
      }).catch(() => {});
    }
  }

  sendRemoteCommand(cmd) {
    if (this.bc) {
      try {
        this.bc.postMessage({ type: 'remote_cmd', command: cmd });
      } catch (e) {}
    }
    fetch(this.syncUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remoteCommand: cmd })
    }).catch(() => {});
  }

  async sendAiPrompt(promptText) {
    try {
      const res = await fetch('http://127.0.0.1:8765/ai_chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: promptText,
          model: this.state.system.ollamaActive || ''
        })
      });
      if (res.ok) {
        return await res.json();
      }
    } catch (e) {}
    return {
      reply: '⚠️ Il bridge Python non è raggiungibile su 127.0.0.1:8765. Avvia Avvia_Neko_App.bat!',
      model: 'OFFLINE',
      actions: []
    };
  }

  async fetchChatHistory() {
    try {
      const res = await fetch('http://127.0.0.1:8765/chat_history');
      if (res.ok) {
        return await res.json();
      }
    } catch (e) {}
    return { history: [], memories: [], todos: [], calendar: null };
  }

  async clearChatHistory() {
    try {
      const res = await fetch('http://127.0.0.1:8765/chat_clear', { method: 'POST' });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async saveCalendarConfig(icalUrl) {
    try {
      const res = await fetch('http://127.0.0.1:8765/calendar_config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ical_url: icalUrl })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async triggerPcAction(openPath) {
    try {
      const res = await fetch('http://127.0.0.1:8765/pc_action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ open_path: openPath })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async fetchAppData() {
    try {
      const res = await fetch('http://127.0.0.1:8765/app_data');
      if (res.ok) return await res.json();
    } catch (e) {}
    return null;
  }

  async conosciamociAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/conosciamoci', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.onboarding) {
          this.state.system.onboarding = data.onboarding;
          if (typeof data.onboarding.answered_count === 'number') {
            this.state.system.profileAnsweredCount = data.onboarding.answered_count;
          }
          if (data.onboarding.profile && Array.isArray(data.onboarding.profile.traits)) {
            this.state.system.profileTraits = data.onboarding.profile.traits;
          }
        }
        if (data && data.system_updates) {
          const su = data.system_updates;
          if (Array.isArray(su.skills_learned)) this.state.system.skillsLearned = su.skills_learned;
          if (Array.isArray(su.profile_traits)) this.state.system.profileTraits = su.profile_traits;
          if (Array.isArray(su.projects)) {
            this.state.system.projectsList = su.projects;
            this.state.system.projectsCount = su.projects.length;
          }
          if (typeof su.active_project_name === 'string') {
            this.state.system.activeProjectName = su.active_project_name;
          }
          if (Array.isArray(su.memories)) {
            this.state.system.memories = su.memories;
            this.state.system.memoriesList = su.memories;
            this.state.system.memoryCount = su.memories.length + (this.state.system.profileTraits ? this.state.system.profileTraits.length : 0);
          }
          if (Array.isArray(su.todos_list)) {
            this.state.system.todosList = su.todos_list;
            this.state.system.todoCount = su.todos_list.length;
          }
          if (Array.isArray(su.instincts)) {
            this.state.system.instinctsList = su.instincts;
            if (!this.state.memory) this.state.memory = {};
            this.state.memory.instincts = su.instincts;
          }
        }
        return data;
      }
    } catch (e) {}
    return { ok: false };
  }

  async projectsAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async saveQuickMemory(factText, projectId = '') {
    try {
      const res = await fetch('http://127.0.0.1:8765/save_quick_memory', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fact: factText, project_id: projectId })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async connectionsAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/connections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async setWindowMode(mode) {
    try {
      const res = await fetch('http://127.0.0.1:8765/window_mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async settingsAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async toggleColorSafe(enabled) {
    try {
      const body = typeof enabled === 'boolean' ? { enabled } : {};
      const res = await fetch('http://127.0.0.1:8765/api/colorsafe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && typeof data.color_safe_mode === 'boolean') {
          this.state.system.colorSafeMode = data.color_safe_mode;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false };
  }

  async pinProfileTrait(traitId, pinned = true) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/profile/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trait_id: traitId, pinned: Boolean(pinned) })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async editProfileTrait(traitId, text) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/profile/edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trait_id: traitId, text })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async deleteProfileTrait(traitId) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/profile/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ trait_id: traitId })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async fetchProfileData() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/profile', { cache: 'no-store' });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false, traits: [] };
  }

  async fetchSkills() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/skills', { cache: 'no-store' });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false, skills: [] };
  }

  async todosAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/todos/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false, error: 'Network error' };
  }

  async skillsLearnedAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/skills_learned/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false, error: 'Network error' };
  }

  async calendarAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/calendar/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false, error: 'Network error' };
  }

  async setContextMode(mode) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/context_mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && (data.context_mode || data.active_mode)) {
          this.state.system.contextMode = data.context_mode || data.active_mode;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false };
  }

  async instinctsAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/instincts/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.instincts)) {
          this.state.system.instinctsList = data.instincts;
          if (!this.state.memory) this.state.memory = {};
          this.state.memory.instincts = data.instincts;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false, error: 'Network error' };
  }

  async evolveProject(projectId) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/projects/evolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, id: projectId })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async projectEvolve(projectId) {
    return this.evolveProject(projectId);
  }

  async projectQualityGate(projectId) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/projects/quality_gate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId, id: projectId })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async createProjectFromPlaybook(playbookSlug, name, client) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/projects/from-playbook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          playbook_slug: playbookSlug,
          playbook_id: playbookSlug,
          slug: playbookSlug,
          project_name: name,
          name: name,
          client: client
        })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  async projectFromPlaybook(payload) {
    if (typeof payload === 'object') {
      const slug = payload.playbook_slug || payload.playbook_id || payload.slug;
      const name = payload.project_name || payload.name;
      const client = payload.client;
      return this.createProjectFromPlaybook(slug, name, client);
    }
    return { ok: false };
  }

  async fetchPlaybooks() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/playbooks', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.playbooks)) {
          this.state.system.playbooks = data.playbooks;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false, playbooks: [] };
  }

  async sessionAction(payload) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/session/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false };
  }

  // --- Milestone 4: Project Workspace Folder APIs (F18, F19, F25) ---
  async bindProjectFolder(projectId, path = '', pickNative = false) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/projects/bind_folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          workspace_path: path,
          pick_native: pickNative
        })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(this.state.system.projectsList)) {
          const target = this.state.system.projectsList.find((p) => p.id === projectId);
          if (target) {
            target.workspace_path = data.workspace_path || path;
            if (data.workspace_summary) target.workspace_summary = data.workspace_summary;
          }
        }
        return data;
      }
    } catch (e) {}
    return { ok: false, error: 'Network error' };
  }

  async openProjectFolder(projectId, path = '', dryRun = false) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/projects/open_folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          workspace_path: path,
          dry_run: dryRun
        })
      });
      if (res.ok) return await res.json();
    } catch (e) {}
    return { ok: false, error: 'Network error' };
  }

  async scanProjectFolder(projectId, force = false) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/projects/scan_folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          force: force
        })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && data.workspace_summary && Array.isArray(this.state.system.projectsList)) {
          const target = this.state.system.projectsList.find((p) => p.id === projectId);
          if (target) {
            target.workspace_summary = data.workspace_summary;
            if (data.workspace_path) target.workspace_path = data.workspace_path;
          }
        }
        return data;
      }
    } catch (e) {}
    return { ok: false, error: 'Network error' };
  }

  // --- Milestone 4: Skills & MCP Control Plane APIs (F20, F21, F22, F23, F24, F25) ---
  async toggleSkill(skillId, enabled) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/skills/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_id: skillId,
          id: skillId,
          enabled: enabled
        })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok) {
          if (data.context_budget) this.state.system.contextBudget = data.context_budget;
          if (Array.isArray(data.skills)) this.state.system.skillsRegistry = data.skills;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false };
  }

  async importSkill(skillData) {
    try {
      const payload = typeof skillData === 'object' ? skillData : { content: String(skillData), format: 'markdown' };
      const res = await fetch('http://127.0.0.1:8765/api/skills/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.skills)) {
          this.state.system.skillsRegistry = data.skills;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false };
  }

  async toggleMcp(connectorId, enabled) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/mcp/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: connectorId,
          connector_id: connectorId,
          enabled: enabled
        })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok) {
          if (data.context_budget) this.state.system.contextBudget = data.context_budget;
          if (Array.isArray(data.connectors)) this.state.system.mcpConnectors = data.connectors;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false };
  }

  async addMcp(mcpData) {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/mcp/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(mcpData || {})
      });
      if (res.ok) {
        const data = await res.json();
        if (data.ok) {
          if (data.context_budget) this.state.system.contextBudget = data.context_budget;
          if (Array.isArray(data.connectors)) this.state.system.mcpConnectors = data.connectors;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false };
  }

  async fetchMcpConnectors() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/mcp', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.connectors)) {
          this.state.system.mcpConnectors = data.connectors;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false, connectors: [] };
  }

  async fetchMcp() {
    return this.fetchMcpConnectors();
  }

  async fetchSkills() {
    try {
      const res = await fetch('http://127.0.0.1:8765/api/skills', { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (data.ok && Array.isArray(data.skills)) {
          this.state.system.skillsRegistry = data.skills;
        }
        return data;
      }
    } catch (e) {}
    return { ok: false, skills: [] };
  }

  applyBridgeData(data) {
    if (!data || typeof data !== 'object') return;

    this.bridgeConnected = true;
    if (!this.state) this.state = {};
    if (!this.state.system) this.state.system = {};
    this.state.system.bridgeOnline = true;

    if (typeof data.cpu === 'number') this.state.system.cpu = Math.round(data.cpu);
    if (typeof data.ram === 'number') this.state.system.ram = Math.round(data.ram);
    if (typeof data.gpu === 'number' || typeof data.gpu_percent === 'number') {
      this.state.system.gpu = Math.round(typeof data.gpu_percent === 'number' ? data.gpu_percent : data.gpu);
    }
    if (typeof data.vram_used_gb === 'number') this.state.system.vram_used_gb = data.vram_used_gb;
    if (typeof data.vram_total_gb === 'number') this.state.system.vram_total_gb = data.vram_total_gb;
    if (typeof data.vram_percent === 'number') this.state.system.vram_percent = data.vram_percent;

    if (data.active_window) {
      this.state.system.active_window = data.active_window;
      if (typeof data.active_window === 'object') {
        this.state.system.activeWindow = {
          app: data.active_window.app || data.active_window.app_name || '',
          title: data.active_window.title || data.active_window.window_title || '',
          ...data.active_window
        };
      }
    }
    if (typeof data.is_afk === 'boolean') this.state.system.is_afk = data.is_afk;
    if (data.afk) this.state.system.afk = data.afk;
    if (typeof data.afk_idle_seconds === 'number') this.state.system.afk_idle_seconds = data.afk_idle_seconds;
    if (typeof data.color_safe_mode === 'boolean') this.state.system.colorSafeMode = data.color_safe_mode;

    this.state.system.aiWorking = Boolean(data.ai_working);
    this.state.system.aiSource = data.ai_source || 'IDLE';
    this.state.system.aiDetail = data.ai_detail || '';
    if (typeof data.is_desktop_focus === 'boolean') {
      this.state.system.isDesktopFocus = data.is_desktop_focus;
    }
    if (data.ollama_active) {
      this.state.system.ollamaActive = data.ollama_active;
    }
    if (Array.isArray(data.ollama_models)) {
      this.state.system.ollamaModels = data.ollama_models;
    }
    if (typeof data.ollama_online === 'boolean') {
      this.state.system.ollamaOnline = data.ollama_online;
    }
    if (data.ollama_pull) {
      this.state.system.ollamaPull = data.ollama_pull;
    }

    if (typeof data.memory_count === 'number') {
      this.state.system.memoryCount = data.memory_count;
    }
    if (typeof data.todo_count === 'number') {
      this.state.system.todoCount = data.todo_count;
    }
    if (Array.isArray(data.todos_list)) {
      this.state.system.todosList = data.todos_list;
    }
    if (typeof data.projects_count === 'number') {
      this.state.system.projectsCount = data.projects_count;
    }
    if (typeof data.archive_count === 'number') {
      this.state.system.archiveCount = data.archive_count;
    }
    if (typeof data.stale_projects_count === 'number') {
      this.state.system.staleProjectsCount = data.stale_projects_count;
    }
    if (typeof data.active_project_name === 'string') {
      this.state.system.activeProjectName = data.active_project_name;
    }
    if (typeof data.profile_answered_count === 'number') {
      this.state.system.profileAnsweredCount = data.profile_answered_count;
    }
    if (typeof data.hotkey === 'string') {
      this.state.system.hotkey = data.hotkey;
    }
    if (typeof data.window_mode === 'string') {
      this.state.system.windowMode = data.window_mode;
    }
    if (typeof data.focus_input_seq === 'number') {
      this.state.system.focusInputSeq = data.focus_input_seq;
    }
    if (data.calendar) {
      this.state.system.calendar = data.calendar;
    }
    if (Array.isArray(data.calendar_events)) {
      this.state.system.calendarEvents = data.calendar_events;
    }
    if (Array.isArray(data.todo_deadlines)) {
      this.state.system.todoDeadlines = data.todo_deadlines;
    }
    if (data.calendar_sync_status) {
      this.state.system.calendarSyncStatus = data.calendar_sync_status;
    }
    if (Array.isArray(data.skills_learned)) {
      this.state.system.skillsLearned = data.skills_learned;
    }
    if (Array.isArray(data.profile_traits)) {
      this.state.system.profileTraits = data.profile_traits;
    }
    if (Array.isArray(data.memories)) {
      this.state.system.memories = data.memories;
      this.state.system.memoriesList = data.memories;
    }
    if (Array.isArray(data.projects)) {
      this.state.system.projectsList = data.projects;
    }
    if (data.context_mode || data.active_context_mode) {
      this.state.system.contextMode = data.context_mode || data.active_context_mode;
    }
    if (data.context_budget) {
      this.state.system.contextBudget = data.context_budget;
    }
    if (Array.isArray(data.instincts)) {
      this.state.system.instinctsList = data.instincts;
      if (!this.state.memory) this.state.memory = {};
      this.state.memory.instincts = data.instincts;
    }
    if (data.window_task_suggestion !== undefined) {
      this.state.system.windowTaskSuggestion = data.window_task_suggestion;
    }
    if (data.session_checkpoint !== undefined) {
      this.state.system.sessionCheckpoint = data.session_checkpoint;
    }
    if (Array.isArray(data.playbooks)) {
      this.state.system.playbooks = data.playbooks;
    }
    // Hydrate connectors from object with mcp_connectors, object with connectors, or raw array
    if (Array.isArray(data)) {
      if (data.length > 0) {
        const first = data[0];
        const idStr = first && typeof first === 'object' ? String(first.id || '').toLowerCase() : '';
        if (first && (first.estimated_tokens !== undefined || first.type === 'builtin' || first.type === 'server' || idStr.startsWith('c') || idStr.startsWith('mcp'))) {
          this.state.system.mcpConnectors = data;
        } else if (first && (first.format !== undefined || first.domain !== undefined || first.instructions !== undefined || idStr.startsWith('s') || idStr.startsWith('skill'))) {
          this.state.system.skillsRegistry = data;
        }
      }
    } else {
      if (Array.isArray(data.mcp_connectors)) {
        this.state.system.mcpConnectors = data.mcp_connectors;
      } else if (Array.isArray(data.connectors)) {
        this.state.system.mcpConnectors = data.connectors;
      } else if (data.mcp_connectors && Array.isArray(data.mcp_connectors.connectors)) {
        this.state.system.mcpConnectors = data.mcp_connectors.connectors;
      }

      if (Array.isArray(data.skills)) {
        this.state.system.skillsRegistry = data.skills;
      } else if (data.skills_registry) {
        if (Array.isArray(data.skills_registry.skills)) {
          this.state.system.skillsRegistry = data.skills_registry.skills;
        } else if (Array.isArray(data.skills_registry)) {
          this.state.system.skillsRegistry = data.skills_registry;
        }
      }
    }

    if (typeof data.code_version === 'number' && data.code_version > 0) {
      if (this.lastCodeVersion && data.code_version > this.lastCodeVersion) {
        window.location.reload();
        return;
      }
      this.lastCodeVersion = data.code_version;
    }

    if (this.state.system.aiWorking !== this.lastAiWorking) {
      this.lastAiWorking = this.state.system.aiWorking;
      this.onAiStateChange(this.state.system.aiWorking, this.state.system.aiSource);
    }

    if (!this.demoAudioActive && typeof data.audio_peak === 'number') {
      this.state.audioEnergy = data.audio_peak;
      this.state.audioLeft = data.audio_left || data.audio_peak;
      this.state.audioRight = data.audio_right || data.audio_peak;
      this.state.audioBeat = data.audio_beat || 0;

      if (Array.isArray(data.audio_bands)) {
        for (let i = 0; i < Math.min(24, data.audio_bands.length); i++) {
          this.spectrum[i] = this.spectrum[i] * 0.35 + data.audio_bands[i] * 0.65;
        }
      }
    }
  }

  connectRealtimeBridge() {
    const connectSSE = () => {
      if (this.sseActive) return;
      try {
        const es = new EventSource(this.streamUrl);
        this.sseActive = true;

        es.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            this.applyBridgeData(data);
          } catch (e) {}
        };

        es.onerror = () => {
          es.close();
          this.sseActive = false;
          this.bridgeConnected = false;
          this.state.system.bridgeOnline = false;
          setTimeout(connectSSE, 2000);
        };
      } catch (e) {
        this.sseActive = false;
        setTimeout(connectSSE, 2500);
      }
    };

    connectSSE();

    setInterval(async () => {
      if (this.sseActive && this.bridgeConnected) return;
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 450);
        const res = await fetch(this.statusUrl, { signal: controller.signal, cache: 'no-store' });
        clearTimeout(timeout);
        if (res.ok) {
          const data = await res.json();
          this.applyBridgeData(data);
        }
      } catch (err) {
        this.bridgeConnected = false;
        this.state.system.bridgeOnline = false;
      }
    }, 800);
  }

  toggleDemoAudio() {
    this.demoAudioActive = !this.demoAudioActive;
    if (!this.demoAudioActive) {
      this.state.audioEnergy = 0;
      this.state.audioBeat = 0;
      this.spectrum.fill(0);
    }
    return this.demoAudioActive;
  }

  update(dt, time) {
    if (!this.bridgeConnected) {
      const targetCpu =
        this.state.activeMode === 'ai' ? 68 + Math.sin(time * 2.5) * 18 : 16 + Math.sin(time * 0.7) * 8;
      this.state.system.cpu = Math.round(this.state.system.cpu * 0.92 + targetCpu * 0.08);
    }

    if (this.demoAudioActive) {
      let sum = 0;
      const beatWave = Math.pow(Math.max(0, Math.sin(time * 5.2)), 4);
      for (let i = 0; i < 24; i++) {
        const freqWave = Math.abs(Math.sin(time * 8.5 + i * 0.45)) * 0.55 + beatWave * 0.45;
        this.spectrum[i] = freqWave;
        sum += freqWave;
      }
      this.state.audioEnergy = sum / 24;
      this.state.audioBeat = beatWave > 0.65 ? beatWave : 0;
    } else if (!this.bridgeConnected) {
      let sum = 0;
      for (let i = 0; i < 24; i++) {
        this.spectrum[i] *= 0.9;
        sum += this.spectrum[i];
      }
      if (sum / 24 < 0.01) {
        this.state.audioEnergy = 0;
        this.state.audioBeat = 0;
      }
    }
  }
}

window.SystemBridge = SystemBridge;
