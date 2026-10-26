"""
app_window.py - Launcher Applicazione Desktop Neko 3-in-1 (Dashboard + Mini-Widget + Hotkey Globale Alt+N).
Funzionalità:
  1. Verifica che il server locale `ai_monitor.py` sia attivo su http://127.0.0.1:8765 (avviandolo se spento).
  2. Apre Neko in una finestra App Desktop dedicata e pulita (`?mode=app`, 1180x780 centrata).
  3. Registra la scorciatoia globale di Windows scelta dall'utente (di default `Alt + N` per non andare in
     conflitto con `Alt + Spazio` di Gemini/PowerToys!) e la aggiorna dinamicamente se cambiata nelle Impostazioni.
"""

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import time
import urllib.request

BRIDGE_DIR = os.path.abspath(os.path.dirname(__file__))
MONITOR_SCRIPT = os.path.join(BRIDGE_DIR, "ai_monitor.py")
STORAGE_CONFIG_FILE = os.path.join(BRIDGE_DIR, "storage_config.json")
DEFAULT_DATA_DIR = os.path.join(BRIDGE_DIR, "data")
APP_PROFILE_DIR = os.path.join(BRIDGE_DIR, ".neko_app_profile")
APP_URL = "http://127.0.0.1:8765/?mode=app"
STATUS_URL = "http://127.0.0.1:8765/status"
WINDOW_MODE_URL = "http://127.0.0.1:8765/window_mode"

user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

HOTKEY_MAP = {
    "Alt+N": (MOD_ALT | MOD_NOREPEAT, 0x4E),       # Alt + N ("Neko" - Default senza conflitti con Gemini!)
    "Alt+Q": (MOD_ALT | MOD_NOREPEAT, 0x51),       # Alt + Q
    "Ctrl+Space": (MOD_CONTROL | MOD_NOREPEAT, 0x20),  # Ctrl + Spazio
    "Alt+Z": (MOD_ALT | MOD_NOREPEAT, 0x5A),       # Alt + Z
}


def get_configured_hotkey() -> str:
    data_dir = DEFAULT_DATA_DIR
    if os.path.isfile(STORAGE_CONFIG_FILE):
        try:
            with open(STORAGE_CONFIG_FILE, "r", encoding="utf-8") as sf:
                scfg = json.load(sf)
                custom = (scfg.get("data_dir") or "").strip()
                if custom and os.path.isdir(custom):
                    data_dir = custom
        except Exception:
            pass
    profile_path = os.path.join(data_dir, "profile.json")
    if not os.path.isfile(profile_path):
        profile_path = os.path.join(BRIDGE_DIR, "profile.json")
    if os.path.isfile(profile_path):
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                hk = data.get("hotkey", "Alt+N")
                if hk in HOTKEY_MAP:
                    return hk
        except Exception:
            pass
    return "Alt+N"


def is_server_running() -> bool:
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=1.2) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_server_running():
    if is_server_running():
        return True
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    subprocess.Popen(
        [pythonw, MONITOR_SCRIPT],
        cwd=BRIDGE_DIR,
        startupinfo=startupinfo,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(25):
        time.sleep(0.25)
        if is_server_running():
            return True
    return False


def find_neko_app_hwnd():
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            title = buf.value or ""
            if "ASCII AI Companion" in title or "Neko Desktop" in title:
                parent = user32.GetParent(hwnd)
                cls_buf = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(hwnd, cls_buf, 128)
                if cls_buf.value not in ("Progman", "WorkerW") and not parent:
                    found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found[0] if found else None


def find_browser_executable():
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\Application\msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def launch_app_window():
    existing_hwnd = find_neko_app_hwnd()
    if existing_hwnd:
        if user32.IsIconic(existing_hwnd):
            user32.ShowWindow(existing_hwnd, 9)
        user32.SetForegroundWindow(existing_hwnd)
        trigger_focus_input()
        return existing_hwnd

    browser_exe = find_browser_executable()
    screen_w = user32.GetSystemMetrics(0) or 1920
    screen_h = user32.GetSystemMetrics(1) or 1080
    win_w, win_h = min(1180, screen_w - 80), min(780, screen_h - 90)
    win_x = max(20, (screen_w - win_w) // 2)
    win_y = max(20, (screen_h - win_h) // 2)

    if browser_exe:
        os.makedirs(APP_PROFILE_DIR, exist_ok=True)
        subprocess.Popen(
            [
                browser_exe,
                f"--app={APP_URL}",
                f"--user-data-dir={APP_PROFILE_DIR}",
                f"--window-size={win_w},{win_h}",
                f"--window-position={win_x},{win_y}",
                "--disable-features=Translate,msEdgeSidebarV2",
                "--no-first-run",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.startfile(APP_URL)

    for _ in range(20):
        time.sleep(0.2)
        hwnd = find_neko_app_hwnd()
        if hwnd:
            user32.SetForegroundWindow(hwnd)
            return hwnd
    return None


def trigger_focus_input():
    try:
        req = urllib.request.Request(
            WINDOW_MODE_URL,
            data=json.dumps({"mode": "focus_input"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.0).read()
    except Exception:
        pass


def run_hotkey_loop():
    """
    Ascolta in background la scorciatoia globale configurata (default `Alt + N` per evitare conflitti con Gemini).
    Controlla anche ogni secondo se l'utente ha cambiato scorciatoia dal pannello Impostazioni di Neko!
    """
    WM_HOTKEY = 0x0312
    PM_REMOVE = 0x0001

    current_hk_name = get_configured_hotkey()
    mods, vk = HOTKEY_MAP.get(current_hk_name, HOTKEY_MAP["Alt+N"])
    user32.RegisterHotKey(None, 1, mods, vk)

    msg = wintypes.MSG()
    last_check = time.time()
    try:
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY and msg.wParam == 1:
                    hwnd = find_neko_app_hwnd()
                    if not hwnd:
                        launch_app_window()
                    else:
                        if user32.IsIconic(hwnd):
                            user32.ShowWindow(hwnd, 9)
                        user32.SetForegroundWindow(hwnd)
                        trigger_focus_input()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            now = time.time()
            if now - last_check > 1.5:
                last_check = now
                new_hk = get_configured_hotkey()
                if new_hk != current_hk_name:
                    user32.UnregisterHotKey(None, 1)
                    current_hk_name = new_hk
                    mods, vk = HOTKEY_MAP.get(current_hk_name, HOTKEY_MAP["Alt+N"])
                    user32.RegisterHotKey(None, 1, mods, vk)

            time.sleep(0.03)
    finally:
        user32.UnregisterHotKey(None, 1)


if __name__ == "__main__":
    ensure_server_running()
    launch_app_window()
    if "--no-hotkey" not in sys.argv:
        run_hotkey_loop()
