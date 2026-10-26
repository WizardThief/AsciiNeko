"""
app_window.py - Applicazione Desktop Nativa Neko (pywebview + Server AI Integrato + Hotkey Globale Alt+N).
Funzionalità:
  1. Avvia il server locale `ai_monitor` direttamente in-process su 127.0.0.1:8765.
  2. Apre Neko in una vera finestra desktop nativa tramite `pywebview` (WebView2).
  3. Quando clicchi la `X` della finestra, Neko si nasconde in background (0% CPU) invece di morire,
     così premendo `Alt + N` (o la scorciatoia scelta) la finestra riappare istantaneamente in primo piano!
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
ROOT_DIR = os.path.abspath(os.path.join(BRIDGE_DIR, ".."))
for _p in (ROOT_DIR, BRIDGE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ai_monitor

STORAGE_CONFIG_FILE = os.path.join(BRIDGE_DIR, "storage_config.json")
DEFAULT_DATA_DIR = os.path.join(BRIDGE_DIR, "data")
WEBVIEW_STORAGE_DIR = os.path.join(BRIDGE_DIR, ".webview_data")
APP_URL = f"http://127.0.0.1:{ai_monitor.PORT}/?mode=app"
STATUS_URL = f"http://127.0.0.1:{ai_monitor.PORT}/status"
WINDOW_MODE_URL = f"http://127.0.0.1:{ai_monitor.PORT}/window_mode"
WINDOW_TITLE = "Neko Desktop — ASCII AI Companion"

user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = wintypes.BOOL

HWND_TOPMOST = wintypes.HWND(-1)
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000

DCC_HOTKEY_BLACKLIST = {
    "Alt+Q": "3ds Max: Isolate Selection",
    "Alt+Z": "Blender: X-Ray Toggle / 3ds Max: Zoom",
    "Ctrl+Space": "IDE IntelliSense / Blender: Maximize",
}


def validate_hotkey(hotkey: str) -> tuple:
    normalized = (hotkey or "").replace(" ", "")
    clean = normalized.lower()
    for blacklisted, desc in DCC_HOTKEY_BLACKLIST.items():
        if clean == blacklisted.replace(" ", "").lower():
            return False, f"Scorciatoia protetta: {desc}"
    if clean in ["ctrl+alt+n", "alt+n"]:
        return True, "Scorciatoia valida"
    return True, "Scorciatoia personalizzata accettata"


HOTKEY_MAP = {
    "Ctrl+Alt+N": (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x4E),  # Ctrl + Alt + N (Primaria stile Raycast)
    "Alt+N": (MOD_ALT | MOD_NOREPEAT, 0x4E),                      # Alt + N (Default storico Neko)
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
                hk = data.get("hotkey", "Ctrl+Alt+N")
                is_valid, reason = validate_hotkey(hk)
                if not is_valid:
                    print(f"ATTENZIONE: Scorciatoia '{hk}' rifiutata ({reason})! Riservata ai software 3D/DCC. Uso 'Ctrl+Alt+N'.")
                    return "Ctrl+Alt+N"
                if hk in HOTKEY_MAP:
                    return hk
                norm_hk = hk.replace(" ", "")
                if norm_hk in HOTKEY_MAP:
                    return norm_hk
        except Exception:
            pass
    return "Ctrl+Alt+N"


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


def find_neko_app_hwnd(include_hidden: bool = False):
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, lparam):
        if include_hidden or user32.IsWindowVisible(hwnd):
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


def force_foreground_hwnd(hwnd, keep_topmost: bool = False):
    """Porta la finestra di Neko in primo piano sopra qualsiasi altra app di Windows 11."""
    if not hwnd:
        return
    try:
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # Forza il salto in cima allo Z-order di Windows tramite TOPMOST temporaneo
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        if not keep_topmost:
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


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


def summon_neko_window(pywebview_win):
    """Mostra, ripristina e porta in primo piano la finestra nativa di Neko."""
    if pywebview_win is not None:
        try:
            pywebview_win.show()
            pywebview_win.restore()
        except Exception:
            pass
    hwnd = find_neko_app_hwnd(include_hidden=True)
    is_mini = getattr(ai_monitor.win_conn_manager, "window_mode", "dashboard") == "mini"
    force_foreground_hwnd(hwnd, keep_topmost=is_mini)
    trigger_focus_input(pywebview_win)


def run_hotkey_loop(pywebview_win):
    """
    Ascolta in background la scorciatoia globale configurata (default `Alt + N` per evitare conflitti con Gemini).
    Controlla anche ogni 1.5s se l'utente ha cambiato scorciatoia dal pannello Impostazioni di Neko
    e vigila che il server HTTP locale sia sempre attivo.
    """
    WM_HOTKEY = 0x0312
    PM_REMOVE = 0x0001

    current_hk_name = get_configured_hotkey()
    mods, vk = HOTKEY_MAP.get(current_hk_name, HOTKEY_MAP.get("Ctrl+Alt+N", HOTKEY_MAP["Alt+N"]))
    user32.RegisterHotKey(None, 1, mods, vk)
    # Registra sempre anche Alt+N (ID 2) e Ctrl+Alt+N (ID 3) come alias rapidi
    user32.RegisterHotKey(None, 2, MOD_ALT | MOD_NOREPEAT, 0x4E)
    user32.RegisterHotKey(None, 3, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x4E)

    msg = wintypes.MSG()
    last_check = time.time()
    try:
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY and msg.wParam in (1, 2, 3):
                    summon_neko_window(pywebview_win)
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
                    mods, vk = HOTKEY_MAP.get(current_hk_name, HOTKEY_MAP.get("Ctrl+Alt+N", HOTKEY_MAP["Alt+N"]))
                    user32.RegisterHotKey(None, 1, mods, vk)

            time.sleep(0.03)
    finally:
        user32.UnregisterHotKey(None, 1)
        user32.UnregisterHotKey(None, 2)
        user32.UnregisterHotKey(None, 3)


def main():
    # Se c'è già una finestra di Neko (anche nascosta in background), portala in primo piano ed esci
    existing_hwnd = find_neko_app_hwnd(include_hidden=True)
    if existing_hwnd:
        force_foreground_hwnd(existing_hwnd, keep_topmost=False)
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

    # Quando clicchi la 'X' della finestra, nascondi Neko in background invece di uccidere il processo,
    # così premendo Alt+N la finestra si riapre all'istante!
    def on_closing():
        threading.Thread(target=lambda: window.hide(), daemon=True).start()
        return False

    window.events.closing += on_closing

    os.makedirs(WEBVIEW_STORAGE_DIR, exist_ok=True)
    hk_thread = threading.Thread(target=run_hotkey_loop, args=(window,), daemon=True)
    hk_thread.start()

    webview.start(private_mode=False, storage_path=WEBVIEW_STORAGE_DIR)


if __name__ == "__main__":
    main()
