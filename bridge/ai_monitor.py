"""
ai_monitor.py - Bridge locale (Zero dipendenze esterne) per ASCII Neko Desktop Companion.
Funzionalità:
  1. Cattura in tempo reale (30 FPS) l'audio di sistema di Windows tramite WASAPI COM
     con filtro Anti-Notifiche (> 2.2s di musica continua).
  2. Rileva se il puntatore del mouse si trova davvero sullo sfondo vuoto del Desktop (is_desktop_under_cursor)
     usando WindowFromPoint + GetClassNameW, così i click fatti dentro altre finestre vengono ignorati!
  3. Integra il motore AI Locale (Ollama su http://127.0.0.1:11434), avviando automaticamente `ollama serve`
     se spento, esponendo POST /ai_chat + Tool locali (Top CPU/RAM, To-Do list persistente, Apertura cartelle).
  4. Sincronizzazione Multi-Schermo per avere UN SOLO Neko che vive sul Monitor 1 e può visitare il Monitor 2.
"""

import ctypes
from ctypes import wintypes, POINTER, Structure, c_float, c_uint32, c_void_p, byref, WINFUNCTYPE, HRESULT
import glob
import http.server
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

PORT = 8765
OLLAMA_BASE = "http://127.0.0.1:11434"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TODO_FILE = os.path.join(os.path.dirname(__file__), "todo.json")
USER_HOME = os.path.expanduser("~")

# --- Strutture ctypes Windows ---
class MEMORYSTATUSEX(Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

class FILETIME(Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]

class GUID(Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]
    def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
        self.Data1, self.Data2, self.Data3 = l, w1, w2
        self.Data4[:] = (b1, b2, b3, b4, b5, b6, b7, b8)

CLSID_MMDeviceEnumerator = GUID(0xBCDE0395, 0xE52F, 0x467C, 0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E)
IID_IMMDeviceEnumerator = GUID(0xA95664D2, 0x9614, 0x4F35, 0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6)
IID_IAudioMeterInformation = GUID(0xC02216F6, 0x8C67, 0x4B5B, 0x9D, 0x00, 0xD0, 0x08, 0xE7, 0x3E, 0x00, 0x64)

user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

def _filetime_to_int(ft):
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


class WindowsAudioCapture:
    """Legge il volume di picco dall'uscita audio di Windows con filtro Anti-Notifiche (> 2.2s)."""
    def __init__(self):
        self.peak = 0.0
        self.left = 0.0
        self.right = 0.0
        self.beat = 0.0
        self.bands = [0.0] * 24
        self._rolling_avg = 0.05
        self._max_seen = 0.25
        self._sustained_sec = 0.0
        self._silence_sec = 0.0
        self._smoothed_peak = 0.0
        self._history = [0.0] * 24
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._thread.start()

    def _init_com_meter(self):
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        pEnumerator = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnumerator)
        )
        if hr != 0 or not pEnumerator:
            return None, None

        vtable_enum = ctypes.cast(pEnumerator, POINTER(POINTER(c_void_p))).contents
        GetDefaultAudioEndpoint = WINFUNCTYPE(HRESULT, c_void_p, c_uint32, c_uint32, POINTER(c_void_p))(vtable_enum[4])
        pDevice = c_void_p()
        hr = GetDefaultAudioEndpoint(pEnumerator, 0, 1, byref(pDevice))
        if hr != 0 or not pDevice:
            return None, None

        vtable_dev = ctypes.cast(pDevice, POINTER(POINTER(c_void_p))).contents
        Activate = WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(vtable_dev[3])
        pMeter = c_void_p()
        hr = Activate(pDevice, byref(IID_IAudioMeterInformation), 1, None, byref(pMeter))
        if hr != 0 or not pMeter:
            return None, None

        vtable_meter = ctypes.cast(pMeter, POINTER(POINTER(c_void_p))).contents
        GetPeakValue = WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_float))(vtable_meter[3])
        GetMeteringChannelCount = WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_uint32))(vtable_meter[4])
        GetChannelsPeakValues = WINFUNCTYPE(HRESULT, c_void_p, c_uint32, POINTER(c_float))(vtable_meter[5])
        return pMeter, (GetPeakValue, GetMeteringChannelCount, GetChannelsPeakValues)

    def _audio_loop(self):
        pMeter, funcs = self._init_com_meter()
        if not pMeter:
            return
        GetPeakValue, GetMeteringChannelCount, GetChannelsPeakValues = funcs

        while True:
            try:
                raw_peak = c_float(0.0)
                GetPeakValue(pMeter, byref(raw_peak))
                p_val = float(raw_peak.value)

                ch_count = c_uint32(0)
                GetMeteringChannelCount(pMeter, byref(ch_count))
                if ch_count.value >= 2:
                    ch_peaks = (c_float * ch_count.value)()
                    GetChannelsPeakValues(pMeter, ch_count.value, ch_peaks)
                    l_val = float(ch_peaks[0])
                    r_val = float(ch_peaks[1])
                else:
                    l_val = p_val
                    r_val = p_val

                dt = 0.033
                if p_val > 0.012:
                    self._silence_sec = 0.0
                    self._sustained_sec = min(10.0, self._sustained_sec + dt)
                else:
                    self._silence_sec += dt
                    if self._silence_sec > 0.85:
                        self._sustained_sec = max(0.0, self._sustained_sec - dt * 3.5)

                is_sustained_music = self._sustained_sec >= 2.2

                if p_val > 0.012 and is_sustained_music:
                    self._max_seen = max(p_val, self._max_seen * 0.997)
                    norm_scale = 0.75 / max(0.18, self._max_seen)
                    raw_norm = min(0.85, p_val * norm_scale)
                    norm_l = min(0.85, l_val * norm_scale)
                    norm_r = min(0.85, r_val * norm_scale)
                else:
                    raw_norm = 0.0
                    norm_l = 0.0
                    norm_r = 0.0

                self._smoothed_peak = self._smoothed_peak * 0.82 + raw_norm * 0.18
                norm_peak = self._smoothed_peak if self._smoothed_peak > 0.015 else 0.0

                diff = norm_peak - self._rolling_avg
                self._rolling_avg = self._rolling_avg * 0.90 + norm_peak * 0.10
                beat_val = min(0.7, max(0.0, diff * 2.0)) if norm_peak > 0.05 else 0.0

                self._history.pop(0)
                self._history.append(norm_peak)

                t = time.time()
                new_bands = []
                for i in range(24):
                    hist_val = self._history[23 - i]
                    stereo_bias = norm_l if i < 12 else norm_r
                    mod = 0.65 + 0.35 * math.sin(t * (6.0 + i * 0.7) + i)
                    b_val = min(1.0, (hist_val * 0.55 + stereo_bias * 0.45) * mod + beat_val * 0.25)
                    new_bands.append(round(b_val, 3))

                with self._lock:
                    self.peak = round(norm_peak, 3)
                    self.left = round(norm_l, 3)
                    self.right = round(norm_r, 3)
                    self.beat = round(beat_val, 3)
                    self.bands = new_bands
            except Exception:
                pass
            time.sleep(0.033)

    def get_audio_state(self):
        with self._lock:
            return {
                "audio_peak": self.peak,
                "audio_left": self.left,
                "audio_right": self.right,
                "audio_beat": self.beat,
                "audio_bands": list(self.bands),
            }


class OllamaLocalBridge:
    """Gestisce la comunicazione con Ollama locale + Tool rapidi del terminale."""
    def __init__(self):
        self.models = []
        self.active_model = ""
        self.is_generating = False
        self.ollama_online = False
        self._ensure_thread = threading.Thread(target=self._init_ollama, daemon=True)
        self._ensure_thread.start()

    def _init_ollama(self):
        # Controlla se Ollama risponde; altrimenti prova ad avviare `ollama serve` in background
        if not self._fetch_models():
            ollama_bin = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
            if not os.path.isfile(ollama_bin):
                ollama_bin = "ollama"
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                subprocess.Popen(
                    [ollama_bin, "serve"],
                    startupinfo=startupinfo,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(2.5)
                self._fetch_models()
            except Exception:
                pass

    def _fetch_models(self):
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                self.models = models
                self.ollama_online = True
                if models and not self.active_model:
                    self.active_model = models[0]
                return True
        except Exception:
            self.ollama_online = False
            return False

    def load_todos(self):
        if os.path.isfile(TODO_FILE):
            try:
                with open(TODO_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def save_todos(self, items):
        try:
            with open(TODO_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_top_processes(self):
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            cmd = (
                "Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 "
                "Name, @{Name='MB';Expression={[math]::Round($_.WorkingSet64/1MB,0)}} | ConvertTo-Json -Compress"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", cmd],
                startupinfo=startupinfo,
                text=True,
                timeout=3.0
            ).strip()
            procs = json.loads(out)
            if isinstance(procs, dict):
                procs = [procs]
            summary = " · ".join([f"{p['Name']} ({p['MB']} MB)" for p in procs])
            return f"📊 Top Processi RAM: {summary}"
        except Exception:
            return "📊 Impossibile leggere la lista dettagliata dei processi in questo istante."

    def handle_chat_or_command(self, prompt_text, requested_model=None, cpu=0, ram=0):
        text = (prompt_text or "").strip()
        if not text:
            return {"reply": "Miao? Scrivi pure un comando o una domanda!", "model": self.active_model}

        low = text.lower()

        # 0. Tool Rapido: Stacca/Chiudi il Tema Desktop
        if low in ("/stop", "/exit", "esci", "stacca tema", "chiudi tema"):
            def _close_theme_window():
                time.sleep(0.4)
                WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                def _cb(hwnd, lparam):
                    if user32.IsWindowVisible(hwnd):
                        buf = ctypes.create_unicode_buffer(256)
                        user32.GetWindowTextW(hwnd, buf, 256)
                        if "ASCII AI Companion // Neko Desktop" in buf.value:
                            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                    return True
                user32.EnumWindows(WNDENUMPROC(_cb), 0)
            threading.Thread(target=_close_theme_window, daemon=True).start()
            return {
                "reply": "👋 Tema Desktop staccato! A presto, miao!",
                "model": "LOCAL-TOOL"
            }

        # 1. Tool Rapido: Top CPU/RAM
        if low in ("/sys", "/cpu", "top cpu", "top ram", "status"):
            top_info = self.get_top_processes()
            return {
                "reply": f"[SISTEMA: CPU {cpu}% | RAM {ram}%] {top_info}",
                "model": "LOCAL-TOOL"
            }

        # 2. Tool Rapido: Lista To-Do
        if low.startswith("/todo") or low.startswith("todo"):
            arg = text[5:].strip() if low.startswith("/todo") else text[4:].strip()
            if arg.startswith(":"):
                arg = arg[1:].strip()
            todos = self.load_todos()
            if not arg:
                if not todos:
                    return {"reply": "📝 La tua lista To-Do è vuota! Scrivi `todo: <cosa da fare>` per aggiungere.", "model": "LOCAL-TOOL"}
                formatted = " | ".join([f"[{i+1}] {t}" for i, t in enumerate(todos)])
                return {"reply": f"📝 To-Do attivi: {formatted} (scrivi `todo clear` per svuotare)", "model": "LOCAL-TOOL"}
            elif arg.lower() in ("clear", "pulisci", "reset"):
                self.save_todos([])
                return {"reply": "🧹 Lista To-Do svuotata con successo!", "model": "LOCAL-TOOL"}
            else:
                todos.append(arg)
                self.save_todos(todos)
                return {"reply": f"✅ Aggiunto alla To-Do list (#{len(todos)}): \"{arg}\"", "model": "LOCAL-TOOL"}

        # 3. Richiesta al Modello LLM Locale (Ollama)
        if not self.ollama_online:
            self._fetch_models()

        model_to_use = requested_model or self.active_model
        if not model_to_use:
            return {
                "reply": "⚠️ Ollama è installato ma non ho trovato modelli scaricati (es. esegui `ollama pull llama3.2` nel terminale). Intanto puoi usare i controlli `/sys` e `todo: ...`!",
                "model": "OFFLINE"
            }

        self.is_generating = True
        try:
            payload = {
                "model": model_to_use,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Sei Neko AI, un assistente compagno da scrivania intelligente, conciso e simpatico "
                            "che vive nel terminale del desktop di Diego. Rispondi sempre in italiano in modo "
                            "molto diretto, utile e compatto (massimo 2-3 frasi brevi)."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                "stream": False,
            }
            req = urllib.request.Request(
                f"{OLLAMA_BASE}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45.0) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                msg = res_data.get("message", {}).get("content", "").strip()
                msg = " ".join(msg.splitlines())
                return {"reply": msg or "Miao! Fatto!", "model": model_to_use}
        except Exception as e:
            return {"reply": f"⚠️ Errore connessione Ollama ({model_to_use}): {str(e)}", "model": model_to_use}
        finally:
            self.is_generating = False


def is_test_environment() -> bool:
    """Detects whether running inside a test runner (unittest, pytest)."""
    return any(mod in sys.modules for mod in ("unittest", "pytest")) or "test" in sys.argv[0].lower()


class NekoBotTelemetryAggregator:
    """
    Decoupled background aggregator for neko_bot daemon and status telemetry.
    Polls 127.0.0.1:8766/status asynchronously and falls back to .neko/status.json.
    Exposes thread-safe, zero-I/O in-memory snapshot for 25-30 FPS companion stream.
    """
    DEFAULT_DAEMON_URL = "http://127.0.0.1:8766"
    POLL_INTERVAL_SEC = 0.5
    PROBE_TIMEOUT_SEC = 0.25
    CACHE_TTL_SEC = 0.1

    def __init__(self, status_file_path=None, daemon_url=None, workspace_dir=None, auto_start=True):
        if daemon_url:
            self.daemon_url = str(daemon_url).rstrip("/")
        else:
            self.daemon_url = os.environ.get("NEKO_DAEMON_URL", self.DEFAULT_DAEMON_URL).rstrip("/")
        self.custom_status_file = str(status_file_path) if status_file_path else None
        self.workspace_dir = str(workspace_dir) if workspace_dir else None
        self._lock = threading.RLock()
        self._cached_telemetry = self._get_default_telemetry(status="offline")
        self._daemon_online = False
        self._last_source = "init"
        self._last_poll_time = 0.0

        # Decoupled background polling thread
        self._stop_event = threading.Event()
        self._thread = None

        if auto_start:
            self.start()

    def _get_default_telemetry(self, status="offline"):
        return {
            "daemon_health": status,
            "daemon_pid": None,
            "cloud_sync": {
                "status": status,
                "connected": False,
                "last_sync_time": None,
                "last_sync": None,
                "pending_mutations": 0,
                "pending": 0,
                "cloud_server_url": "http://127.0.0.1:8767",
                "server": "http://127.0.0.1:8767",
            },
            "memory": {
                "total_facts": 0,
                "facts_count": 0,
                "active_projects": 0,
                "recent_episodes": 0,
                "upcoming_deadlines": [],
            },
            "skills": {
                "total_skills": 0,
                "installed": 0,
                "active_skills": [],
                "active_tools": 0,
                "last_executed_skill": None,
                "last_execution": None,
            },
            "tasks": {
                "total_active": 0,
                "completed_today": 0,
                "top_task": None,
            },
            "top_task": None,
            "notification_queue": [],
            "alerts": [],
        }

    def _find_status_file(self):
        if self.custom_status_file:
            if os.path.exists(self.custom_status_file):
                return self.custom_status_file
            return None
        if self.workspace_dir:
            cand = os.path.join(self.workspace_dir, ".neko", "status.json")
            if os.path.exists(cand):
                return cand
            return None
        env_path = os.environ.get("NEKO_STATUS_FILE")
        if env_path and os.path.exists(env_path):
            return env_path
        candidates = [
            os.path.join(PROJECT_ROOT, ".neko", "status.json"),
            os.path.join(PROJECT_ROOT, "neko_bot", ".neko", "status.json"),
            os.path.join(os.path.dirname(PROJECT_ROOT), ".neko", "status.json"),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    def _normalize_payload(self, raw, source_type="daemon"):
        if not isinstance(raw, dict):
            return self._get_default_telemetry(status="error")

        defaults = self._get_default_telemetry(
            status=raw.get("daemon_health", "running" if source_type == "daemon" else "offline")
        )

        if "daemon_pid" in raw:
            defaults["daemon_pid"] = raw["daemon_pid"]

        # Merge cloud_sync
        cloud = raw.get("cloud_sync")
        if isinstance(cloud, dict):
            conn = bool(cloud.get("connected", False))
            cs_status = cloud.get("status", "synced" if conn else "offline")
            lst = cloud.get("last_sync_time") or cloud.get("last_sync")
            pm = cloud.get("pending_mutations") if "pending_mutations" in cloud else cloud.get("pending", 0)
            url = cloud.get("cloud_server_url") or cloud.get("server", "http://127.0.0.1:8767")
            defaults["cloud_sync"] = {
                "status": cs_status,
                "connected": conn,
                "last_sync_time": lst,
                "last_sync": lst,
                "pending_mutations": pm,
                "pending": pm,
                "cloud_server_url": url,
                "server": url,
            }

        # Merge memory
        mem = raw.get("memory")
        if isinstance(mem, dict):
            tf = mem.get("total_facts") if "total_facts" in mem else mem.get("facts_count", 0)
            ap = mem.get("active_projects", 0)
            re = mem.get("recent_episodes", 0)
            deadlines = mem.get("upcoming_deadlines", [])
            norm_deadlines = []
            if isinstance(deadlines, list):
                for d in deadlines:
                    if isinstance(d, dict):
                        pname = d.get("project") or d.get("name", "Untitled")
                        due = d.get("due") or d.get("deadline_utc")
                        norm_deadlines.append({
                            "project": pname,
                            "name": pname,
                            "milestone": d.get("milestone", ""),
                            "due": due,
                            "deadline_utc": d.get("deadline_utc") or due,
                            "risk": d.get("risk", "medium"),
                            "overdue": d.get("overdue", False),
                        })
            defaults["memory"] = {
                "total_facts": tf,
                "facts_count": tf,
                "active_projects": ap,
                "recent_episodes": re,
                "upcoming_deadlines": norm_deadlines,
            }

        # Merge skills
        sk = raw.get("skills")
        if isinstance(sk, dict):
            act = sk.get("active_skills", [])
            if not isinstance(act, list):
                act = []
            tot = sk.get("total_skills") if "total_skills" in sk else sk.get("installed", len(act))
            active_tools_count = sk.get("active_tools", len(act))
            last_s = sk.get("last_executed_skill") or sk.get("last_execution")
            defaults["skills"] = {
                "total_skills": tot,
                "installed": tot,
                "active_skills": act,
                "active_tools": active_tools_count,
                "last_executed_skill": last_s,
                "last_execution": last_s,
            }
            if "workflows" in sk:
                defaults["skills"]["workflows"] = sk["workflows"]

        # Merge tasks and top_task
        if "tasks" in raw and isinstance(raw["tasks"], dict):
            defaults["tasks"].update(raw["tasks"])
        if "top_task" in raw:
            defaults["top_task"] = raw["top_task"]
            if defaults["tasks"].get("top_task") is None:
                defaults["tasks"]["top_task"] = raw["top_task"]

        # Merge notifications & alerts
        if "notification_queue" in raw and isinstance(raw["notification_queue"], list):
            defaults["notification_queue"] = raw["notification_queue"]
        if "alerts" in raw and isinstance(raw["alerts"], list):
            defaults["alerts"] = raw["alerts"]

        return defaults

    def _poll_once(self, force=False):
        if self._stop_event.is_set():
            return
        now = time.time()
        if not force and (now - self._last_poll_time) < self.CACHE_TTL_SEC:
            return

        # Stage 1: Active HTTP probe to daemon
        if self.daemon_url and not self._stop_event.is_set():
            try:
                req = urllib.request.Request(f"{self.daemon_url}/status", headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.PROBE_TIMEOUT_SEC) as resp:
                    if self._stop_event.is_set():
                        return
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        norm = self._normalize_payload(data, source_type="daemon")
                        with self._lock:
                            self._cached_telemetry = norm
                            self._daemon_online = True
                            self._last_source = "http"
                            self._last_poll_time = time.time()
                        return
            except Exception:
                pass

        # Stage 2: Fallback to .neko/status.json file
        s_file = self._find_status_file()
        if s_file and not self._stop_event.is_set():
            for attempt in range(3):
                try:
                    if os.path.getsize(s_file) == 0:
                        norm = self._get_default_telemetry(status="offline")
                        with self._lock:
                            self._cached_telemetry = norm
                            self._daemon_online = False
                            self._last_source = "empty_file"
                            self._last_poll_time = time.time()
                        return
                    with open(s_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    norm = self._normalize_payload(data, source_type="file")
                    with self._lock:
                        self._cached_telemetry = norm
                        self._daemon_online = False
                        self._last_source = "file"
                        self._last_poll_time = time.time()
                    return
                except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
                    norm = self._get_default_telemetry(status="error")
                    norm["cloud_sync"]["status"] = "error"
                    with self._lock:
                        self._cached_telemetry = norm
                        self._daemon_online = False
                        self._last_source = "corrupt_file"
                        self._last_poll_time = time.time()
                    return
                except PermissionError:
                    time.sleep(0.01 * (attempt + 1))
                except Exception:
                    break

        # Stage 3: Offline Default Fallback
        with self._lock:
            self._cached_telemetry = self._get_default_telemetry(status="offline")
            self._daemon_online = False
            self._last_source = "default"
            self._last_poll_time = time.time()

    def start(self):
        """Starts the background polling thread and runs initial probe if not running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._poll_once(force=True)
            self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="NekoTelemetryAggregator")
            self._thread.start()

    def poll(self, force=False):
        """Public poll trigger with TTL support."""
        self._poll_once(force=force)

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                self._poll_once(force=False)
            except Exception:
                pass
            if self._stop_event.wait(self.POLL_INTERVAL_SEC):
                break

    def get_telemetry(self):
        """Thread-safe zero-I/O snapshot (< 0.05ms) for 25-30 FPS SSE streaming."""
        with self._lock:
            return dict(self._cached_telemetry)

    def stop(self, timeout=1.0):
        """Signals the background thread to stop immediately and joins with timeout."""
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive() and t != threading.current_thread():
            t.join(timeout=timeout)
        self._thread = None

    def close(self):
        self.stop()


class SystemStateMonitor:
    def __init__(self, audio_capture, ollama_bridge, telemetry=None):
        self.audio = audio_capture
        self.ollama = ollama_bridge
        if telemetry is not None:
            self.telemetry = telemetry
        else:
            self.telemetry = NekoBotTelemetryAggregator(auto_start=not is_test_environment())
        self.cpu_percent = 15.0
        self.ram_percent = 40.0
        self.ai_working = False
        self.ai_source = "IDLE"
        self.ai_detail = ""
        self.code_version = 0.0
        self.shared_cat = {
            "worldCol": 60,
            "row": 24,
            "facing": 1,
            "isWalking": False,
            "walkPhase": 0,
            "emotion": "neutral",
            "idleState": "sit",
            "toy": None,
            "remoteCommand": None,
            "cmdId": 0
        }
        self._active_clients = {}
        self._last_idle = 0
        self._last_kernel = 0
        self._last_user = 0
        self._lock = threading.Lock()

        self._init_cpu_times()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _init_cpu_times(self):
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if ctypes.windll.kernel32.GetSystemTimes(byref(idle), byref(kernel), byref(user)):
            self._last_idle = _filetime_to_int(idle)
            self._last_kernel = _filetime_to_int(kernel)
            self._last_user = _filetime_to_int(user)

    def _read_cpu_and_ram(self):
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ram = float(mem.dwMemoryLoad) if ctypes.windll.kernel32.GlobalMemoryStatusEx(byref(mem)) else self.ram_percent

        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        cpu = self.cpu_percent
        if ctypes.windll.kernel32.GetSystemTimes(byref(idle), byref(kernel), byref(user)):
            i_now = _filetime_to_int(idle)
            k_now = _filetime_to_int(kernel)
            u_now = _filetime_to_int(user)

            total = (k_now - self._last_kernel) + (u_now - self._last_user)
            i_diff = i_now - self._last_idle

            self._last_idle, self._last_kernel, self._last_user = i_now, k_now, u_now
            if total > 0:
                busy = max(0, total - i_diff)
                cpu = min(100.0, max(1.0, (busy / total) * 100.0))

        return round(cpu, 1), round(ram, 1)

    def _detect_ai_activity(self, current_cpu):
        if self.ollama.is_generating:
            return True, f"OLLAMA ({self.ollama.active_model})", "Generazione risposta locale"

        now = time.time()
        agy_brain_dir = os.path.join(USER_HOME, ".gemini", "antigravity-cli", "brain")
        if os.path.isdir(agy_brain_dir):
            patterns = [
                os.path.join(agy_brain_dir, "*", ".system_generated", "logs", "transcript.jsonl"),
                os.path.join(agy_brain_dir, "*", "*.md"),
            ]
            latest_mtime = 0
            for pat in patterns:
                for path in glob.glob(pat):
                    try:
                        mtime = os.path.getmtime(path)
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                    except OSError:
                        pass
            if now - latest_mtime < 25.0:
                return True, "ANTIGRAVITY AGENT", "Elaborazione in corso"

        claude_dir = os.path.join(USER_HOME, ".claude", "projects")
        if os.path.isdir(claude_dir):
            for root, _, files in os.walk(claude_dir):
                for f in files:
                    if f.endswith(".jsonl"):
                        try:
                            if now - os.path.getmtime(os.path.join(root, f)) < 18.0:
                                return True, "CLAUDE CODE", "Sessione attiva"
                        except OSError:
                            pass

        return False, "IDLE", ""

    def _scan_code_version(self):
        latest = 0.0
        for sub in ["index.html", "css/*.css", "js/*.js"]:
            for p in glob.glob(os.path.join(PROJECT_ROOT, sub)):
                try:
                    m = os.path.getmtime(p)
                    if m > latest:
                        latest = m
                except OSError:
                    pass
        return round(latest, 3)

    def _read_cursor_and_desktop_focus(self):
        """
        Legge le coordinate globali del mouse e verifica se sotto il cursore c'è davvero
        lo sfondo vuoto del Desktop (o la nostra finestra Neko), ignorando i click fatti
        dentro Chrome, Esplora File o altri programmi!
        """
        pt = wintypes.POINT()
        user32.GetCursorPos(byref(pt))
        cx, cy = int(pt.x), int(pt.y)
        screen_w = user32.GetSystemMetrics(0) or 2560
        screen_idx = 0 if cx < screen_w else 1

        hwnd = user32.WindowFromPoint(pt)
        is_desktop = True
        if hwnd:
            cls_buf = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls_buf, 128)
            cls_name = cls_buf.value

            root_hwnd = user32.GetAncestor(hwnd, 2)  # GA_ROOT = 2
            root_cls_buf = ctypes.create_unicode_buffer(128)
            title_buf = ctypes.create_unicode_buffer(256)
            if root_hwnd:
                user32.GetClassNameW(root_hwnd, root_cls_buf, 128)
                user32.GetWindowTextW(root_hwnd, title_buf, 256)
            root_cls = root_cls_buf.value
            win_title = title_buf.value

            desktop_classes = {
                "Progman",
                "WorkerW",
                "SysListView32",
                "SHELLDLL_DefView",
                "CefSharpBrowserWindow",
                "Chrome_RenderWidgetHostHWND",
            }
            # Se la finestra principale è il Desktop (Progman/WorkerW) o la nostra app "ASCII AI Companion"
            if root_cls in ("Progman", "WorkerW") or cls_name in ("SysListView32", "SHELLDLL_DefView"):
                is_desktop = True
            elif "ASCII AI Companion" in win_title or "AsciiNeko" in win_title:
                is_desktop = True
            else:
                # È una normale applicazione aperta (es. Chrome, VS Code, Esplora Risorse)
                is_desktop = False

        return cx, cy, screen_idx, screen_w, is_desktop

    def register_client_slot(self, client_id):
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._active_clients.items() if now - v["seen"] > 5.0]
            for k in expired:
                del self._active_clients[k]

            if client_id in self._active_clients:
                self._active_clients[client_id]["seen"] = now
                return self._active_clients[client_id]["slot"]

            used_slots = {v["slot"] for v in self._active_clients.values()}
            slot = 0 if 0 not in used_slots else 1
            self._active_clients[client_id] = {"slot": slot, "seen": now}
            return slot

    def update_shared_cat(self, payload):
        with self._lock:
            if "remoteCommand" in payload and payload["remoteCommand"]:
                self.shared_cat["remoteCommand"] = payload["remoteCommand"]
                self.shared_cat["cmdId"] = int(time.time() * 1000)
            else:
                for k in ("worldCol", "row", "facing", "isWalking", "walkPhase", "emotion", "idleState", "toy"):
                    if k in payload:
                        self.shared_cat[k] = payload[k]

    def _loop(self):
        while True:
            cpu, ram = self._read_cpu_and_ram()
            ai_work, ai_src, ai_det = self._detect_ai_activity(cpu)
            c_ver = self._scan_code_version()
            with self._lock:
                self.cpu_percent = cpu
                self.ram_percent = ram
                self.ai_working = ai_work
                self.ai_source = ai_src
                self.ai_detail = ai_det
                self.code_version = c_ver
            time.sleep(1.1)

    def get_snapshot(self, client_id=None):
        cx, cy, c_screen, screen_w, is_desktop = self._read_cursor_and_desktop_focus()
        assigned_slot = self.register_client_slot(client_id) if client_id else 0
        with self._lock:
            snap = {
                "cpu": self.cpu_percent,
                "ram": self.ram_percent,
                "ai_working": self.ai_working,
                "ai_source": self.ai_source,
                "ai_detail": self.ai_detail,
                "code_version": self.code_version,
                "cursor_x": cx,
                "cursor_y": cy,
                "cursor_screen": c_screen,
                "screen_w": screen_w,
                "is_desktop_focus": is_desktop,
                "assigned_slot": assigned_slot,
                "shared_cat": dict(self.shared_cat),
                "ollama_models": list(self.ollama.models),
                "ollama_active": self.ollama.active_model,
                "timestamp": time.time(),
            }
        snap.update(self.audio.get_audio_state())
        if self.telemetry:
            snap.update(self.telemetry.get_telemetry())
        return snap

    def stop(self):
        if self.telemetry:
            self.telemetry.stop()


audio_capture = WindowsAudioCapture()
ollama_bridge = OllamaLocalBridge()
telemetry_aggregator = NekoBotTelemetryAggregator(auto_start=not is_test_environment())
monitor = SystemStateMonitor(audio_capture, ollama_bridge, telemetry=telemetry_aggregator)


def stop_telemetry(timeout: float = 1.0):
    """Explicitly terminates background telemetry aggregator thread for clean test teardown."""
    global telemetry_aggregator, monitor
    if telemetry_aggregator is not None:
        telemetry_aggregator.stop(timeout=timeout)
    if monitor is not None and getattr(monitor, "telemetry", None) is not None:
        monitor.telemetry.stop(timeout=timeout)


def start_telemetry():
    """Starts module-level telemetry aggregator if not already running."""
    global telemetry_aggregator
    if telemetry_aggregator is not None:
        telemetry_aggregator.start()


class CompanionRequestHandler(http.server.SimpleHTTPRequestHandler):
    monitor = None
    ollama_bridge = None

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory or PROJECT_ROOT, **kwargs)

    @property
    def current_monitor(self):
        return self.monitor if self.monitor is not None else monitor

    @property
    def current_ollama(self):
        return self.ollama_bridge if self.ollama_bridge is not None else ollama_bridge

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                pass

        if self.path.startswith("/cat_sync"):
            self.current_monitor.update_shared_cat(body)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return

        if self.path.startswith("/ai_chat"):
            prompt = body.get("prompt", "")
            model = body.get("model", "")
            result = self.current_ollama.handle_chat_or_command(
                prompt,
                requested_model=model,
                cpu=self.current_monitor.cpu_percent,
                ram=self.current_monitor.ram_percent,
            )
            data = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(404)
        self.end_headers()

    def _extract_client_id(self):
        if "client_id=" in self.path:
            return self.path.split("client_id=")[1].split("&")[0]
        return None

    def do_GET(self):
        client_id = self._extract_client_id()
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            try:
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while True:
                    payload = json.dumps(self.current_monitor.get_snapshot(client_id), ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.04)
            except Exception:
                return

        if self.path.startswith("/status"):
            data = json.dumps(self.current_monitor.get_snapshot(client_id), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        return super().do_GET()


if __name__ == "__main__":
    start_telemetry()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), CompanionRequestHandler)
    print("==========================================================")
    print("  ASCII NEKO COMPANION // OLLAMA + MULTI-SCREEN BRIDGE")
    print(f"  - Stream Live 25fps : http://127.0.0.1:{PORT}/stream")
    print(f"  - Chat AI Locale    : http://127.0.0.1:{PORT}/ai_chat")
    print("==========================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop_telemetry()
