/**
 * SystemBridge - Gestisce il collegamento real-time (25-30 FPS) e la sincronizzazione Multi-Monitor
 * così esiste UN SOLO Neko che vive principalmente sullo Schermo 1 ma può attraversare lo Schermo 2!
 */
class SystemBridge {
  constructor(state, onAiStateChange, onRemoteCommand) {
    this.state = state;
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

  /**
   * Pubblica la posizione globale del Neko dallo Schermo 1 (Master) verso lo Schermo 2
   */
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

  /**
   * Invia un comando (es. click gomitolo o pescetto) dallo Schermo 2 allo Schermo 1
   */
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

  /**
   * Invia una domanda o comando al Terminale AI Locale (Ollama / Tool rapidi)
   */
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
      reply: '⚠️ Il bridge Python non è raggiungibile su 127.0.0.1:8765. Avvia start_companion.bat!',
      model: 'OFFLINE'
    };
  }

  applyBridgeData(data) {
    this.bridgeConnected = true;
    this.state.system.bridgeOnline = true;

    if (typeof data.cpu === 'number') this.state.system.cpu = Math.round(data.cpu);
    if (typeof data.ram === 'number') this.state.system.ram = Math.round(data.ram);
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

    // Calibrazione automatica dello Schermo (Schermo 0 = Principale, Schermo 1 = Secondario):
    // 1. Se questa finestra ha appena ricevuto un evento mousemove (< 120ms fa),
    //    allora sappiamo con certezza assoluta su quale monitor fisico si trova grazie a data.cursor_screen!
    if (!this.state.screenManualOverride) {
      const msSinceLocalMove = performance.now() - (this.state.mouse.lastMoveTime || 0);
      if (msSinceLocalMove < 140 && typeof data.cursor_screen === 'number') {
        this.state.screenIndex = data.cursor_screen;
        this.state.screenCalibrated = true;
      } else if (!this.state.screenCalibrated) {
        if (window.screenX >= 1000 || window.screenLeft >= 1000) {
          this.state.screenIndex = 1;
        } else if (typeof data.assigned_slot === 'number') {
          this.state.screenIndex = data.assigned_slot;
        }
      }
    }

    // Posizione globale del cursore per permettere al Neko sullo Schermo 1 di guardare il mouse anche quando è sullo Schermo 2!
    if (typeof data.cursor_x === 'number' && typeof data.screen_w === 'number') {
      this.state.globalMouse = {
        x: data.cursor_x,
        y: data.cursor_y,
        screen: data.cursor_screen || 0,
        screenW: data.screen_w || 2560
      };
    }

    // Sincronizzazione stato del Neko tra i monitor
    if (data.shared_cat) {
      if (this.state.screenIndex !== 0) {
        this.state.sharedCat = data.shared_cat;
      } else if (
        data.shared_cat.remoteCommand &&
        data.shared_cat.cmdId &&
        data.shared_cat.cmdId !== this.lastCmdId
      ) {
        this.lastCmdId = data.shared_cat.cmdId;
        this.onRemoteCommand(data.shared_cat.remoteCommand);
      }
    }

    // Hot-Reload Automatico
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

  getAsciiSpectrum() {
    const blocks = '▁▂▃▄▅▆▇█';
    let out = '';
    for (let i = 0; i < 24; i++) {
      const v = Math.min(1, Math.max(0, this.spectrum[i]));
      const idx = Math.min(blocks.length - 1, Math.floor(v * blocks.length));
      out += blocks[idx];
    }
    return out;
  }
}

window.SystemBridge = SystemBridge;
