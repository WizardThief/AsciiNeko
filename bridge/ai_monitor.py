"""
ai_monitor.py - Bridge locale (Zero dipendenze esterne) per ASCII AI Companion Wallpaper.
Funzionalità:
  1. Cattura in tempo reale (30 FPS) l'audio di sistema di Windows tramite WASAPI COM (IAudioMeterInformation)
     con rilevamento automatico dei Beat musicali, canali Stereo L/R e spettro dinamico a 24 bande.
  2. Rileva quando Antigravity / Claude / Ollama lavorano sul PC + statistiche CPU/RAM reali.
  3. Espone uno stream Server-Sent Events (SSE) a bassa latenza su http://127.0.0.1:8765/stream
     oltre all'endpoint JSON http://127.0.0.1:8765/status.
"""

import ctypes
from ctypes import wintypes, POINTER, Structure, c_float, c_uint32, c_void_p, byref, WINFUNCTYPE, HRESULT
import glob
import http.server
import json
import math
import os
import subprocess
import threading
import time

PORT = 8765
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
USER_HOME = os.path.expanduser("~")

# --- Strutture ctypes Windows per RAM, CPU e WASAPI Audio Meter ---
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

def _filetime_to_int(ft):
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


class WindowsAudioCapture:
    """Legge il volume di picco in tempo reale dall'uscita audio predefinita di Windows via WASAPI COM."""
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
            return None, None, None

        vtable_enum = ctypes.cast(pEnumerator, POINTER(POINTER(c_void_p))).contents
        GetDefaultAudioEndpoint = WINFUNCTYPE(HRESULT, c_void_p, c_uint32, c_uint32, POINTER(c_void_p))(vtable_enum[4])
        pDevice = c_void_p()
        hr = GetDefaultAudioEndpoint(pEnumerator, 0, 1, byref(pDevice))
        if hr != 0 or not pDevice:
            return None, None, None

        vtable_dev = ctypes.cast(pDevice, POINTER(POINTER(c_void_p))).contents
        Activate = WINFUNCTYPE(HRESULT, c_void_p, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(vtable_dev[3])
        pMeter = c_void_p()
        hr = Activate(pDevice, byref(IID_IAudioMeterInformation), 1, None, byref(pMeter))
        if hr != 0 or not pMeter:
            return None, None, None

        vtable_meter = ctypes.cast(pMeter, POINTER(POINTER(c_void_p))).contents
        GetPeakValue = WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_float))(vtable_meter[3])
        GetMeteringChannelCount = WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_uint32))(vtable_meter[4])
        GetChannelsPeakValues = WINFUNCTYPE(HRESULT, c_void_p, c_uint32, POINTER(c_float))(vtable_meter[5])
        return pMeter, (GetPeakValue, GetMeteringChannelCount, GetChannelsPeakValues), ole32

    def _audio_loop(self):
        pMeter, funcs, _ = self._init_com_meter()
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

                # Filtro Anti-Notifiche Windows:
                # L'audio deve essere presente in modo continuativo per almeno 2.2 secondi prima di attivare la modalità musica.
                # I suoni brevi (notifiche di sistema, beep, click < 2.2s) vengono ignorati completamente!
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

                # Smussamento esponenziale morbido (niente scatti caotici)
                self._smoothed_peak = self._smoothed_peak * 0.82 + raw_norm * 0.18
                norm_peak = self._smoothed_peak if self._smoothed_peak > 0.015 else 0.0

                diff = norm_peak - self._rolling_avg
                self._rolling_avg = self._rolling_avg * 0.90 + norm_peak * 0.10
                beat_val = min(0.7, max(0.0, diff * 2.0)) if norm_peak > 0.05 else 0.0

                # Aggiorna buffer storico e genera spettro dinamico a 24 bande
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

            time.sleep(0.033)  # ~30 FPS

    def get_audio_state(self):
        with self._lock:
            return {
                "audio_peak": self.peak,
                "audio_left": self.left,
                "audio_right": self.right,
                "audio_beat": self.beat,
                "audio_bands": list(self.bands),
            }


class SystemStateMonitor:
    def __init__(self, audio_capture):
        self.audio = audio_capture
        self.cpu_percent = 15.0
        self.ram_percent = 40.0
        self.ai_working = False
        self.ai_source = "IDLE"
        self.ai_detail = ""
        self.desktop_items = []
        self.code_version = 0.0
        self.shared_cat = {
            "worldCol": 60,
            "row": 24,
            "facing": 1,
            "isWalking": False,
            "walkPhase": 0,
            "emotion": "neutral",
            "toy": None,
            "remoteCommand": None,
            "cmdId": 0
        }
        self._active_clients = {}
        self._last_desktop_scan = 0.0
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

            i_diff = i_now - self._last_idle
            k_diff = k_now - self._last_kernel
            u_diff = u_now - self._last_user
            total = k_diff + u_diff

            self._last_idle = i_now
            self._last_kernel = k_now
            self._last_user = u_now

            if total > 0:
                busy = max(0, total - i_diff)
                cpu = min(100.0, max(1.0, (busy / total) * 100.0))

        return round(cpu, 1), round(ram, 1)

    def _detect_ai_activity(self, current_cpu):
        now = time.time()

        # 1. Antigravity
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
                return True, "ANTIGRAVITY AGENT", "Elaborazione conversazione / tool in corso"

        # 2. Claude Code
        claude_dir = os.path.join(USER_HOME, ".claude", "projects")
        if os.path.isdir(claude_dir):
            for root, _, files in os.walk(claude_dir):
                for f in files:
                    if f.endswith(".jsonl"):
                        try:
                            if now - os.path.getmtime(os.path.join(root, f)) < 18.0:
                                return True, "CLAUDE CODE", "Sessione agente attiva"
                        except OSError:
                            pass

        # 3. Ollama / LM Studio
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            out = subprocess.check_output(
                ["tasklist", "/NH", "/FO", "CSV"],
                startupinfo=startupinfo,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1.5
            ).lower()

            if "ollama_llama_server.exe" in out or "llama-server.exe" in out:
                if current_cpu > 25.0:
                    return True, "LOCAL LLM (OLLAMA)", "Inferenza modello locale"
            if "lm studio.exe" in out and current_cpu > 30.0:
                return True, "LM STUDIO", "Inferenza modello locale"
        except Exception:
            pass

        return False, "IDLE", ""

    def _scan_desktop_items(self):
        now = time.time()
        if now - self._last_desktop_scan < 4.0 and self.desktop_items:
            return self.desktop_items
        self._last_desktop_scan = now

        desktop_dir = os.path.join(USER_HOME, "Desktop")
        items = []
        if os.path.isdir(desktop_dir):
            try:
                for entry in os.scandir(desktop_dir):
                    if entry.name.startswith(".") or entry.name.lower() == "desktop.ini":
                        continue
                    raw_name = entry.name
                    # Pulisci suffissi Windows (.lnk, - collegamento, .exe, .url)
                    clean = raw_name
                    for suffix in [" - collegamento.lnk", " - Shortcut.lnk", ".lnk", ".url", ".exe"]:
                        if clean.lower().endswith(suffix.lower()):
                            clean = clean[: -len(suffix)]
                            break

                    is_dir = entry.is_dir() or ("collegamento" in raw_name.lower() and "." not in clean)
                    kind = "folder" if is_dir else ("app" if raw_name.lower().endswith((".exe", ".lnk")) else "file")
                    items.append({
                        "name": clean[:16],
                        "type": kind,
                    })
            except OSError:
                pass
        return items[:14]

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

    def _loop(self):
        while True:
            cpu, ram = self._read_cpu_and_ram()
            ai_work, ai_src, ai_det = self._detect_ai_activity(cpu)
            desk = self._scan_desktop_items()
            c_ver = self._scan_code_version()
            with self._lock:
                self.cpu_percent = cpu
                self.ram_percent = ram
                self.ai_working = ai_work
                self.ai_source = ai_src
                self.ai_detail = ai_det
                self.desktop_items = desk
                self.code_version = c_ver
            time.sleep(1.2)

    def _read_cursor_info(self):
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(byref(pt))
        cx, cy = int(pt.x), int(pt.y)
        # Schermo 0 (Principale: 0..2560), Schermo 1 (Secondario: 2560..5120)
        screen_w = ctypes.windll.user32.GetSystemMetrics(0) or 2560
        screen_idx = 0 if cx < screen_w else 1
        return cx, cy, screen_idx, screen_w

    def register_client_slot(self, client_id):
        now = time.time()
        with self._lock:
            # Rimuovi client disconnessi da oltre 5s
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
                for k in ("worldCol", "row", "facing", "isWalking", "walkPhase", "emotion", "toy"):
                    if k in payload:
                        self.shared_cat[k] = payload[k]

    def get_snapshot(self, client_id=None):
        cx, cy, c_screen, screen_w = self._read_cursor_info()
        assigned_slot = self.register_client_slot(client_id) if client_id else 0
        with self._lock:
            snap = {
                "cpu": self.cpu_percent,
                "ram": self.ram_percent,
                "ai_working": self.ai_working,
                "ai_source": self.ai_source,
                "ai_detail": self.ai_detail,
                "desktop_items": list(self.desktop_items),
                "code_version": self.code_version,
                "cursor_x": cx,
                "cursor_y": cy,
                "cursor_screen": c_screen,
                "screen_w": screen_w,
                "assigned_slot": assigned_slot,
                "shared_cat": dict(self.shared_cat),
                "timestamp": time.time(),
            }
        snap.update(self.audio.get_audio_state())
        return snap


audio_capture = WindowsAudioCapture()
monitor = SystemStateMonitor(audio_capture)


class CompanionRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PROJECT_ROOT, **kwargs)

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
        if self.path.startswith("/cat_sync"):
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    monitor.update_shared_cat(body)
                except Exception:
                    pass
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
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
            # Stream Server-Sent Events (SSE) a 25 FPS per audio e stato multi-schermo!
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    payload = json.dumps(monitor.get_snapshot(client_id))
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.04)  # 25 FPS
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception:
                return

        if self.path.startswith("/status"):
            data = json.dumps(monitor.get_snapshot(client_id)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        return super().do_GET()


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), CompanionRequestHandler)
    print("==========================================================")
    print("  ASCII AI COMPANION // WASAPI AUDIO + AI BRIDGE ATTIVO")
    print(f"  - Stream Live 25fps : http://127.0.0.1:{PORT}/stream")
    print(f"  - Endpoint Stato AI : http://127.0.0.1:{PORT}/status")
    print(f"  - Anteprima Browser : http://127.0.0.1:{PORT}/")
    print("==========================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
