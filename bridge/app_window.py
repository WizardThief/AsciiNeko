"""
app_window.py - Applicazione Desktop Nativa Neko (pywebview + Server AI Integrato + Hotkey Globale Alt+N).
Funzionalità:
  1. Avvia il server locale `ai_monitor` direttamente in-process (o si collega se già attivo su 127.0.0.1:8765),
     eliminando qualsiasi rischio di disconnessione ("127.0.0.1 ha rifiutato la connessione").
  2. Apre Neko in una vera finestra desktop nativa tramite `pywebview` (WebView2 nativo di Windows,
     senza dipendere da finestre browser Edge `--app` esterne).
  3. Registra la scorciatoia globale di Windows scelta dall'utente (di default `Alt + N`) in un thread dedicato
     e la aggiorna dinamicamente se cambiata nelle Impostazioni.
"""

import ctypes
from ctypes import wintypes
import json
import os
import sys
import threading
import time
import urllib.request

import webview

BRIDGE_DIR = os.path.abspath(os.path.dirname(__file__))
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

import ai_monitor

STORAGE_CONFIG_FILE = os.path.join(BRIDGE_DIR, "storage_config.json")
DEFAULT_DATA_DIR = os.path.join(BRIDGE_DIR, "data")
WEBVIEW_STORAGE_DIR = os.path.join(BRIDGE_DIR, ".webview_data")
APP_URL = f"http://127.0.0.1:{ai_monitor.PORT}/?mode=app"
STATUS_URL = f"http://127.0.0.1:{ai_monitor.PORT}/status"
WINDOW_MODE_URL = f"http://127.0.0.1:{ai_monitor.PORT}/window_mode"
WINDOW_TITLE = "Neko Desktop — ASCII AI Companion"

user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

HOTKEY_MAP = {
    "Alt+N": (MOD_ALT | MOD_NOREPEAT, 0x4E),           # Alt + N ("Neko" - Default senza conflitti con Gemini!)
    "Alt+Q": (MOD_ALT | MOD_NOREPEAT, 0x51),           # Alt + Q
    "Ctrl+Space": (MOD_CONTROL | MOD_NOREPEAT, 0x20),  # Ctrl + Spazio
    "Alt+Z": (MOD_ALT | MOD_NOREPEAT, 0x5A),           # Alt + Z
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
        with urllib.request.urlopen(STATUS_URL, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def ensure_server_running():
    if is_server_running():
        return True
    ai_monitor.start_background_server()
    for _ in range(25):
        time.sleep(0.15)
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


def trigger_focus_input(pywebview_win=None):
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
    if pywebview_win is not None:
        try:
            pywebview_win.evaluate_js(
                "const el = document.getElementById('ai-input-field'); if (el) { el.focus(); el.select(); }"
            )
        except Exception:
            pass


def run_hotkey_loop(pywebview_win):
    """
    Ascolta in background la scorciatoia globale configurata (default `Alt + N` per evitare conflitti con Gemini).
    Controlla anche ogni 1.5s se l'utente ha cambiato scorciatoia dal pannello Impostazioni di Neko
    e vigila che il server HTTP locale sia sempre attivo.
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
                    try:
                        pywebview_win.restore()
                        pywebview_win.show()
                    except Exception:
                        pass
                    hwnd = find_neko_app_hwnd()
                    if hwnd:
                        if user32.IsIconic(hwnd):
                            user32.ShowWindow(hwnd, 9)
                        user32.SetForegroundWindow(hwnd)
                    trigger_focus_input(pywebview_win)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            now = time.time()
            if now - last_check > 1.5:
                last_check = now
                if not is_server_running():
                    ai_monitor.start_background_server()
                new_hk = get_configured_hotkey()
                if new_hk != current_hk_name:
                    user32.UnregisterHotKey(None, 1)
                    current_hk_name = new_hk
                    mods, vk = HOTKEY_MAP.get(current_hk_name, HOTKEY_MAP["Alt+N"])
                    user32.RegisterHotKey(None, 1, mods, vk)

            time.sleep(0.03)
    finally:
        user32.UnregisterHotKey(None, 1)


def main():
    # Se c'è già una finestra di Neko aperta, portala in primo piano ed esci
    existing_hwnd = find_neko_app_hwnd()
    if existing_hwnd:
        if user32.IsIconic(existing_hwnd):
            user32.ShowWindow(existing_hwnd, 9)
        user32.SetForegroundWindow(existing_hwnd)
        trigger_focus_input()
        return

    ensure_server_running()

    screen_w = user32.GetSystemMetrics(0) or 1920
    screen_h = user32.GetSystemMetrics(1) or 1080
    win_w, win_h = min(1180, screen_w - 80), min(780, screen_h - 90)
    win_x = max(20, (screen_w - win_w) // 2)
    win_y = max(20, (screen_h - win_h) // 2)

    window = webview.create_window(
        WINDOW_TITLE,
        APP_URL,
        width=win_w,
        height=win_h,
        x=win_x,
        y=win_y,
        min_size=(400, 340),
        background_color="#070a0f",
        text_select=True,
    )
    ai_monitor.win_conn_manager.pywebview_window = window

    os.makedirs(WEBVIEW_STORAGE_DIR, exist_ok=True)
    hk_thread = threading.Thread(target=run_hotkey_loop, args=(window,), daemon=True)
    hk_thread.start()

    webview.start(private_mode=False, storage_path=WEBVIEW_STORAGE_DIR)


if __name__ == "__main__":
    main()
