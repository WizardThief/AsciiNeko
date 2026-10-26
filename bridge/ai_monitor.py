"""
ai_monitor.py - Bridge locale unificato (Zero dipendenze esterne) per ASCII Neko Desktop Companion.
Funzionalità:
  1. Cattura in tempo reale (30 FPS) l'audio di sistema di Windows tramite WASAPI COM
     con filtro Anti-Notifiche (> 2.2s di musica continua).
  2. Rileva se il puntatore del mouse si trova davvero sullo sfondo vuoto del Desktop (is_desktop_under_cursor)
     usando WindowFromPoint + GetClassNameW, così i click fatti dentro altre finestre vengono ignorati.
  3. Motore Assistente AI Locale (Ollama su http://127.0.0.1:11434) con:
     - Cronologia Chat persistente su disco (bridge/chat_history.json) e memoria conversazionale multi-turno
     - Memoria a lungo termine di fatti e preferenze (bridge/memory.json)
     - Integrazione Google Calendar (lettura feed privato iCal .ics + Google Calendar API / Quick-Add link)
       e Agenda locale (bridge/events.json) con promemoria automatici sul desktop
     - Controllo App e File del PC da chat (Avvio programmi dal Menu Start/percorsi comuni, apertura cartelle,
       ricerca file rapida, lettura/riassunto degli Appunti di Windows)
     - Briefing giornaliero unificato (Agenda + To-Do + Memoria + Stato PC)
  4. Sincronizzazione Multi-Schermo per avere UN SOLO Neko che vive sul Monitor 1 e può visitare il Monitor 2.
"""

import ctypes
from ctypes import wintypes, POINTER, Structure, c_float, c_uint32, c_void_p, byref, WINFUNCTYPE, HRESULT
from datetime import datetime, date, timedelta, timezone
import glob
import http.server
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

import shutil
import logging

from bridge.storage_manager import (
    StorageManager,
    StorageError,
    StorageLockError,
    StorageCorruptError,
    get_storage_manager,
)
from bridge.retrieval import (
    TokenBudgetManager,
    build_ollama_payload,
    inject_ollama_options,
)
from bridge.telemetry import (
    TelemetryService,
    get_telemetry_service,
    parse_window_title,
)
from bridge.task_engine import TaskEngine
from bridge.skill_registry import SkillRegistry

logger = logging.getLogger("neko.ai_monitor")

CJK_REGEX = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff]")


def sanitize_anti_cjk(text: str) -> str:
    """
    Rimuove tutti i caratteri ideografici CJK (cinese, giapponese, coreano)
    per eliminare fughe di token e allucinazioni linguistiche dei modelli 7B.
    """
    if not text:
        return ""
    return CJK_REGEX.sub("", str(text)).strip()


DCC_HOTKEY_BLACKLIST = {
    "Alt+Q": "3ds Max: Isolate Selection",
    "Alt+Z": "Blender: X-Ray Toggle / 3ds Max: Zoom",
    "Ctrl+Space": "IDE IntelliSense / Blender: Maximize",
}


def validate_dcc_hotkey(hotkey: str) -> tuple:
    clean = (hotkey or "").replace(" ", "")
    for blacklisted, desc in DCC_HOTKEY_BLACKLIST.items():
        if clean.lower() == blacklisted.replace(" ", "").lower():
            return False, f"Scorciatoia non consentita: {desc}"
    return True, "OK"


PORT = 8765
OLLAMA_BASE = "http://127.0.0.1:11434"
BRIDGE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BRIDGE_DIR, ".."))
USER_HOME = os.path.expanduser("~")
COMPUTER_NAME = os.environ.get("COMPUTERNAME", "PC-LOCALE")

STORAGE_CONFIG_FILE = os.path.join(BRIDGE_DIR, "storage_config.json")
DEFAULT_DATA_DIR = os.path.join(BRIDGE_DIR, "data")
DATA_DIR = DEFAULT_DATA_DIR

TODO_FILE = ""
CHAT_HISTORY_FILE = ""
MEMORY_FILE = ""
PROFILE_FILE = ""
PROJECTS_FILE = ""
ARCHIVE_FILE = ""
CALENDAR_CONFIG_FILE = ""
EVENTS_FILE = ""

DATA_FILENAMES = (
    "profile.json",
    "projects.json",
    "archive.json",
    "memory.json",
    "todo.json",
    "events.json",
    "chat_history.json",
    "calendar_config.json",
)


def _apply_data_dir(custom_dir: str = "", migrate_from: str = ""):
    global DATA_DIR, TODO_FILE, CHAT_HISTORY_FILE, MEMORY_FILE, PROFILE_FILE, PROJECTS_FILE, ARCHIVE_FILE, CALENDAR_CONFIG_FILE, EVENTS_FILE

    target_dir = (custom_dir or "").strip()
    if not target_dir and os.path.isfile(STORAGE_CONFIG_FILE):
        try:
            with open(STORAGE_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                target_dir = (cfg.get("data_dir") or "").strip()
        except Exception:
            target_dir = ""

    if not target_dir:
        target_dir = DEFAULT_DATA_DIR

    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception:
        target_dir = DEFAULT_DATA_DIR
        os.makedirs(target_dir, exist_ok=True)

    # Migra eventuali file JSON legacy presenti nella vecchia cartella (o direttamente in bridge/) senza sovrascrivere file già esistenti nella nuova cartella
    sources_to_check = [BRIDGE_DIR]
    if migrate_from and os.path.isdir(migrate_from) and os.path.abspath(migrate_from) != os.path.abspath(target_dir):
        sources_to_check.insert(0, migrate_from)

    for src_dir in sources_to_check:
        if os.path.abspath(src_dir) == os.path.abspath(target_dir):
            continue
        for fname in DATA_FILENAMES:
            src_path = os.path.join(src_dir, fname)
            dst_path = os.path.join(target_dir, fname)
            if os.path.isfile(src_path) and not os.path.isfile(dst_path):
                try:
                    shutil.copy2(src_path, dst_path)
                except Exception:
                    pass
            # Rimuovi i vecchi file JSON sparsi in bridge/ dopo averli migrati in bridge/data/
            if os.path.abspath(src_dir) == os.path.abspath(BRIDGE_DIR) and os.path.isfile(src_path) and os.path.isfile(dst_path):
                try:
                    os.remove(src_path)
                except Exception:
                    pass

    DATA_DIR = os.path.abspath(target_dir)
    TODO_FILE = os.path.join(DATA_DIR, "todo.json")
    CHAT_HISTORY_FILE = os.path.join(DATA_DIR, "chat_history.json")
    MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")
    PROFILE_FILE = os.path.join(DATA_DIR, "profile.json")
    PROJECTS_FILE = os.path.join(DATA_DIR, "projects.json")
    ARCHIVE_FILE = os.path.join(DATA_DIR, "archive.json")
    CALENDAR_CONFIG_FILE = os.path.join(DATA_DIR, "calendar_config.json")
    EVENTS_FILE = os.path.join(DATA_DIR, "events.json")

    # Initialize StorageManager & run non-destructive migration
    try:
        sm = get_storage_manager(data_dir=DATA_DIR)
        sm.migrate_legacy_data(DATA_DIR)
    except Exception as e:
        logger.warning(f"Storage migration warning: {e}")

    global _task_engine_instance, _skill_registry_instance
    _task_engine_instance = None
    _skill_registry_instance = None

    return DATA_DIR


_task_engine_instance: Optional[TaskEngine] = None
_skill_registry_instance: Optional[SkillRegistry] = None


def get_task_engine() -> TaskEngine:
    global _task_engine_instance
    if _task_engine_instance is None:
        sm = get_storage_manager(data_dir=DATA_DIR)
        _task_engine_instance = TaskEngine(storage_manager=sm)
    return _task_engine_instance


def get_skill_registry() -> SkillRegistry:
    global _skill_registry_instance
    if _skill_registry_instance is None:
        sm = get_storage_manager(data_dir=DATA_DIR)
        skills_path = os.path.join(DATA_DIR, "skills")
        _skill_registry_instance = SkillRegistry(skills_dir=skills_path, storage_manager=sm)
    return _skill_registry_instance


_apply_data_dir()


# --- Helpers JSON su disco (Delegated to Transactional StorageManager) ---
def _load_json(path, default):
    """
    Safely loads JSON using StorageManager atomic reader.
    Handles Windows file contention and preserves corrupt files to .corrupt.bak.
    """
    try:
        sm = get_storage_manager()
        return sm.atomic_load_json(path, default=default)
    except StorageCorruptError as ce:
        logger.error(f"CORRUPT DATA DETECTED: {ce}")
        return default
    except Exception as e:
        logger.warning(f"Error loading {path}: {e}")
        return default


def _save_json(path, data):
    """
    Atomically writes JSON using uuid4 tmp file, msvcrt OS locking, and exponential backoff.
    Completely eliminates VULN-01 (WinError 32 lost updates).
    """
    try:
        sm = get_storage_manager()
        return sm.atomic_save_json(path, data)
    except Exception as e:
        logger.error(f"Failed atomic save to {path}: {e}")
        return False


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


class CalendarManager:
    """
    Gestisce l'integrazione con Google Calendar (tramite feed iCal .ics privato e/o Google Calendar REST API)
    più gli appuntamenti/scadenze locali salvati in bridge/events.json.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.config = _load_json(
            CALENDAR_CONFIG_FILE,
            {
                "ical_urls": [],
                "google_access_token": "",
                "google_calendar_id": "primary",
                "reminder_minutes": 15,
            },
        )
        self.local_events = _load_json(EVENTS_FILE, [])
        self.remote_events = []
        self.last_fetch_ts = 0.0
        self.last_error = ""
        self._thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._thread.start()

    def _sync_loop(self):
        while True:
            try:
                self.refresh_remote_events()
            except Exception:
                pass
            time.sleep(240)  # Aggiorna ogni 4 minuti

    def get_config(self):
        with self._lock:
            return dict(self.config)

    def update_config(self, ical_url=None, google_access_token=None, clear_urls=False):
        with self._lock:
            if clear_urls:
                self.config["ical_urls"] = []
            if ical_url is not None:
                clean_url = ical_url.strip()
                if clean_url:
                    urls = [u for u in self.config.get("ical_urls", []) if u != clean_url]
                    urls.insert(0, clean_url)
                    self.config["ical_urls"] = urls[:5]
                else:
                    self.config["ical_urls"] = []
            if google_access_token is not None:
                self.config["google_access_token"] = google_access_token.strip()
            _save_json(CALENDAR_CONFIG_FILE, self.config)
        self.refresh_remote_events(force=True)
        return self.get_config()

    def _parse_ics_datetime(self, raw_val):
        """Converte un valore DTSTART/DTEND iCal in datetime locale naive."""
        val = (raw_val or "").strip()
        if not val:
            return None, False
        # Eventi tutto il giorno: YYYYMMDD
        if len(val) == 8 and val.isdigit():
            try:
                dt = datetime.strptime(val, "%Y%m%d")
                return dt, True
            except Exception:
                return None, False
        # Formato con ora: YYYYMMDDTHHMMSSZ o YYYYMMDDTHHMMSS
        is_utc = val.endswith("Z")
        clean = val.rstrip("Z")
        if "T" in clean:
            parts = clean.split("T")
            d_part = parts[0][:8]
            t_part = parts[1][:6].ljust(6, "0")
            try:
                dt = datetime.strptime(d_part + t_part, "%Y%m%d%H%M%S")
                if is_utc:
                    dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
                return dt, False
            except Exception:
                return None, False
        return None, False

    def _parse_ics_text(self, ics_text):
        """Parser nativo per file iCalendar (.ics) di Google Calendar / Outlook / Apple."""
        # Unfolding RFC 5545 (righe che iniziano con spazio o tab continuano la riga precedente)
        unfolded = re.sub(r"\r?\n[ \t]", "", ics_text or "")
        events = []
        in_event = False
        curr = {}

        for line in unfolded.splitlines():
            line = line.strip()
            if line == "BEGIN:VEVENT":
                in_event = True
                curr = {}
                continue
            elif line == "END:VEVENT":
                in_event = False
                dt_start, all_day = self._parse_ics_datetime(curr.get("DTSTART", ""))
                if dt_start:
                    dt_end, _ = self._parse_ics_datetime(curr.get("DTEND", ""))
                    summary = curr.get("SUMMARY", "Impegno a calendario")
                    summary = summary.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ")
                    location = curr.get("LOCATION", "").replace("\\,", ",")
                    events.append(
                        {
                            "id": curr.get("UID", f"ics_{int(dt_start.timestamp())}"),
                            "title": summary,
                            "start_iso": dt_start.strftime("%Y-%m-%dT%H:%M:%S"),
                            "end_iso": dt_end.strftime("%Y-%m-%dT%H:%M:%S") if dt_end else "",
                            "all_day": all_day,
                            "location": location,
                            "source": "google_ics",
                        }
                    )
                continue

            if in_event and ":" in line:
                key_part, val_part = line.split(":", 1)
                base_key = key_part.split(";", 1)[0].upper()
                curr[base_key] = val_part

        return events

    def refresh_remote_events(self, force=False):
        now = time.time()
        if not force and (now - self.last_fetch_ts < 60):
            return
        self.last_fetch_ts = now

        cfg = self.get_config()
        urls = cfg.get("ical_urls") or []
        token = cfg.get("google_access_token") or ""
        cal_id = cfg.get("google_calendar_id") or "primary"

        collected = []
        err_msg = ""

        # 1. Scarica da URL iCal (.ics)
        for url in urls:
            if not url:
                continue
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "AsciiNeko-Companion/2.0"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    raw_ics = resp.read().decode("utf-8", errors="replace")
                    collected.extend(self._parse_ics_text(raw_ics))
            except Exception as e:
                err_msg = f"Errore lettura iCal: {str(e)}"

        # 2. Se configurato un token OAuth Google Calendar API
        if token:
            try:
                t_min = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                t_max = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
                q = urllib.parse.urlencode(
                    {
                        "timeMin": t_min,
                        "timeMax": t_max,
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        "maxResults": "50",
                    }
                )
                api_url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal_id)}/events?{q}"
                req = urllib.request.Request(
                    api_url,
                    headers={"Authorization": f"Bearer {token}"},
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=8.0) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for item in data.get("items", []):
                        st = item.get("start", {})
                        et = item.get("end", {})
                        dt_str = st.get("dateTime") or st.get("date")
                        if not dt_str:
                            continue
                        all_day = "date" in st and "dateTime" not in st
                        if all_day:
                            dt_obj = datetime.strptime(dt_str[:10], "%Y-%m-%d")
                        else:
                            dt_obj = datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
                        collected.append(
                            {
                                "id": item.get("id", ""),
                                "title": item.get("summary", "Evento Google Calendar"),
                                "start_iso": dt_obj.strftime("%Y-%m-%dT%H:%M:%S"),
                                "end_iso": et.get("dateTime", "")[:19],
                                "all_day": all_day,
                                "location": item.get("location", ""),
                                "source": "google_api",
                            }
                        )
            except Exception as e:
                err_msg = f"Errore API Google Calendar: {str(e)}"

        with self._lock:
            self.remote_events = collected
            self.last_error = err_msg

    def add_event(self, title, start_dt: datetime, duration_minutes=60):
        end_dt = start_dt + timedelta(minutes=duration_minutes)
        ev = {
            "id": f"loc_{int(time.time() * 1000)}",
            "title": title.strip(),
            "start_iso": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "end_iso": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "all_day": False,
            "location": "",
            "source": "local",
        }
        with self._lock:
            self.local_events.append(ev)
            _save_json(EVENTS_FILE, self.local_events)

        # Se c'è un token OAuth Google Calendar attivo, prova a crearlo anche su Google Calendar API
        token = self.config.get("google_access_token", "")
        cal_id = self.config.get("google_calendar_id", "primary")
        created_on_google = False
        if token:
            try:
                local_tz = datetime.now().astimezone().tzinfo
                body = {
                    "summary": ev["title"],
                    "start": {"dateTime": start_dt.replace(tzinfo=local_tz).isoformat()},
                    "end": {"dateTime": end_dt.replace(tzinfo=local_tz).isoformat()},
                }
                api_url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal_id)}/events"
                req = urllib.request.Request(
                    api_url,
                    data=json.dumps(body).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=6.0) as resp:
                    if resp.status in (200, 201):
                        created_on_google = True
            except Exception:
                pass

        # Genera anche il link rapido di Google Calendar per aggiunta con 1 click nel browser
        gcal_fmt_start = start_dt.strftime("%Y%m%dT%H%M%S")
        gcal_fmt_end = end_dt.strftime("%Y%m%dT%H%M%S")
        gcal_quick_url = (
            "https://calendar.google.com/calendar/render?action=TEMPLATE"
            f"&text={urllib.parse.quote(ev['title'])}"
            f"&dates={gcal_fmt_start}/{gcal_fmt_end}"
        )
        ev["gcal_url"] = gcal_quick_url
        ev["created_on_google"] = created_on_google
        return ev

    def delete_local_event(self, index_or_title):
        with self._lock:
            if not self.local_events:
                return False
            if str(index_or_title).isdigit():
                idx = int(index_or_title) - 1
                if 0 <= idx < len(self.local_events):
                    self.local_events.pop(idx)
                    _save_json(EVENTS_FILE, self.local_events)
                    return True
            q = str(index_or_title).lower().strip()
            before = len(self.local_events)
            self.local_events = [e for e in self.local_events if q not in e.get("title", "").lower()]
            if len(self.local_events) < before:
                _save_json(EVENTS_FILE, self.local_events)
                return True
        return False

    def get_upcoming_events(self, days_ahead=7):
        now = datetime.now()
        start_of_today = datetime(now.year, now.month, now.day)
        limit_dt = start_of_today + timedelta(days=days_ahead)

        with self._lock:
            all_raw = list(self.local_events) + list(self.remote_events)

        upcoming = []
        seen_keys = set()
        for ev in all_raw:
            try:
                dt = datetime.fromisoformat(ev["start_iso"])
            except Exception:
                continue
            if dt < start_of_today - timedelta(hours=2) or dt > limit_dt:
                continue
            dedup = f"{ev.get('title', '').lower()}_{dt.strftime('%Y-%m-%d_%H:%M')}"
            if dedup in seen_keys:
                continue
            seen_keys.add(dedup)

            mins_until = int((dt - now).total_seconds() / 60)
            is_today = dt.date() == now.date()
            is_tomorrow = dt.date() == (now.date() + timedelta(days=1))
            day_label = "Oggi" if is_today else ("Domani" if is_tomorrow else dt.strftime("%d/%m"))
            time_label = "Tutto il giorno" if ev.get("all_day") else dt.strftime("%H:%M")

            item = dict(ev)
            item["day_label"] = day_label
            item["time_label"] = time_label
            item["minutes_until"] = mins_until
            item["is_today"] = is_today
            item["imminent"] = (not ev.get("all_day")) and (-10 <= mins_until <= int(self.config.get("reminder_minutes", 15)))
            upcoming.append(item)

        upcoming.sort(key=lambda x: x["start_iso"])
        return upcoming

    def get_summary_state(self):
        upcoming = self.get_upcoming_events(days_ahead=7)
        today_events = [e for e in upcoming if e.get("is_today")]
        imminent = [e for e in upcoming if e.get("imminent")]
        next_ev = None
        for e in upcoming:
            if e.get("minutes_until", -999) >= -15 or e.get("all_day"):
                next_ev = e
                break
        cfg = self.get_config()
        return {
            "configured": bool(cfg.get("ical_urls") or cfg.get("google_access_token")),
            "ical_urls": cfg.get("ical_urls", []),
            "has_google_token": bool(cfg.get("google_access_token")),
            "today_count": len(today_events),
            "upcoming_count": len(upcoming),
            "next_event": next_ev,
            "imminent_events": imminent,
            "upcoming_events": upcoming[:15],
            "last_error": self.last_error,
        }


class WindowsPCActions:
    """
    Permette a Neko di interagire con le applicazioni del PC Windows:
    - Avviare programmi installati (cercando nei percorsi noti e nei collegamenti .lnk del Menu Start)
    - Aprire cartelle rapide (Desktop, Download, Documenti, Progetti) o URL
    - Cercare file sul PC
    - Leggere il contenuto degli Appunti di Windows (Clipboard)
    """

    KNOWN_FOLDERS = {
        "desktop": os.path.join(USER_HOME, "Desktop"),
        "download": os.path.join(USER_HOME, "Downloads"),
        "downloads": os.path.join(USER_HOME, "Downloads"),
        "scaricati": os.path.join(USER_HOME, "Downloads"),
        "documenti": os.path.join(USER_HOME, "Documents"),
        "documents": os.path.join(USER_HOME, "Documents"),
        "immagini": os.path.join(USER_HOME, "Pictures"),
        "pictures": os.path.join(USER_HOME, "Pictures"),
        "video": os.path.join(USER_HOME, "Videos"),
        "musica": os.path.join(USER_HOME, "Music"),
        "home": USER_HOME,
        "utente": USER_HOME,
        "progetto": PROJECT_ROOT,
        "asciineko": PROJECT_ROOT,
    }

    KNOWN_URLS = {
        "google calendar": "https://calendar.google.com",
        "calendario": "https://calendar.google.com",
        "gmail": "https://mail.google.com",
        "youtube": "https://www.youtube.com",
        "github": "https://github.com/WizardThief/AsciiNeko",
        "chatgpt": "https://chatgpt.com",
    }

    KNOWN_COMMANDS = {
        "calcolatrice": "calc.exe",
        "calc": "calc.exe",
        "blocco note": "notepad.exe",
        "notepad": "notepad.exe",
        "paint": "mspaint.exe",
        "esplora file": "explorer.exe",
        "esplora risorse": "explorer.exe",
        "explorer": "explorer.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "terminale": "wt.exe",
        "task manager": "taskmgr.exe",
        "gestione attività": "taskmgr.exe",
        "impostazioni": "ms-settings:",
        "code": "code",
        "vs code": "code",
        "vscode": "code",
        "visual studio code": "code",
    }

    @classmethod
    def open_target(cls, target_name: str):
        raw = (target_name or "").strip().strip("\"'")
        low = raw.lower()
        if not low:
            return False, "Specifica il nome dell'applicazione o della cartella da aprire!"

        # 1. URL noti (es. "Apri Google Calendar", "Apri Gmail", "Apri YouTube")
        for k, url in cls.KNOWN_URLS.items():
            if low == k or low.startswith(k):
                os.startfile(url)
                return True, f"🌐 Ho aperto **{k.title()}** nel browser!"

        if low.startswith("http://") or low.startswith("https://"):
            os.startfile(raw)
            return True, f"🌐 Ho aperto il link: {raw}"

        # 2. Cartelle utente note
        for k, folder_path in cls.KNOWN_FOLDERS.items():
            if low == k or low == f"cartella {k}":
                if os.path.isdir(folder_path):
                    os.startfile(folder_path)
                    return True, f"📂 Ho aperto la cartella **{k.title()}** (`{folder_path}`)"

        # 3. Percorso diretto su disco o su Server di rete (NAS / UNC)
        if os.path.exists(raw):
            os.startfile(raw)
            return True, f"📂 Ho aperto `{raw}`"
        if raw.startswith("\\\\") or (len(raw) > 3 and raw[1:3] == ":\\" and not os.path.exists(raw[:3])):
            return (
                False,
                f"🏢 Il percorso `{raw}` non è raggiungibile da questo PC (`{COMPUTER_NAME}`). "
                "Probabilmente si trova sul Server dell'ufficio — le Note Cliente e i To-Do del progetto restano comunque consultabili qui!",
            )

        # 4. Comandi rapidi di sistema Windows
        for k, exe in cls.KNOWN_COMMANDS.items():
            if low == k:
                try:
                    os.startfile(exe)
                    return True, f"🚀 Ho avviato **{k.title()}**!"
                except Exception:
                    try:
                        subprocess.Popen(exe, shell=True)
                        return True, f"🚀 Ho avviato **{k.title()}**!"
                    except Exception:
                        pass

        # 5. Ricerca automatica nei collegamenti (.lnk) del Menu Start di Windows
        start_dirs = [
            os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(USER_HOME, "Desktop"),
        ]
        best_match = None
        for sdir in start_dirs:
            if not os.path.isdir(sdir):
                continue
            for root, _, files in os.walk(sdir):
                for fname in files:
                    if fname.lower().endswith((".lnk", ".url", ".exe")):
                        stem = os.path.splitext(fname)[0].lower()
                        if low == stem:
                            best_match = os.path.join(root, fname)
                            break
                        elif low in stem and not best_match:
                            best_match = os.path.join(root, fname)
                if best_match and os.path.splitext(os.path.basename(best_match))[0].lower() == low:
                    break

        if best_match:
            try:
                os.startfile(best_match)
                app_title = os.path.splitext(os.path.basename(best_match))[0]
                return True, f"🚀 Ho avviato **{app_title}**!"
            except Exception as e:
                return False, f"⚠️ Ho trovato `{best_match}` ma non sono riuscito ad avviarlo: {e}"

        return False, f"⚠️ Non ho trovato l'applicazione o cartella **\"{raw}\"** nel Menu Start o nelle cartelle note."

    @classmethod
    def search_files(cls, query: str, max_results: int = 6):
        q = (query or "").strip().lower()
        if not q:
            return []
        search_roots = [
            os.path.join(USER_HOME, "Desktop"),
            os.path.join(USER_HOME, "Documents"),
            os.path.join(USER_HOME, "Downloads"),
            os.path.join(USER_HOME, "WizardThief"),
        ]
        matches = []
        seen = set()
        for rdir in search_roots:
            if not os.path.isdir(rdir):
                continue
            for root, dirs, files in os.walk(rdir):
                # Evita cartelle di cache pesanti
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in (".git", "node_modules", "__pycache__", ".venv", "venv", "AppData")
                ]
                for item in files + dirs:
                    if q in item.lower():
                        full = os.path.join(root, item)
                        if full not in seen:
                            seen.add(full)
                            matches.append(
                                {
                                    "name": item,
                                    "path": full,
                                    "parent": root,
                                    "is_dir": os.path.isdir(full),
                                }
                            )
                            if len(matches) >= max_results:
                                return matches
        return matches

    @classmethod
    def read_clipboard(cls) -> str:
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                startupinfo=startupinfo,
                text=True,
                timeout=2.5,
            )
            return (out or "").strip()
        except Exception:
            return ""


class MemoryAndProjectsManager:
    """
    Gestisce il ciclo di vita della Memoria a 3 Livelli di Neko:
      1. Livello 1 — Profilo Utente Base (`bridge/profile.json`) + Motore Intervista "🐾 Conosciamoci"
      2. Livello 2 — Schede Progetto Attive (`bridge/projects.json`) + Pin Rapidi Server (Zero Scansione)
      3. Livello 3 — Archivio Storico a Lungo Termine (`bridge/archive.json`) con:
         - Check inattività progetti (>14 giorni)
         - Risveglio automatico preferenze quando ritorna un vecchio cliente
         - Rilevamento passivo in chat ("🧠 Salvo questo?")
    """

    ONBOARDING_QUESTIONS = [
        {
            "id": "q_identity",
            "category": "Identità & Attività",
            "question": "Ciao! Facciamo conoscenza: come ti chiami e di cosa ti occupi principalmente (lavoro, studio o attività)?",
            "options": [
                "Sviluppo Software, Programmazione o IT",
                "Design, Grafica, Architettura / 3D o Creatività",
                "Business, Management, Marketing o Consulenza",
                "Studio, Ricerca o Libera Professione",
            ],
            "trait_prefix": "Identità e attività principale",
        },
        {
            "id": "q_tools",
            "category": "Lavoro & Strumenti",
            "question": "Quali sono i programmi, gli strumenti o le piattaforme che usi di più ogni giorno sul PC?",
            "options": [
                "Editor di codice, Terminale, Git e strumenti di sviluppo",
                "Software grafici, 3D, CAD o montaggio video",
                "Browser, Suite Office / Google Workspace, Notion e Mail",
                "Un mix di software tecnici, gestionali e creativi",
            ],
            "trait_prefix": "Strumenti usati quotidianamente",
        },
        {
            "id": "q_projects",
            "category": "Clienti & Progetti",
            "question": "Su che tipo di progetti, clienti o obiettivi ti stai concentrando in questo periodo?",
            "options": [
                "Progetti per clienti con scadenze e revisioni da tracciare",
                "Sviluppo di prodotti, app o progetti personali",
                "Gestione operativa di task, scadenze e riunioni",
                "Studio, formazione o ricerca su nuovi argomenti",
            ],
            "trait_prefix": "Focus progetti e attività",
        },
        {
            "id": "q_routine",
            "category": "Routine & Orari",
            "question": "Qual è la cosa in cui un assistente sulla scrivania ti farebbe più comodo durante la giornata?",
            "options": [
                "Ricordarmi impegni, call e To-Do senza farmi perdere il filo",
                "Tenere traccia delle note e regole di ogni progetto o cliente",
                "Aiutarmi al volo con riassunti, bozze, calcoli o problemi tecnici",
                "Organizzare la giornata al mattino e fare il punto a fine lavoro",
            ],
            "trait_prefix": "Supporto prioritario richiesto a Neko",
        },
        {
            "id": "q_style",
            "category": "Stile & Preferenze",
            "question": "Come preferisci che Neko comunichi e interagisca con te?",
            "options": [
                "Diretto, sintetico e super pratico (2-3 righe)",
                "Organizzato per punti elenco chiari e azionabili",
                "Amichevole e propositivo, suggerendo subito l'azione giusta",
                "Tecnico, preciso e focalizzato sulla soluzione",
            ],
            "trait_prefix": "Stile di comunicazione preferito",
        },
    ]

    DEFAULT_QUICK_LINKS = [
        {"label": "📅 Google Calendar", "url": "https://calendar.google.com"},
        {"label": "✉️ Gmail", "url": "https://mail.google.com"},
        {"label": "📂 Google Drive", "url": "https://drive.google.com"},
        {"label": "🐙 GitHub AsciiNeko", "url": "https://github.com/WizardThief/AsciiNeko"},
    ]

    def __init__(self):
        self._lock = threading.RLock()
        self.profile = _load_json(
            PROFILE_FILE,
            {
                "name": "",
                "answers": {},
                "traits": [],
                "quick_links": list(self.DEFAULT_QUICK_LINKS),
                "active_project_id": "",
                "last_ai_question": None,
            },
        )
        if "answers" not in self.profile or not isinstance(self.profile["answers"], dict):
            self.profile["answers"] = {}
        if "traits" not in self.profile or not isinstance(self.profile["traits"], list):
            self.profile["traits"] = []
        if "quick_links" not in self.profile or not isinstance(self.profile["quick_links"], list):
            self.profile["quick_links"] = list(self.DEFAULT_QUICK_LINKS)
        if "hotkey" not in self.profile or not self.profile["hotkey"]:
            self.profile["hotkey"] = "Alt+N"

        self.projects = _load_json(PROJECTS_FILE, [])
        if not isinstance(self.projects, list):
            self.projects = []
        self.archive = _load_json(ARCHIVE_FILE, [])
        if not isinstance(self.archive, list):
            self.archive = []

    def _save_profile(self):
        _save_json(PROFILE_FILE, self.profile)

    def _save_projects(self):
        _save_json(PROJECTS_FILE, self.projects)

    def _save_archive(self):
        _save_json(ARCHIVE_FILE, self.archive)

    def reload_from_disk(self):
        with self._lock:
            self.profile = _load_json(PROFILE_FILE, self.profile)
            self.projects = _load_json(PROJECTS_FILE, [])
            if not isinstance(self.projects, list):
                self.projects = []
            self.archive = _load_json(ARCHIVE_FILE, [])
            if not isinstance(self.archive, list):
                self.archive = []

    # --- LIVELLO 1: PROFILO UTENTE & TUTORIAL DI PROFILAZIONE ADATTIVO AI ("🐾 CONOSCIAMOCI") ---
    def _rebuild_traits(self):
        traits = []
        q_map = {q["id"]: q for q in self.ONBOARDING_QUESTIONS}
        for qid, ans_obj in self.profile.get("answers", {}).items():
            ai_summary = sanitize_anti_cjk((ans_obj.get("ai_trait") or "").strip())
            if ai_summary:
                traits.append(ai_summary)
                continue
            ans_text = sanitize_anti_cjk((ans_obj.get("answer") or "").strip())
            if not ans_text:
                continue
            prefix = sanitize_anti_cjk(q_map[qid]["trait_prefix"] if qid in q_map else ans_obj.get("trait_prefix") or ans_obj.get("category", "Profilo"))
            traits.append(f"{prefix}: {ans_text}")
        self.profile["traits"] = traits

    def _call_ollama_profiling(self, latest_question: str = "", latest_answer: str = "", category_hint: str = ""):
        """
        Usa il modello locale Ollama (es. qwen2.5:7b) in tempo reale per:
        1. Sintetizzare l'ultima risposta dell'utente in un tratto di memoria chiaro e universale (`extracted_trait`).
        2. Generare una nuova domanda di profilazione su misura per il lavoro/vita dell'utente (`next_question`)
           con 4 opzioni cliccabili pertinenti.
        """
        try:
            # Scegli il modello attivo o cerca il primo disponibile
            req_tags = urllib.request.Request(f"{OLLAMA_BASE}/api/tags", method="GET")
            with urllib.request.urlopen(req_tags, timeout=1.5) as r_tags:
                models_data = json.loads(r_tags.read().decode("utf-8")).get("models", [])
                model_names = [m.get("name", "") for m in models_data if m.get("name")]
            chat_models = [
                m for m in model_names if not any(x in m.lower() for x in ("embed", "nomic", "bge", "clef"))
            ]
            chat_models.sort(key=lambda n: (0 if n.lower().startswith("qwen2.5") else 1, n.lower()))
            model = self.profile.get("preferred_model") or (chat_models[0] if chat_models else "")
            if not model:
                return None

            existing_traits = list(self.profile.get("traits", []))
            answered_questions = [
                f"- D: {v.get('question', '')} -> R: {v.get('answer', '')}"
                for v in list(self.profile.get("answers", {}).values())[-8:]
            ]
            context_str = "\n".join(answered_questions) if answered_questions else "Nessuna risposta precedente."
            cat_instruction = (
                f"Concentra la prossima domanda sul tema '{category_hint}'."
                if category_hint
                else "Scegli l'aspetto più utile da scoprire ora per profilare al meglio l'utente (il suo lavoro specifico, come organizza le attività, che strumenti usa, quali sono i suoi obiettivi o preferenze)."
            )

            prompt = (
                "Sei il motore di profilazione intelligente di Neko, un assistente AI personale da scrivania.\n"
                "Il tuo compito è conoscere l'utente passo dopo passo (qualunque sia il suo lavoro o percorso: programmatore, designer, medico, studente, artigiano, manager, ecc.) senza dare nulla per scontato.\n\n"
                f"STORICO RISPOSTE GIÀ DATE DALL'UTENTE:\n{context_str}\n\n"
            )
            if latest_answer:
                prompt += (
                    f"ULTIMA DOMANDA FATTA: {latest_question}\n"
                    f"ULTIMA RISPOSTA DELL'UTENTE: {latest_answer}\n\n"
                )
            prompt += (
                f"{cat_instruction}\n"
                "Rispondi ESCLUSIVAMENTE con un oggetto JSON valido (nessun testo fuori dal JSON) con questa struttura esatta:\n"
                "{\n"
                '  "extracted_trait": "Sintesi chiara e concisa in terza persona di ciò che hai appena imparato dall\'ultima risposta (es. \'Professione: Architetto specializzato in ristrutturazioni\' oppure \'Strumenti chiave: Python, VS Code e Docker\'). Lascia stringa vuota se non c\'è un\'ultima risposta",\n'
                '  "category": "Una tra: Identità & Attività | Lavoro & Strumenti | Clienti & Progetti | Routine & Orari | Stile & Preferenze",\n'
                '  "trait_prefix": "Titolo breve del nuovo aspetto indagato (es. Flusso di lavoro quotidiano)",\n'
                '  "question": "Una domanda naturale, curiosa e specifica basata su quello che l\'utente ti ha già detto (non ripetere domande già fatte!)",\n'
                '  "options": ["Opzione rapida 1 su misura", "Opzione rapida 2 su misura", "Opzione rapida 3 su misura", "Opzione rapida 4 su misura"]\n'
                "}"
            )

            payload = build_ollama_payload(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                stream=False,
                num_ctx=8192,
            )
            req = urllib.request.Request(
                f"{OLLAMA_BASE}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                raw = json.loads(resp.read().decode("utf-8")).get("message", {}).get("content", "").strip()
                clean_raw = sanitize_anti_cjk(raw)
                try:
                    parsed = json.loads(clean_raw)
                except Exception:
                    parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("question"):
                    opts = parsed.get("options")
                    if not isinstance(opts, list) or len(opts) < 2:
                        opts = [
                            "Sì, esattamente così",
                            "In parte, dipende dal progetto",
                            "Preferisco gestire questo aspetto in modo flessibile",
                            "Ti spiego meglio a parole mie qui sotto",
                        ]
                    clean_trait = sanitize_anti_cjk(parsed.get("extracted_trait") or "")
                    clean_q = sanitize_anti_cjk(parsed["question"])
                    clean_opts = [sanitize_anti_cjk(str(o)) for o in opts[:4] if sanitize_anti_cjk(str(o))]
                    clean_prefix = sanitize_anti_cjk(parsed.get("trait_prefix") or "Profilo")
                    clean_cat = sanitize_anti_cjk(parsed.get("category") or category_hint or "Profilo Su Misura AI")
                    return {
                        "extracted_trait": clean_trait,
                        "next_question": {
                            "id": f"q_ai_{int(time.time() * 1000)}",
                            "category": clean_cat,
                            "question": clean_q,
                            "options": clean_opts,
                            "trait_prefix": clean_prefix,
                            "ai_generated": True,
                        },
                    }
        except Exception:
            pass
        return None

    REQUIRED_TUTORIAL_STEPS = 5

    def get_onboarding_state(self, category=None, exclude_id=None, generate_ai=False):
        with self._lock:
            answered = self.profile.get("answers", {})
            ans_count = len(answered)
            if ans_count >= self.REQUIRED_TUTORIAL_STEPS and not self.profile.get("tutorial_completed"):
                self.profile["tutorial_completed"] = True
                self._save_profile()
            tutorial_completed = bool(self.profile.get("tutorial_completed")) or (ans_count >= self.REQUIRED_TUTORIAL_STEPS)
            categories = [
                "Identità & Attività",
                "Lavoro & Strumenti",
                "Clienti & Progetti",
                "Routine & Orari",
                "Stile & Preferenze",
            ]
            next_q = self.get_random_question(category=category, exclude_id=exclude_id, generate_ai=generate_ai)
            return {
                "profile": self.profile,
                "categories": categories,
                "questions": self.ONBOARDING_QUESTIONS,
                "next_question": next_q,
                "answered_count": ans_count,
                "required_steps": self.REQUIRED_TUTORIAL_STEPS,
                "tutorial_completed": tutorial_completed,
                "total_questions": max(len(self.ONBOARDING_QUESTIONS), ans_count + 1),
            }

    def get_random_question(self, category=None, exclude_id=None, generate_ai=False):
        with self._lock:
            answered = self.profile.get("answers", {})

            # 1. Se l'utente ha chiesto esplicitamente una nuova domanda AI (o se ha già risposto alle prime domande base)
            if generate_ai and len(answered) >= 1:
                ai_res = self._call_ollama_profiling(category_hint=category or "")
                if ai_res and ai_res.get("next_question"):
                    self.profile["last_ai_question"] = ai_res["next_question"]
                    self._save_profile()
                    return ai_res["next_question"]

            # 2. Se c'è già una domanda AI generata dopo l'ultima risposta e non stiamo chiedendo di cambiarla
            cached_ai_q = self.profile.get("last_ai_question")
            if (
                cached_ai_q
                and isinstance(cached_ai_q, dict)
                and cached_ai_q.get("id") != exclude_id
                and cached_ai_q.get("id") not in answered
                and (not category or cached_ai_q.get("category") == category)
            ):
                return cached_ai_q

            # 3. Altrimenti proponi in ordine le domande seme universali non ancora risposte
            pool = [
                q
                for q in self.ONBOARDING_QUESTIONS
                if (not category or q["category"] == category) and q["id"] != exclude_id
            ]
            if not pool:
                pool = [q for q in self.ONBOARDING_QUESTIONS if q["id"] != exclude_id] or list(
                    self.ONBOARDING_QUESTIONS
                )

            unanswered = [q for q in pool if q["id"] not in answered]
            if unanswered:
                return unanswered[0]

            # 4. Se tutte le domande base sono state risposte, genera una domanda su misura con l'AI!
            ai_res = self._call_ollama_profiling(category_hint=category or "")
            if ai_res and ai_res.get("next_question"):
                self.profile["last_ai_question"] = ai_res["next_question"]
                self._save_profile()
                return ai_res["next_question"]

            idx = int(time.time() * 1000) % len(pool)
            return pool[idx]

    def save_onboarding_answer(self, question_id: str, answer_text: str, category: str = "", question_text: str = ""):
        clean_ans = (answer_text or "").strip()
        if not clean_ans:
            return self.get_onboarding_state(category=category)

        with self._lock:
            q_obj = next((q for q in self.ONBOARDING_QUESTIONS if q["id"] == question_id), None)
            cached_ai_q = self.profile.get("last_ai_question")
            if not q_obj and isinstance(cached_ai_q, dict) and cached_ai_q.get("id") == question_id:
                q_obj = cached_ai_q

            cat = category or (q_obj["category"] if q_obj else "Profilo")
            q_txt = question_text or (q_obj["question"] if q_obj else question_id)
            t_pref = q_obj.get("trait_prefix", cat) if q_obj else cat

            self.profile["answers"][question_id] = {
                "category": cat,
                "question": q_txt,
                "trait_prefix": t_pref,
                "answer": clean_ans,
                "ai_trait": "",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }

            # Elabora in tempo reale con Ollama per sintetizzare il tratto e generare la prossima domanda su misura!
            ai_res = self._call_ollama_profiling(
                latest_question=q_txt,
                latest_answer=clean_ans,
                category_hint="",
            )
            if ai_res:
                if ai_res.get("extracted_trait"):
                    self.profile["answers"][question_id]["ai_trait"] = ai_res["extracted_trait"]
                if ai_res.get("next_question"):
                    self.profile["last_ai_question"] = ai_res["next_question"]

            self._rebuild_traits()
            self._save_profile()

            if question_id.startswith("q_ctx_"):
                proj_id = question_id[len("q_ctx_") :]
                self.add_project_note(proj_id, clean_ans)

            return self.get_onboarding_state(category=None, exclude_id=question_id)

    def delete_onboarding_answer(self, question_id: str):
        with self._lock:
            answers = self.profile.get("answers", {})
            if question_id in answers:
                del answers[question_id]
                self._rebuild_traits()
                self._save_profile()
            return self.get_onboarding_state()

    def set_hotkey(self, hotkey: str):
        clean_input = (hotkey or "Ctrl+Alt+N").strip()
        ok, reason = validate_dcc_hotkey(clean_input)
        if not ok:
            logger.warning(f"Rejected blacklisted hotkey '{clean_input}': {reason}")
            return self.profile.get("hotkey", "Ctrl+Alt+N")
        hk_map = {
            "ctrl+alt+n": "Ctrl+Alt+N",
            "alt+n": "Alt+N",
        }
        clean_hk = hk_map.get(clean_input.lower(), "Ctrl+Alt+N")
        with self._lock:
            self.profile["hotkey"] = clean_hk
            self._save_profile()
            return clean_hk

    def pin_profile_trait(self, trait_id: str, pinned: bool = True):
        with self._lock:
            if "pinned_traits" not in self.profile or not isinstance(self.profile["pinned_traits"], list):
                self.profile["pinned_traits"] = []
            sm = get_storage_manager()
            # Try to pin in SQLite by ID
            sm.pin_memory(trait_id, pinned=pinned)
            # Also update SQLite by matching text
            with sm.transaction() as conn:
                conn.execute(
                    "UPDATE memories SET pinned = ?, updated_at = ? WHERE id = ? OR text = ?",
                    (1 if pinned else 0, time.time(), trait_id, trait_id),
                )
            target_text = trait_id
            for t in self.profile.get("traits", []):
                if t == trait_id or f"trait_{abs(hash(t)) % 1000000}" == trait_id:
                    target_text = t
                    break
            if pinned:
                if target_text not in self.profile["pinned_traits"]:
                    self.profile["pinned_traits"].append(target_text)
            else:
                if target_text in self.profile["pinned_traits"]:
                    self.profile["pinned_traits"].remove(target_text)
            self._save_profile()
            return True

    def edit_profile_trait(self, trait_id: str, new_text: str):
        clean_text = sanitize_anti_cjk((new_text or "").strip())
        if not clean_text:
            return False
        with self._lock:
            sm = get_storage_manager()
            with sm.transaction() as conn:
                conn.execute(
                    "UPDATE memories SET text = ?, updated_at = ? WHERE id = ? OR text = ?",
                    (clean_text, time.time(), trait_id, trait_id),
                )
            old_text = trait_id
            traits = self.profile.get("traits", [])
            for idx, t in enumerate(traits):
                if t == trait_id or f"trait_{abs(hash(t)) % 1000000}" == trait_id:
                    old_text = t
                    traits[idx] = clean_text
                    break
            if "pinned_traits" in self.profile and old_text in self.profile["pinned_traits"]:
                self.profile["pinned_traits"].remove(old_text)
                self.profile["pinned_traits"].append(clean_text)
            self._save_profile()
            return True

    def delete_profile_trait(self, trait_id: str):
        with self._lock:
            sm = get_storage_manager()
            sm.delete_memory(trait_id)
            with sm.transaction() as conn:
                conn.execute(
                    "DELETE FROM memories WHERE id = ? OR text = ?",
                    (trait_id, trait_id),
                )
            old_text = trait_id
            traits = self.profile.get("traits", [])
            new_traits = []
            for t in traits:
                if t == trait_id or f"trait_{abs(hash(t)) % 1000000}" == trait_id:
                    old_text = t
                    continue
                new_traits.append(t)
            self.profile["traits"] = new_traits
            if "pinned_traits" in self.profile and old_text in self.profile.get("pinned_traits", []):
                self.profile["pinned_traits"].remove(old_text)
            if trait_id in self.profile.get("answers", {}):
                del self.profile["answers"][trait_id]
            self._save_profile()
            return True

    def reset_profile(self):
        with self._lock:
            self.profile["answers"] = {}
            self.profile["traits"] = []
            self.profile["last_ai_question"] = None
            self.profile["tutorial_completed"] = False
            self._save_profile()
            return self.get_onboarding_state()

    def reset_projects_and_archive(self):
        with self._lock:
            self.projects = []
            self.archive = []
            self.profile["active_project_id"] = ""
            self._save_projects()
            self._save_archive()
            self._save_profile()
            return self.get_projects_state()

    def add_quick_link(self, label: str, url: str):
        lbl = (label or "").strip()
        u = (url or "").strip()
        if not lbl or not u:
            return self.profile.get("quick_links", [])
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        with self._lock:
            links = self.profile.setdefault("quick_links", list(self.DEFAULT_QUICK_LINKS))
            links.append({"label": lbl, "url": u})
            self._save_profile()
            return list(links)

    def delete_quick_link(self, index: int):
        with self._lock:
            links = self.profile.setdefault("quick_links", list(self.DEFAULT_QUICK_LINKS))
            if 0 <= index < len(links):
                links.pop(index)
                self._save_profile()
            return list(links)

    # --- LIVELLO 2: SCHEDE PROGETTO ATTIVE & PIN SERVER (ZERO SCANSIONE) ---
    def get_stale_projects(self, inactive_days: int = 14):
        now_ts = time.time()
        threshold = inactive_days * 86400
        stale = []
        with self._lock:
            for p in self.projects:
                if p.get("status", "active") != "active":
                    continue
                last_ts = float(p.get("last_ts") or now_ts)
                days_old = int((now_ts - last_ts) / 86400)
                if (now_ts - last_ts) >= threshold:
                    item = dict(p)
                    item["days_inactive"] = max(inactive_days, days_old)
                    stale.append(item)
        return stale

    def get_projects_state(self):
        with self._lock:
            active_id = self.profile.get("active_project_id", "")
            if active_id and not any(p.get("id") == active_id for p in self.projects):
                active_id = self.projects[0]["id"] if self.projects else ""
                self.profile["active_project_id"] = active_id
                self._save_profile()
            elif not active_id and self.projects:
                active_id = self.projects[0]["id"]
                self.profile["active_project_id"] = active_id
                self._save_profile()

            active_proj = next((p for p in self.projects if p.get("id") == active_id), None)
            return {
                "projects": list(self.projects),
                "archive": list(self.archive),
                "active_project_id": active_id,
                "active_project": active_proj,
                "stale_projects": self.get_stale_projects(14),
                "computer_name": COMPUTER_NAME,
            }

    def create_or_update_project(
        self, name: str, client: str = "", note: str = "", pin_path: str = "", pin_label: str = ""
    ):
        clean_name = (name or "").strip()
        clean_client = (client or "").strip()
        if not clean_name:
            return {"ok": False, "error": "Specifica il nome del progetto."}

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        now_ts = time.time()

        with self._lock:
            existing = next((p for p in self.projects if p.get("name", "").lower() == clean_name.lower()), None)
            if existing:
                proj = existing
                if clean_client:
                    proj["client"] = clean_client
                proj["updated_at"] = now_str
                proj["last_ts"] = now_ts
            else:
                proj = {
                    "id": f"proj_{int(now_ts * 1000)}",
                    "name": clean_name,
                    "client": clean_client,
                    "status": "active",
                    "notes": [],
                    "pins": [],
                    "created_at": now_str,
                    "updated_at": now_str,
                    "last_ts": now_ts,
                }
                self.projects.insert(0, proj)

            if note and note.strip():
                n_clean = note.strip()
                if n_clean not in proj["notes"]:
                    proj["notes"].append(n_clean)

            if pin_path and pin_path.strip():
                p_clean = pin_path.strip().strip("\"'")
                lbl = (pin_label or "").strip() or os.path.basename(p_clean.rstrip("\\/")) or p_clean
                if not any(pin.get("path", "").lower() == p_clean.lower() for pin in proj["pins"]):
                    proj["pins"].append(
                        {
                            "id": f"pin_{int(time.time() * 1000)}",
                            "label": f"📂 {lbl}",
                            "path": p_clean,
                            "host": COMPUTER_NAME,
                        }
                    )

            self.profile["active_project_id"] = proj["id"]
            self._save_projects()
            self._save_profile()

            # Controlla se questo cliente (o progetto) esisteva già nell'Archivio Storico!
            search_q = f"{clean_name} {clean_client}".strip()
            archived_matches = self.find_archived_client_matches(search_q)

            return {
                "ok": True,
                "project": proj,
                "archived_matches": archived_matches,
                "state": self.get_projects_state(),
            }

    def set_active_project(self, project_id: str):
        with self._lock:
            for p in self.projects:
                if p.get("id") == project_id:
                    self.profile["active_project_id"] = project_id
                    p["last_ts"] = time.time()
                    p["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self._save_profile()
                    self._save_projects()
                    break
            return self.get_projects_state()

    def add_project_note(self, project_id: str, note_text: str):
        clean = (note_text or "").strip()
        if not clean:
            return self.get_projects_state()
        with self._lock:
            target = next((p for p in self.projects if p.get("id") == project_id), None)
            if not target and self.projects:
                target = self.projects[0]
            if target:
                if clean not in target.get("notes", []):
                    target.setdefault("notes", []).append(clean)
                target["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                target["last_ts"] = time.time()
                self._save_projects()
            return self.get_projects_state()

    def delete_project_note(self, project_id: str, note_index: int):
        with self._lock:
            target = next((p for p in self.projects if p.get("id") == project_id), None)
            if target and 0 <= note_index < len(target.get("notes", [])):
                target["notes"].pop(note_index)
                target["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                target["last_ts"] = time.time()
                self._save_projects()
            return self.get_projects_state()

    def add_project_pin(self, project_id: str, label: str, path: str):
        p_clean = (path or "").strip().strip("\"'")
        if not p_clean:
            return self.get_projects_state()
        lbl = (label or "").strip() or os.path.basename(p_clean.rstrip("\\/")) or p_clean
        if not lbl.startswith(("📂", "🎬", "🎨", "📌", "📄")):
            lbl = f"📂 {lbl}"
        with self._lock:
            target = next((p for p in self.projects if p.get("id") == project_id), None)
            if not target and self.projects:
                target = self.projects[0]
            if target:
                pins = target.setdefault("pins", [])
                if not any(x.get("path", "").lower() == p_clean.lower() for x in pins):
                    pins.append(
                        {
                            "id": f"pin_{int(time.time() * 1000)}",
                            "label": lbl,
                            "path": p_clean,
                            "host": COMPUTER_NAME,
                        }
                    )
                target["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                target["last_ts"] = time.time()
                self._save_projects()
            return self.get_projects_state()

    def delete_project_pin(self, project_id: str, pin_id: str):
        with self._lock:
            target = next((p for p in self.projects if p.get("id") == project_id), None)
            if target:
                target["pins"] = [x for x in target.get("pins", []) if x.get("id") != pin_id]
                target["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                self._save_projects()
            return self.get_projects_state()

    # --- LIVELLO 3: ARCHIVIO STORICO (MEMORIA FREDDA) & RISVEGLIO CLIENTE ---
    def archive_project(self, project_id: str):
        with self._lock:
            idx = next((i for i, p in enumerate(self.projects) if p.get("id") == project_id), -1)
            if idx == -1:
                return {"ok": False, "state": self.get_projects_state()}
            proj = self.projects.pop(idx)
            proj["status"] = "archived"
            proj["archived_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            # Evita duplicati in archivio con lo stesso id
            self.archive = [a for a in self.archive if a.get("id") != proj["id"]]
            self.archive.insert(0, proj)
            if self.profile.get("active_project_id") == project_id:
                self.profile["active_project_id"] = self.projects[0]["id"] if self.projects else ""
                self._save_profile()
            self._save_projects()
            self._save_archive()
            return {"ok": True, "archived_project": proj, "state": self.get_projects_state()}

    def restore_from_archive(
        self, archive_id: str, mode: str = "reactivate_project", target_project_id: str = None
    ):
        with self._lock:
            arch_item = next((a for a in self.archive if a.get("id") == archive_id), None)
            if not arch_item:
                return {"ok": False, "state": self.get_projects_state()}

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            if mode == "copy_client_notes":
                # Copia le preferenze storiche del cliente nel progetto attivo corrente senza togliere il vecchio progetto dall'archivio!
                tid = target_project_id or self.profile.get("active_project_id")
                target = next((p for p in self.projects if p.get("id") == tid), None)
                if not target and self.projects:
                    target = self.projects[0]
                if target:
                    for n in arch_item.get("notes", []):
                        if n not in target.setdefault("notes", []):
                            target["notes"].append(n)
                    target["updated_at"] = now_str
                    target["last_ts"] = time.time()
                    self._save_projects()
                    return {
                        "ok": True,
                        "restored_to": target["name"],
                        "notes_copied": arch_item.get("notes", []),
                        "state": self.get_projects_state(),
                    }

            # Altrimenti riporta l'intero progetto tra quelli Attivi
            self.archive = [a for a in self.archive if a.get("id") != archive_id]
            restored = dict(arch_item)
            restored["status"] = "active"
            restored["updated_at"] = now_str
            restored["last_ts"] = time.time()
            self.projects.insert(0, restored)
            self.profile["active_project_id"] = restored["id"]
            self._save_archive()
            self._save_projects()
            self._save_profile()
            return {"ok": True, "project": restored, "state": self.get_projects_state()}

    def delete_archived_project(self, archive_id: str):
        with self._lock:
            self.archive = [a for a in self.archive if a.get("id") != archive_id]
            self._save_archive()
            return self.get_projects_state()

    def find_archived_client_matches(self, text: str):
        """
        Controlla se nel testo dell'utente (o nel nuovo progetto) viene nominato un cliente
        o un progetto presente nell'Archivio Storico (`archive.json`).
        """
        low = (text or "").lower()
        if len(low) < 3:
            return []
        matches = []
        with self._lock:
            for arch in self.archive:
                client = (arch.get("client") or "").strip()
                pname = (arch.get("name") or "").strip()
                notes = arch.get("notes", [])
                if not notes:
                    continue
                matched = False
                # Cerca corrispondenza sul nome cliente (anche parziale, es. "Rossi" in "Sig. Rossi")
                if client:
                    client_tokens = [
                        t
                        for t in re.split(r"[\s\.,_-]+", client.lower())
                        if len(t) >= 3 and t not in ("sig", "signor", "dott", "arch", "ing", "studio", "srl", "spa")
                    ]
                    if client.lower() in low or any(
                        re.search(rf"\b{re.escape(tok)}\b", low) for tok in client_tokens
                    ):
                        matched = True
                if not matched and len(pname) >= 4 and pname.lower() in low:
                    matched = True

                if matched:
                    matches.append(
                        {
                            "archive_id": arch["id"],
                            "project_name": pname,
                            "client": client or pname,
                            "notes": list(notes),
                            "archived_at": arch.get("archived_at", ""),
                        }
                    )
        return matches

    def detect_memory_candidate(self, text: str):
        """
        Rileva passivamente se l'utente ha scritto in chat una preferenza, regola di un cliente
        o informazione utile (es. "il signor rossi vuole sempre tutto minimale", "preferisco...",
        "usa sempre...") per proporre il bottone 1-click `[🧠 + Salva]`.
        """
        clean = (text or "").strip()
        low = clean.lower()
        if len(clean) < 12 or clean.startswith("/"):
            return None
        if low.startswith(("ricorda che", "ricordati che", "memorizza", "nota:", "salva in memoria", "todo", "apri ", "fissa ")):
            return None

        triggers = (
            "vuole sempre",
            "vuole tutto",
            "non vuole",
            "preferisce",
            "preferisco",
            "usa sempre",
            "usiamo sempre",
            "formato ",
            "stile ",
            "il cliente ",
            "signor ",
            "sig. ",
            "scadenza ",
            "consegna ",
            "importante:",
            "regola:",
        )
        if not any(t in low for t in triggers):
            return None

        with self._lock:
            active_id = self.profile.get("active_project_id", "")
            active_proj = next((p for p in self.projects if p.get("id") == active_id), None)
            # Controlla se nel messaggio è nominato uno specifico progetto attivo
            for p in self.projects:
                pname = (p.get("name") or "").lower()
                pclient = (p.get("client") or "").lower()
                if (pname and pname in low) or (pclient and len(pclient) >= 3 and pclient in low):
                    active_proj = p
                    break

            return {
                "fact": clean,
                "project_id": active_proj["id"] if active_proj else "",
                "project_name": active_proj["name"] if active_proj else "",
            }

    @classmethod
    def capture_open_windows_paths(cls):
        """
        Interroga istantaneamente (< 400ms, ZERO scansione del disco/server!) le finestre aperte
        di Esplora File (inclusi percorsi di rete `\\\\Server\\...` o NAS) e i titoli delle app 3D/creative.
        """
        ps_script = (
            "$items = @(); "
            "try { "
            "  $shell = New-Object -ComObject Shell.Application; "
            "  foreach ($w in $shell.Windows()) { "
            "    try { "
            "      $p = $w.Document.Folder.Self.Path; "
            "      if ($p) { $items += @{ type='folder'; path=$p; title=(Split-Path $p -Leaf) } } "
            "    } catch {} "
            "  } "
            "} catch {} "
            "Get-Process | Where-Object { $_.MainWindowTitle -and $_.ProcessName -match '3dsmax|UnrealEditor|blender|Photoshop|AfterFX|Substance|Cinema4D' } | ForEach-Object { "
            "  $items += @{ type='app'; path=$_.MainWindowTitle; title=($_.ProcessName + ': ' + $_.MainWindowTitle) } "
            "}; "
            "$items | ConvertTo-Json -Compress"
        )
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_script],
                startupinfo=startupinfo,
                text=True,
                timeout=3.0,
            ).strip()
            if not out:
                return []
            raw = json.loads(out)
            if isinstance(raw, dict):
                raw = [raw]
            results = []
            seen = set()
            for r in raw:
                p = (r.get("path") or "").strip()
                if not p or p in seen:
                    continue
                seen.add(p)
                t = r.get("title") or os.path.basename(p.rstrip("\\/")) or p
                results.append(
                    {
                        "type": r.get("type", "folder"),
                        "path": p,
                        "label": f"📂 {t}" if r.get("type") == "folder" else f"🎬 {t}",
                    }
                )
            return results
        except Exception:
            return []


class WindowAndConnectionsManager:
    """
    Gestisce:
      1. La finestra dell'App Desktop Neko (`Dashboard` 1140x760 vs `Mini-Widget` 390x240 Always-on-Top)
         e il richiamo globale `Alt + Spazio` (`focus_input_seq`).
      2. Il download in background dei modelli Ollama (`ollama pull llama3.2`) dal pannello Connessioni.
      3. La sincronizzazione rapida Git Casa ↔ Lavoro con 1 click.
    """

    def __init__(self, ollama_ref, calendar_ref, mem_proj_ref):
        self.ollama = ollama_ref
        self.calendar = calendar_ref
        self.mem_proj = mem_proj_ref
        self.window_mode = "dashboard"
        self.focus_input_seq = 0
        self.ollama_pull_status = {"active": False, "model": "", "message": ""}
        self.pywebview_window = None

    def _find_app_hwnds(self):
        hwnds = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                title = buf.value or ""
                if "ASCII AI Companion" in title or "Neko Desktop" in title:
                    # Ignora finestre figlie di WorkerW / Progman (sfondo Lively Wallpaper)
                    parent = user32.GetParent(hwnd)
                    cls_buf = ctypes.create_unicode_buffer(128)
                    user32.GetClassNameW(hwnd, cls_buf, 128)
                    if cls_buf.value not in ("Progman", "WorkerW") and not parent:
                        hwnds.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        return hwnds

    def set_window_mode(self, mode: str):
        hwnds = self._find_app_hwnds()
        screen_w = user32.GetSystemMetrics(0) or 1920
        screen_h = user32.GetSystemMetrics(1) or 1080
        SWP_SHOWWINDOW = 0x0040
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

        if mode == "focus_input":
            self.focus_input_seq += 1
            if self.pywebview_window is not None:
                try:
                    self.pywebview_window.restore()
                    self.pywebview_window.show()
                except Exception:
                    pass
            for hwnd in hwnds:
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            return {"ok": True, "window_mode": self.window_mode, "focus_input_seq": self.focus_input_seq}

        if mode == "mini":
            self.window_mode = "mini"
            w, h = 480, 420
            x = max(20, screen_w - w - 28)
            y = max(20, screen_h - h - 72)
            if self.pywebview_window is not None:
                try:
                    self.pywebview_window.restore()
                    self.pywebview_window.resize(w, h)
                    self.pywebview_window.move(x, y)
                    self.pywebview_window.on_top = True
                except Exception:
                    pass
            for hwnd in hwnds:
                user32.ShowWindow(hwnd, 9)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_SHOWWINDOW)
            return {"ok": True, "window_mode": "mini", "hwnd_count": len(hwnds)}

        # Default: "dashboard"
        self.window_mode = "dashboard"
        w, h = min(1180, screen_w - 80), min(780, screen_h - 90)
        x = max(20, (screen_w - w) // 2)
        y = max(20, (screen_h - h) // 2)
        if self.pywebview_window is not None:
            try:
                self.pywebview_window.on_top = False
                self.pywebview_window.restore()
                self.pywebview_window.resize(w, h)
                self.pywebview_window.move(x, y)
            except Exception:
                pass
        for hwnd in hwnds:
            user32.ShowWindow(hwnd, 9)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, x, y, w, h, SWP_SHOWWINDOW)
            user32.SetForegroundWindow(hwnd)
        return {"ok": True, "window_mode": "dashboard", "hwnd_count": len(hwnds)}

    def start_ollama_pull(self, model_name: str = "llama3.2"):
        model = (model_name or "llama3.2").strip()
        if self.ollama_pull_status.get("active"):
            return self.ollama_pull_status

        self.ollama_pull_status = {
            "active": True,
            "model": model,
            "message": f"⏳ Download del modello '{model}' avviato in background...",
        }

        def _pull_worker():
            ollama_bin = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
            if not os.path.isfile(ollama_bin):
                ollama_bin = "ollama"
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                proc = subprocess.Popen(
                    [ollama_bin, "pull", model],
                    startupinfo=startupinfo,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                for line in proc.stdout:
                    clean_line = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line or "").strip()
                    if clean_line:
                        self.ollama_pull_status["message"] = f"⬇️ {model}: {clean_line[-70:]}"
                proc.wait()
                self.ollama._fetch_models()
                if model in self.ollama.models or any(m.startswith(model) for m in self.ollama.models):
                    self.ollama.active_model = next(
                        (m for m in self.ollama.models if m.startswith(model)), model
                    )
                    self.ollama_pull_status = {
                        "active": False,
                        "model": model,
                        "message": f"✅ Modello '{model}' installato e attivato!",
                    }
                else:
                    self.ollama_pull_status = {
                        "active": False,
                        "model": model,
                        "message": f"⚠️ Download di '{model}' terminato (verifica che Ollama sia attivo).",
                    }
            except Exception as e:
                self.ollama_pull_status = {
                    "active": False,
                    "model": model,
                    "message": f"⚠️ Errore durante il download: {e}",
                }

        threading.Thread(target=_pull_worker, daemon=True).start()
        return self.ollama_pull_status

    def get_storage_state(self):
        is_custom = os.path.normcase(os.path.abspath(DATA_DIR)) != os.path.normcase(os.path.abspath(DEFAULT_DATA_DIR))
        files_present = [f for f in DATA_FILENAMES if os.path.isfile(os.path.join(DATA_DIR, f))]
        return {
            "data_dir": DATA_DIR,
            "default_dir": DEFAULT_DATA_DIR,
            "is_custom": is_custom,
            "files_count": len(files_present),
            "computer": COMPUTER_NAME,
        }

    def set_storage_dir(self, new_dir: str):
        raw = (new_dir or "").strip().strip("\"'")
        prev_dir = DATA_DIR
        if not raw or raw.lower() == "default":
            target_dir = DEFAULT_DATA_DIR
            save_cfg_val = ""
        else:
            target_dir = os.path.abspath(os.path.expanduser(raw))
            save_cfg_val = target_dir

        try:
            os.makedirs(target_dir, exist_ok=True)
            _apply_data_dir(target_dir, migrate_from=prev_dir)
            _save_json(STORAGE_CONFIG_FILE, {"data_dir": save_cfg_val})

            # Ricarica in tempo reale tutti i file dalla nuova cartella (es. Google Drive / OneDrive)
            self.mem_proj.reload_from_disk()
            with self.ollama._lock:
                self.ollama.memories = _load_json(MEMORY_FILE, [])
                self.ollama.chat_history = _load_json(CHAT_HISTORY_FILE, [])
            with self.calendar._lock:
                self.calendar.local_events = _load_json(EVENTS_FILE, [])
                self.calendar.config = _load_json(CALENDAR_CONFIG_FILE, self.calendar.config)

            return {
                "ok": True,
                "message": f"✅ Cartella Memoria impostata su: {DATA_DIR}",
                "storage": self.get_storage_state(),
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"⚠️ Impossibile impostare la cartella '{target_dir}': {e}",
                "storage": self.get_storage_state(),
            }

    def pick_storage_dir_dialog(self):
        """Apre il selettore cartella nativo di Windows per scegliere una cartella (es. Google Drive / OneDrive)."""
        try:
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.Description = 'Scegli la cartella dove salvare le Memorie di Neko (es. Google Drive o OneDrive)'; "
                f"$f.SelectedPath = '{DATA_DIR.replace(chr(39), chr(39)*2)}'; "
                "$f.ShowNewFolderButton = $true; "
                "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath }"
            )
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Sta", "-Command", ps_script],
                startupinfo=startupinfo,
                text=True,
                timeout=120.0,
            ).strip()
            if out:
                return self.set_storage_dir(out)
            return {
                "ok": False,
                "cancelled": True,
                "message": "Selezione cartella annullata.",
                "storage": self.get_storage_state(),
            }
        except Exception as e:
            return {
                "ok": False,
                "message": f"⚠️ Impossibile aprire il selettore cartella: {e}",
                "storage": self.get_storage_state(),
            }

    def get_connections_state(self):
        self.ollama._fetch_models()
        cal_state = self.calendar.get_summary_state()
        return {
            "calendar": cal_state,
            "ollama": {
                "online": self.ollama.ollama_online,
                "active_model": self.ollama.active_model,
                "models": list(self.ollama.models),
                "pull_status": self.ollama_pull_status,
            },
            "storage": self.get_storage_state(),
            "quick_links": self.mem_proj.profile.get("quick_links", []),
            "window_mode": self.window_mode,
        }


class OllamaLocalBridge:
    """
    Gestisce il cervello locale di Neko:
    - Conversazione con Ollama + Cronologia Chat persistente (chat_history.json)
    - Memoria a lungo termine (memory.json)
    - Calendario (Google Calendar + Eventi locali)
    - Azioni rapide sul PC (App, File, Appunti, To-Do, Briefing)
    """

    def __init__(self, calendar_mgr: CalendarManager, mem_proj_mgr: MemoryAndProjectsManager):
        self.calendar = calendar_mgr
        self.mem_proj = mem_proj_mgr
        self.models = []
        self.active_model = ""
        self.is_generating = False
        self.ollama_online = False
        self._lock = threading.RLock()
        self.chat_history = _load_json(CHAT_HISTORY_FILE, [])
        self.memories = _load_json(MEMORY_FILE, [])
        self._ensure_thread = threading.Thread(target=self._init_ollama, daemon=True)
        self._ensure_thread.start()

    def _init_ollama(self):
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
                raw_models = data.get("models", [])
                chat_models = []
                other_models = []
                for m in raw_models:
                    name = m.get("name")
                    if not name:
                        continue
                    caps = m.get("capabilities") or []
                    if "completion" in caps or (
                        not caps
                        and not any(x in name.lower() for x in ("embed", "nomic", "bge", "clef"))
                    ):
                        chat_models.append(name)
                    else:
                        other_models.append(name)
                # Ordina i modelli chat dando priorità a qwen2.5:7b (veloce, leggero in VRAM e ottimo in italiano)
                chat_models.sort(
                    key=lambda n: (
                        0 if n.lower().startswith("qwen2.5") else (1 if "llama3" in n.lower() else 2),
                        n.lower(),
                    )
                )
                models = chat_models + other_models
                self.models = models
                self.ollama_online = True
                pref = self.mem_proj.profile.get("preferred_model", "")
                if pref and pref in chat_models:
                    self.active_model = pref
                elif chat_models and (not self.active_model or self.active_model not in chat_models or (chat_models[0].lower().startswith("qwen2.5") and not self.active_model.lower().startswith("qwen2.5"))):
                    self.active_model = chat_models[0]
                elif models and not self.active_model:
                    self.active_model = models[0]
                return True
        except Exception:
            self.ollama_online = False
            return False

    # --- Gestione Cronologia Chat & Memoria ---
    def get_chat_history(self, limit=60):
        with self._lock:
            return list(self.chat_history[-limit:])

    def clear_chat_history(self):
        with self._lock:
            self.chat_history = []
            _save_json(CHAT_HISTORY_FILE, self.chat_history)
            try:
                get_storage_manager().clear_chat_history()
            except Exception:
                pass

    def _append_chat_turn(self, user_text, reply_text, model_used, actions=None):
        now_str = datetime.now().strftime("%H:%M")
        ts = time.time()
        with self._lock:
            # Sync to SQLite
            try:
                get_storage_manager().append_chat_turn(user_text, reply_text, model=model_used, actions=actions)
            except Exception as e:
                logger.warning(f"StorageManager chat turn sync error: {e}")

            self.chat_history.append(
                {
                    "id": f"u_{int(ts * 1000)}",
                    "role": "user",
                    "text": user_text,
                    "time": now_str,
                    "timestamp": ts,
                }
            )
            self.chat_history.append(
                {
                    "id": f"a_{int(ts * 1000) + 1}",
                    "role": "assistant",
                    "text": reply_text,
                    "time": now_str,
                    "timestamp": ts,
                    "model": model_used,
                    "actions": actions or [],
                }
            )
            if len(self.chat_history) > 200:
                self.chat_history = self.chat_history[-200:]
            _save_json(CHAT_HISTORY_FILE, self.chat_history)

    def get_memories(self):
        with self._lock:
            return list(self.memories)

    def add_memory(self, fact_text: str):
        clean = (fact_text or "").strip()
        if not clean:
            return None
        with self._lock:
            # Dual-write & deduplicate with SQLite StorageManager
            sm = get_storage_manager()
            try:
                mid = sm.upsert_memory(clean, category="Generale", source="user")
            except Exception as e:
                logger.warning(f"StorageManager add_memory error: {e}")
                mid = f"mem_{int(time.time() * 1000)}"

            for m in self.memories:
                if m.get("text", "").lower() == clean.lower() or m.get("id") == mid:
                    return m

            entry = {
                "id": mid,
                "text": clean,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            self.memories.append(entry)
            _save_json(MEMORY_FILE, self.memories)
            return entry

    def delete_memory(self, index_or_query):
        with self._lock:
            if not self.memories:
                return False
            removed_id = None
            if str(index_or_query).isdigit():
                idx = int(index_or_query) - 1
                if 0 <= idx < len(self.memories):
                    removed = self.memories.pop(idx)
                    removed_id = removed.get("id")
                    _save_json(MEMORY_FILE, self.memories)
                    if removed_id:
                        try:
                            get_storage_manager().delete_memory(removed_id)
                        except Exception:
                            pass
                    return True
            q = str(index_or_query).lower().strip()
            before = len(self.memories)
            to_remove = [m for m in self.memories if q in m.get("text", "").lower()]
            self.memories = [m for m in self.memories if q not in m.get("text", "").lower()]
            if len(self.memories) < before:
                _save_json(MEMORY_FILE, self.memories)
                sm = get_storage_manager()
                for m in to_remove:
                    if m.get("id"):
                        try:
                            sm.delete_memory(m["id"])
                        except Exception:
                            pass
                return True
        return False

    def clear_memories(self):
        with self._lock:
            self.memories = []
            _save_json(MEMORY_FILE, self.memories)

    # --- To-Do List ---
    def load_todos(self):
        return _load_json(TODO_FILE, [])

    def save_todos(self, items):
        _save_json(TODO_FILE, items)

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
                timeout=3.0,
            ).strip()
            procs = json.loads(out)
            if isinstance(procs, dict):
                procs = [procs]
            summary = " · ".join([f"{p['Name']} ({p['MB']} MB)" for p in procs])
            return f"📊 Top Processi RAM: {summary}"
        except Exception:
            return "📊 Impossibile leggere la lista dettagliata dei processi in questo istante."

    def _parse_natural_date_time(self, text: str):
        """Estrae data e ora da frasi come 'stasera alle 18', 'domani alle 15:30', '2026-10-28 14:00'."""
        now = datetime.now()
        low = text.lower()
        target_date = now.date()

        m_iso = re.search(r"(\d{4}-\d{2}-\d{2})", low)
        m_it = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", low)
        if m_iso:
            try:
                target_date = datetime.strptime(m_iso.group(1), "%Y-%m-%d").date()
            except Exception:
                pass
        elif m_it:
            try:
                d = int(m_it.group(1))
                m = int(m_it.group(2))
                y = int(m_it.group(3)) if m_it.group(3) else now.year
                if y < 100:
                    y += 2000
                target_date = date(y, m, d)
            except Exception:
                pass
        elif "dopodomani" in low:
            target_date = now.date() + timedelta(days=2)
        elif "domani" in low:
            target_date = now.date() + timedelta(days=1)
        else:
            weekdays_it = {
                "lunedì": 0, "lunedi": 0,
                "martedì": 1, "martedi": 1,
                "mercoledì": 2, "mercoledi": 2,
                "giovedì": 3, "giovedi": 3,
                "venerdì": 4, "venerdi": 4,
                "sabato": 5,
                "domenica": 6,
            }
            for w_name, w_idx in weekdays_it.items():
                if re.search(rf"\b{w_name}\b", low):
                    days_ahead = (w_idx - now.weekday()) % 7
                    if days_ahead == 0:
                        days_ahead = 7
                    target_date = now.date() + timedelta(days=days_ahead)
                    break

        hour, minute = 9, 0
        if "stasera" in low:
            hour = 20
        elif "pomeriggio" in low:
            hour = 15

        m_time = re.search(r"(?:alle|ore|\b)(\d{1,2})[:\.](\d{2})\b", low)
        m_hour_only = re.search(r"\b(?:alle|ore)\s+(\d{1,2})\b", low)
        if m_time:
            hour = min(23, max(0, int(m_time.group(1))))
            minute = min(59, max(0, int(m_time.group(2))))
        elif m_hour_only:
            hour = min(23, max(0, int(m_hour_only.group(1))))
            minute = 0

        # Pulisci il titolo rimuovendo parole temporali e formule di cortesia ("puoi ricordarmi che stasera alle 18 devo...")
        clean_title = re.sub(
            r"^(?:puoi\s+)?(?:ricordarmi|ricordami|mi\s+ricordi|metti\s+sul\s+calendario|metti\s+su\s+google\s+calend[ae]r|aggiungi\s+su\s+google\s+calend[ae]r|aggiungi\s+sul\s+calendario|segna\s+in\s+agenda|segna\s+sul\s+calendario|fissa\s+in\s+agenda|fissa)\s*(?:che|di|:)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        clean_title = re.sub(
            r"\b(oggi|stasera|domani|dopodomani|luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica|alle\s+\d{1,2}([:\.]\d{2})?|ore\s+\d{1,2}([:\.]\d{2})?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(/\d{2,4})?|su\s+google\s+calend[ae]r|sul\s+google\s+calend[ae]r|sul\s+calendario|in\s+agenda)\b",
            "",
            clean_title,
            flags=re.IGNORECASE,
        )
        clean_title = re.sub(r"^\s*(?:che\s+)?(?:devo\s+|bisogna\s+)?", "", clean_title, flags=re.IGNORECASE)
        clean_title = re.sub(r"\s+", " ", clean_title).strip(" -:,?!")
        if not clean_title:
            clean_title = "Impegno a calendario"
        else:
            clean_title = clean_title[0].upper() + clean_title[1:]

        return datetime(target_date.year, target_date.month, target_date.day, hour, minute), clean_title

    def build_daily_briefing(self, cpu=0, ram=0):
        now = datetime.now()
        cal_state = self.calendar.get_summary_state()
        upcoming = cal_state.get("upcoming_events", [])
        today_evs = [e for e in upcoming if e.get("is_today")]
        todos = self.load_todos()
        mems = self.get_memories()
        proj_state = self.mem_proj.get_projects_state()
        active_projs = proj_state.get("projects", [])
        stale_projs = proj_state.get("stale_projects", [])

        parts = [f"☀️ **Briefing di Neko ({now.strftime('%d/%m/%Y %H:%M')})**"]

        if today_evs:
            ev_strs = [f"• {e['time_label']} — {e['title']}" for e in today_evs[:5]]
            parts.append("📅 **Impegni di Oggi:** " + " | ".join(ev_strs))
        elif upcoming:
            nxt = upcoming[0]
            parts.append(f"📅 **Oggi nessun impegno.** Prossimo: {nxt['day_label']} {nxt['time_label']} — {nxt['title']}")
        elif cal_state.get("configured"):
            parts.append("📅 **Agenda (Google Calendar connesso):** Nessun impegno in programma nei prossimi 7 giorni.")
        else:
            parts.append("📅 **Agenda:** Nessun evento in programma (apri `🔌 CONNESSIONI` per collegare Google Calendar).")

        if active_projs:
            ap = proj_state.get("active_project") or active_projs[0]
            notes_preview = f" ({ap['notes'][-1]})" if ap.get("notes") else ""
            parts.append(
                f"📌 **Progetti Attivi ({len(active_projs)}):** focus su **{ap['name']}**"
                + (f" [{ap['client']}]" if ap.get("client") else "")
                + notes_preview
            )
        if stale_projs:
            stale_names = ", ".join([f"'{p['name']}' ({p['days_inactive']}gg)" for p in stale_projs[:3]])
            parts.append(f"📦 **Check-up Progetti:** {stale_names} non vengono toccati da oltre 2 settimane. Vuoi spostarli in Archivio Storico?")

        if todos:
            td_strs = [f"[{i+1}] {t}" for i, t in enumerate(todos[:5])]
            parts.append("📝 **To-Do Attivi:** " + " · ".join(td_strs))
        else:
            parts.append("📝 **To-Do:** Tutti completati!")

        if mems:
            parts.append(f"🧠 **Memoria Attiva:** {len(mems)} note salvate (ultima: \"{mems[-1]['text']}\")")

        parts.append(f"💻 **Sistema ({COMPUTER_NAME}):** CPU {cpu}% · RAM {ram}% · Modello: {self.active_model or 'Ollama'}")
        return "\n".join(parts)

    def _execute_llm_action_tags(self, raw_reply: str, user_prompt: str):
        """
        Intercetta ed esegue davvero i tag d'azione emessi da Ollama (qwen2.5:7b):
          - [[ACTION:CALENDAR | YYYY-MM-DD HH:MM | Titolo impegno]]
          - [[ACTION:TODO | Testo task]]
          - [[ACTION:MEMORY | Fatto da ricordare]]
        """
        extra_actions = []
        appended_notes = []
        tag_pattern = re.compile(r"\[\[\s*ACTION\s*:\s*([A-Z_]+)\s*\|\s*(.*?)\s*\]\]", re.IGNORECASE | re.DOTALL)

        for match in tag_pattern.finditer(raw_reply or ""):
            act_type = match.group(1).strip().upper()
            payload_str = match.group(2).strip()

            if act_type in ("CALENDAR", "EVENT", "AGENDA"):
                parts = [p.strip() for p in payload_str.split("|", 1)]
                if len(parts) == 2:
                    dt_raw, title_raw = parts[0], parts[1]
                    try:
                        dt_start = datetime.strptime(dt_raw[:16], "%Y-%m-%d %H:%M")
                    except Exception:
                        dt_start, _ = self._parse_natural_date_time(dt_raw + " " + title_raw)
                    ev_title = title_raw or "Impegno"
                else:
                    dt_start, ev_title = self._parse_natural_date_time(payload_str)

                ev = self.calendar.add_event(ev_title, dt_start)
                appended_notes.append(
                    f"📅 *Salvato in Agenda per il **{dt_start.strftime('%d/%m/%Y alle %H:%M')}**: **{ev['title']}***"
                )
                extra_actions.append(
                    {
                        "label": "📅 + Salva su Google Calendar Web",
                        "url": ev["gcal_url"],
                    }
                )
                if any(k in (user_prompt or "").lower() for k in ("google calendar", "google calender")):
                    try:
                        WindowsPCActions.open_target(ev["gcal_url"])
                    except Exception:
                        pass

            elif act_type == "TODO":
                task_txt = payload_str.replace("|", " ").strip()
                if task_txt:
                    todos = self.load_todos()
                    if task_txt.lower() not in [t.lower() for t in todos]:
                        todos.append(task_txt)
                        self.save_todos(todos)
                    appended_notes.append(f"✅ *Aggiunto ai To-Do: **{task_txt}***")
                    # Se il messaggio dell'utente conteneva un orario preciso (es. "stasera alle 18"), segnalo anche in Agenda!
                    if re.search(r"\b(alle\s+\d{1,2}|ore\s+\d{1,2})\b", (user_prompt or "").lower()):
                        dt_start, _ = self._parse_natural_date_time(user_prompt)
                        ev = self.calendar.add_event(task_txt, dt_start)
                        appended_notes.append(
                            f"📅 *Fissato anche in Agenda per il **{dt_start.strftime('%d/%m/%Y alle %H:%M')}***"
                        )
                        extra_actions.append(
                            {
                                "label": "📅 + Salva su Google Calendar Web",
                                "url": ev["gcal_url"],
                            }
                        )

            elif act_type == "MEMORY":
                mem_txt = payload_str.replace("|", " ").strip()
                if mem_txt:
                    self.add_memory(mem_txt)
                    appended_notes.append(f"🧠 *Salvato in Memoria: **{mem_txt}***")

        clean_reply = tag_pattern.sub("", raw_reply or "").strip()
        if appended_notes:
            clean_reply = (clean_reply + "\n\n" + "\n".join(appended_notes)).strip()
        return clean_reply, extra_actions

    def _finish_reply(self, prompt_text, reply_text, model_name="LOCAL-TOOL", actions=None, skip_smart_hooks=False):
        final_actions = list(actions or [])
        final_reply = reply_text

        if not skip_smart_hooks and prompt_text:
            # 1. Riconoscimento Ritorno Cliente dall'Archivio Storico (`archive.json`)
            arch_matches = self.mem_proj.find_archived_client_matches(prompt_text)
            for m in arch_matches[:2]:
                notes_str = "; ".join(m["notes"][:2])
                if f"Archivio Storico" not in final_reply:
                    final_reply += (
                        f"\n\n🐾 **Risveglio dall'Archivio Storico:** ho trovato note passate per **{m['client']}** "
                        f"*(Progetto \"{m['project_name']}\": \"{notes_str}\")*. Vuoi riattivarle?"
                    )
                final_actions.append(
                    {
                        "label": f"🔄 Riattiva preferenze di \"{m['client']}\" dall'Archivio",
                        "restore_archive_id": m["archive_id"],
                    }
                )

            # 2. Apprendimento Passivo dalle Chat ("🧠 Salvo questo?")
            cand = self.mem_proj.detect_memory_candidate(prompt_text)
            if cand:
                short_f = cand["fact"][:36] + ("…" if len(cand["fact"]) > 36 else "")
                if cand.get("project_id"):
                    final_actions.append(
                        {
                            "label": f"🧠 + Salva su \"{cand['project_name']}\": \"{short_f}\"",
                            "save_project_note": {
                                "project_id": cand["project_id"],
                                "note": cand["fact"],
                            },
                        }
                    )
                final_actions.append(
                    {
                        "label": f"🧠 + Salva in Memoria: \"{short_f}\"",
                        "save_memory": cand["fact"],
                    }
                )

        self._append_chat_turn(prompt_text, final_reply, model_name, actions=final_actions)
        return {
            "reply": final_reply,
            "model": model_name,
            "actions": final_actions,
            "history": self.get_chat_history(limit=40),
        }

    def handle_chat_or_command(self, prompt_text, requested_model=None, cpu=0, ram=0):
        text = (prompt_text or "").strip()
        if not text:
            return {"reply": "Miao? Scrivi pure un comando o una domanda!", "model": self.active_model}

        low = text.lower()

        # 0. Chiudi tema desktop
        if low in ("/stop", "/exit", "esci", "stacca tema", "chiudi tema"):
            def _close_theme_window():
                time.sleep(0.4)
                WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

                def _cb(hwnd, lparam):
                    if user32.IsWindowVisible(hwnd):
                        buf = ctypes.create_unicode_buffer(256)
                        user32.GetWindowTextW(hwnd, buf, 256)
                        if "ASCII AI Companion // Neko Desktop" in buf.value:
                            user32.PostMessageW(hwnd, 0x0010, 0, 0)
                    return True

                user32.EnumWindows(WNDENUMPROC(_cb), 0)

            threading.Thread(target=_close_theme_window, daemon=True).start()
            return self._finish_reply(text, "👋 Tema Desktop staccato! A presto, miao!")

        # 1. Briefing del Mattino / Riepilogo Giornata
        if low in ("/briefing", "briefing", "riepilogo", "buongiorno", "cosa devo fare oggi", "punto della situazione"):
            rep = self.build_daily_briefing(cpu=cpu, ram=ram)
            return self._finish_reply(text, rep, "NEKO-BRIEFING")

        # 2. Top CPU/RAM
        if low in ("/sys", "/cpu", "top cpu", "top ram", "status"):
            top_info = self.get_top_processes()
            return self._finish_reply(text, f"[SISTEMA: CPU {cpu}% | RAM {ram}%] {top_info}")

        # 3. Gestione To-Do List (/todo, todo: ..., todo done N)
        if low.startswith("/todo") or low.startswith("todo"):
            arg = text[5:].strip() if low.startswith("/todo") else text[4:].strip()
            if arg.startswith(":"):
                arg = arg[1:].strip()
            todos = self.load_todos()
            if not arg:
                if not todos:
                    return self._finish_reply(
                        text, "📝 La tua lista To-Do è vuota! Scrivi `todo: <cosa da fare>` (o usa il pulsante `✅ To-Do` in alto) per aggiungere."
                    )
                formatted = " | ".join([f"[{i+1}] {t}" for i, t in enumerate(todos)])
                todo_actions = [
                    {"label": f"✅ Completa #{i+1}: {t[:22]}", "complete_todo_idx": i}
                    for i, t in enumerate(todos[:4])
                ]
                return self._finish_reply(
                    text,
                    f"📝 **To-Do attivi ({len(todos)}):** {formatted}",
                    "TODO",
                    actions=todo_actions,
                )
            elif arg.lower() in ("clear", "pulisci", "reset"):
                self.save_todos([])
                return self._finish_reply(text, "🧹 Lista To-Do svuotata con successo!")
            elif arg.lower().startswith(("done ", "fatto ", "del ", "rm ")):
                idx_str = arg.split(None, 1)[1].strip()
                if idx_str.isdigit() and 1 <= int(idx_str) <= len(todos):
                    removed = todos.pop(int(idx_str) - 1)
                    self.save_todos(todos)
                    return self._finish_reply(text, f"✅ Completato e rimosso To-Do: \"{removed}\"")
                return self._finish_reply(text, "⚠️ Specifica un numero valido (es. `todo done 1`).")
            else:
                todos.append(arg)
                self.save_todos(todos)
                return self._finish_reply(text, f"✅ Aggiunto alla To-Do list (#{len(todos)}): \"{arg}\"")

        # 4. Gestione Memoria a Lungo Termine (/mem, "ricorda che...", "ricordati che...", "nota: ...")
        # Se però contiene un orario ("alle 18", "stasera alle", "domani alle"), è un promemoria a calendario e non una memoria statica!
        has_time_ref = bool(
            re.search(r"\b(alle\s+\d{1,2}|ore\s+\d{1,2}|stasera|domani|dopodomani)\b", low)
        )
        if not has_time_ref and low.startswith(
            ("/mem", "ricorda che ", "ricordati che ", "memorizza ", "nota: ", "salva in memoria")
        ):
            if low in ("/mem", "/mem list", "memoria"):
                mems = self.get_memories()
                if not mems:
                    return self._finish_reply(
                        text,
                        "🧠 Non ho ancora note in memoria! Scrivimi ad esempio: `Ricorda che il cliente Rossi preferisce i file in FBX`.",
                    )
                formatted = " | ".join([f"[{i+1}] {m['text']}" for i, m in enumerate(mems)])
                return self._finish_reply(
                    text, f"🧠 Memoria di Neko ({len(mems)} note): {formatted} (usa `/mem del 1` per rimuovere)"
                )
            if low in ("/mem clear", "/mem reset"):
                self.clear_memories()
                return self._finish_reply(text, "🧹 Memoria di lungo termine svuotata!")
            if low.startswith(("/mem del ", "/mem rm ")):
                target = text.split(None, 2)[2].strip()
                if self.delete_memory(target):
                    return self._finish_reply(text, f"🗑️ Nota rimossa dalla memoria ({target}).")
                return self._finish_reply(text, "⚠️ Nota non trovata in memoria.")

            # Estrai il fatto da salvare
            fact = text
            for prefix in ("/mem add ", "/mem ", "ricorda che ", "ricordati che ", "memorizza ", "nota: ", "salva in memoria: ", "salva in memoria "):
                if low.startswith(prefix):
                    fact = text[len(prefix) :].strip()
                    break
            entry = self.add_memory(fact)
            if entry:
                return self._finish_reply(
                    text, f"🧠 Miau! Me lo sono segnato in memoria: **\"{entry['text']}\"** (Totale note: {len(self.get_memories())})"
                )

        # 5. Gestione Calendario & Google Calendar (/cal, "che impegni ho...", "fissa/aggiungi impegno...", "ricordami che stasera alle 18...")
        is_explicit_cal_add = (
            low.startswith(("/cal add ", "cal add ", "aggiungi impegno", "aggiungi evento", "fissa appuntamento", "fissa call"))
            or any(
                p in low
                for p in (
                    "metti sul calendario",
                    "metti in calendario",
                    "metti su google calendar",
                    "metti su google calender",
                    "aggiungi su google calendar",
                    "aggiungi su google calender",
                    "aggiungi sul calendario",
                    "segna sul calendario",
                    "segna in agenda",
                )
            )
            or (
                has_time_ref
                and any(
                    p in low
                    for p in ("ricordarmi", "ricordami", "ricordati", "mi ricordi", "avvisami", "promemoria")
                )
            )
        )
        if (
            is_explicit_cal_add
            or low.startswith(("/cal", "cal:", "calendario", "agenda"))
            or any(
                p in low
                for p in (
                    "che impegni ho",
                    "cosa ho in calendario",
                    "prossimi appuntamenti",
                    "prossimi impegni",
                )
            )
        ):
            if low.startswith("/cal url ") or low.startswith("cal url "):
                url = text.split(None, 2)[2].strip() if len(text.split(None, 2)) > 2 else ""
                self.calendar.update_config(ical_url=url)
                cal_st = self.calendar.get_summary_state()
                return self._finish_reply(
                    text,
                    f"📅 Link Google Calendar iCal salvato! Ho caricato **{cal_st['upcoming_count']}** eventi in agenda.",
                )

            if low.startswith(("/cal del ", "cal del ")):
                target = text.split(None, 2)[2].strip()
                if self.calendar.delete_local_event(target):
                    return self._finish_reply(text, f"🗑️ Evento locale `{target}` rimosso dall'agenda.")
                return self._finish_reply(text, "⚠️ Evento locale non trovato.")

            # Creazione nuovo impegno
            if is_explicit_cal_add:
                raw_ev = text
                for pref in (
                    "/cal add ",
                    "cal add ",
                    "aggiungi impegno:",
                    "aggiungi impegno",
                    "aggiungi evento:",
                    "aggiungi evento",
                    "fissa appuntamento:",
                    "fissa appuntamento",
                    "fissa:",
                ):
                    if low.startswith(pref):
                        raw_ev = text[len(pref) :].strip()
                        break
                if raw_ev.lower().startswith("fissa "):
                    raw_ev = raw_ev[6:].strip()
                dt_start, ev_title = self._parse_natural_date_time(raw_ev)
                ev = self.calendar.add_event(ev_title, dt_start)
                wants_gcal = any(k in low for k in ("google calendar", "google calender"))
                if wants_gcal:
                    try:
                        WindowsPCActions.open_target(ev["gcal_url"])
                    except Exception:
                        pass
                g_note = (
                    " (sincronizzato anche su Google Calendar API!)"
                    if ev.get("created_on_google")
                    else (
                        " — ho anche aperto la scheda precompilata di **Google Calendar** per confermarlo!"
                        if wants_gcal
                        else ""
                    )
                )
                actions = [
                    {
                        "label": "📅 + Salva su Google Calendar Web",
                        "url": ev["gcal_url"],
                    }
                ]
                return self._finish_reply(
                    text,
                    f"📅 Impegno registrato in agenda per il **{dt_start.strftime('%d/%m/%Y alle %H:%M')}**: **\"{ev['title']}\"**{g_note}",
                    "CALENDAR",
                    actions=actions,
                )

            # Visualizzazione agenda
            self.calendar.refresh_remote_events()
            cal_st = self.calendar.get_summary_state()
            upcoming = cal_st.get("upcoming_events", [])
            if not upcoming:
                hint = (
                    " (Clicca su `📅 AGENDA` in alto per collegare il link `.ics` di Google Calendar o scrivi `/cal add domani alle 15:00 Call con Marco`!)"
                    if not cal_st.get("configured")
                    else " (Google Calendar `.ics` è connesso correttamente; scrivi ad es. *'Ricordami domani alle 15 call'* per aggiungere un impegno!)"
                )
                return self._finish_reply(text, f"📅 Nessun impegno nei prossimi 7 giorni!{hint}", "CALENDAR")

            lines = [f"• **{e['day_label']} {e['time_label']}**: {e['title']}" for e in upcoming[:8]]
            return self._finish_reply(
                text,
                f"📅 **Prossimi Impegni in Agenda ({len(upcoming)}):**\n" + "\n".join(lines),
                "CALENDAR",
            )

        # 6. Avvio Applicazioni e Cartelle sul PC ("apri ...", "/open ...")
        if low.startswith(("apri ", "avvia ", "lancia ", "/open ")):
            target = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
            ok, msg = WindowsPCActions.open_target(target)
            return self._finish_reply(text, msg, "PC-CONTROL")

        # 7. Ricerca File sul PC ("cerca file ...", "/find ...")
        if low.startswith(("cerca file ", "trova file ", "/find ")):
            query = re.sub(r"^(cerca file|trova file|/find)\s*:?\s*", "", text, flags=re.IGNORECASE).strip()
            results = WindowsPCActions.search_files(query)
            if not results:
                return self._finish_reply(text, f"🔍 Nessun file o cartella trovato con nome **\"{query}\"**.")
            lines = []
            actions = []
            for idx, r in enumerate(results, 1):
                icon = "📁" if r["is_dir"] else "📄"
                lines.append(f"[{idx}] {icon} **{r['name']}** (`{r['path']}`)")
                actions.append(
                    {
                        "label": f"📂 Apri {r['name'][:18]}",
                        "open_path": r["path"] if r["is_dir"] else r["parent"],
                    }
                )
            return self._finish_reply(
                text,
                f"🔍 Ho trovato {len(results)} risultati per **\"{query}\"**:\n" + "\n".join(lines),
                "PC-SEARCH",
                actions=actions[:4],
            )

        # 8. Lettura / Riassunto Appunti di Windows ("/clip", "riassumi gli appunti", "spiega gli appunti", "traduci gli appunti")
        clipboard_context = ""
        if low in ("/clip", "appunti", "leggi appunti") or "appunti" in low or "copiato" in low:
            clip_text = WindowsPCActions.read_clipboard()
            if not clip_text:
                return self._finish_reply(
                    text, "📋 Gli appunti di Windows sono vuoti (o non contengono testo)! Copia qualcosa con `Ctrl+C` e riprova."
                )
            if low in ("/clip", "leggi appunti"):
                preview = clip_text[:400] + ("..." if len(clip_text) > 400 else "")
                return self._finish_reply(text, f"📋 **Testo negli appunti:**\n{preview}", "CLIPBOARD")
            clipboard_context = f"\n\n[TESTO COPIATO NEGLI APPUNTI DI WINDOWS DALL'UTENTE]:\n{clip_text[:3000]}"

        # 8.5 Gestione rapida Progetti da chat ("/progetti", "progetto: ...", "nuovo progetto ...")
        if low in ("/progetti", "progetti", "lista progetti"):
            p_state = self.mem_proj.get_projects_state()
            projs = p_state.get("projects", [])
            arch = p_state.get("archive", [])
            if not projs:
                return self._finish_reply(
                    text,
                    f"📌 Nessun progetto attivo al momento ({len(arch)} in Archivio Storico). Clicca su `📌 PROGETTI` in alto o scrivi `progetto: Villa Rossi - Cliente: Sig. Rossi`!",
                    "PROJECTS",
                )
            lines = []
            actions = []
            for p in projs[:6]:
                c_str = f" [{p['client']}]" if p.get("client") else ""
                n_str = f" — Note: {'; '.join(p['notes'][:2])}" if p.get("notes") else ""
                lines.append(f"• **{p['name']}**{c_str}{n_str}")
                for pin in p.get("pins", [])[:2]:
                    actions.append({"label": f"{pin['label']} ({p['name']})", "open_path": pin["path"]})
            return self._finish_reply(
                text,
                f"📌 **Progetti Attivi ({len(projs)} attivi, {len(arch)} in archivio):**\n" + "\n".join(lines),
                "PROJECTS",
                actions=actions[:4],
            )

        if low.startswith(("progetto:", "nuovo progetto:", "nuovo progetto ", "crea progetto ")):
            raw_p = re.sub(r"^(progetto:|nuovo progetto:?|crea progetto:?)\s*", "", text, flags=re.IGNORECASE).strip()
            parts_p = [x.strip() for x in re.split(r"[-|]", raw_p, maxsplit=1)]
            p_name = parts_p[0] if parts_p else raw_p
            p_client = ""
            if len(parts_p) > 1:
                p_client = re.sub(r"^cliente:?\s*", "", parts_p[1], flags=re.IGNORECASE).strip()
            res = self.mem_proj.create_or_update_project(p_name, client=p_client)
            if res.get("ok"):
                proj = res["project"]
                return self._finish_reply(
                    text,
                    f"📌 Scheda Progetto **\"{proj['name']}\"**"
                    + (f" (Cliente: **{proj['client']}**)" if proj.get("client") else "")
                    + " attivata! Puoi fissare le cartelle del Server dal pannello `📌 PROGETTI`.",
                    "PROJECTS",
                )

        # 9. Richiesta al Modello LLM Locale (Ollama) con Cronologia Conversazionale + Action Tags Esecutivi
        if not self.ollama_online:
            self._fetch_models()

        model_to_use = requested_model or self.active_model
        if not model_to_use:
            actions = [{"label": "⬇️ Scarica Modello qwen2.5:7b (Consigliato)", "pull_ollama_model": "qwen2.5:7b"}]
            return self._finish_reply(
                text,
                "⚠️ Ollama è attivo ma non hai ancora scaricato un modello LLM locale! Clicca sul pulsante qui sotto (o apri `🔌 CONNESSIONI`) per scaricare **`qwen2.5:7b`** con 1 click.",
                "OFFLINE",
                actions=actions,
            )

        self.is_generating = True
        try:
            now_dt = datetime.now()
            sm = get_storage_manager()
            todos = self.load_todos()
            upcoming = self.calendar.get_upcoming_events(days_ahead=3)
            raw_traits = self.mem_proj.profile.get("traits", [])
            proj_state = self.mem_proj.get_projects_state()
            active_proj = proj_state.get("active_project")
            active_proj_id = active_proj.get("id") if active_proj else None
            active_projs = proj_state.get("projects", [])

            # 1. FTS5 BM25 + Time-Decay Retrieval
            retrieved_mems = sm.search_memories(query=text, project_id=active_proj_id, limit=12)

            # 2. Token Budget Manager
            budget_mgr = TokenBudgetManager()

            pinned_traits = []
            for t in raw_traits:
                if isinstance(t, str):
                    pinned_traits.append(t)
                elif isinstance(t, dict):
                    pinned_traits.append(t.get("text", ""))

            context_blocks = [
                f"Data e ora attuali: {now_dt.strftime('%Y-%m-%d %H:%M')} ({now_dt.strftime('%A %d/%m/%Y')}).",
                f"Postazione PC: {COMPUTER_NAME} (CPU {cpu}%, RAM {ram}%).",
            ]
            if pinned_traits:
                context_blocks.append("Profilo Utente & Regole: " + " | ".join(pinned_traits[:12]))
            if active_projs:
                proj_summaries = []
                for p in active_projs[:6]:
                    c_lbl = f" (Cliente: {p['client']})" if p.get("client") else ""
                    n_lbl = f" -> Regole/Note: {'; '.join(p['notes'])}" if p.get("notes") else ""
                    proj_summaries.append(f"{p['name']}{c_lbl}{n_lbl}")
                context_blocks.append("Progetti ATTIVI in corso (Memoria Calda): " + " || ".join(proj_summaries))
            if retrieved_mems:
                context_blocks.append(
                    "Memorie e preferenze rilevanti recuperate (BM25 + Time-Decay): "
                    + " | ".join([m["text"] for m in retrieved_mems])
                )
            if todos:
                context_blocks.append("To-Do attivi dell'utente: " + " | ".join(todos[:8]))
            if upcoming:
                context_blocks.append(
                    "Prossimi impegni in calendario: "
                    + " | ".join([f"{e['day_label']} {e['time_label']} {e['title']}" for e in upcoming[:6]])
                )

            system_prompt = (
                "Sei Neko AI, il gatto-assistente personale da scrivania dell'utente. "
                "Vivi nella sua App Desktop su Windows. Rispondi sempre in italiano in modo naturale, "
                "intelligente, concreto e conciso (2-4 frasi ben formattate).\n\n"
                "REGOLA FONDAMENTALE PER ESEGUIRE AZIONI REALI (CALENDARIO, TO-DO, MEMORIA):\n"
                "Non fingere MAI di aver aggiunto un evento in calendario o un To-Do senza inserire il tag d'azione reale!\n"
                "- Se l'utente ti chiede di segnare un impegno/promemoria in calendario o agenda (anche se risponde semplicemente 'sì' o 'mettilo sul calendario' riferendosi al turno precedente!), DEVI aggiungere a fine messaggio:\n"
                "  [[ACTION:CALENDAR | YYYY-MM-DD HH:MM | Titolo impegno]]\n"
                "- Se l'utente ti chiede di aggiungere una cosa da fare nella lista To-Do (task senza orario specifico), DEVI aggiungere a fine messaggio:\n"
                "  [[ACTION:TODO | Testo sintetico della task]]\n"
                "- Se l'utente ti comunica un'informazione importante su chi è, sul suo lavoro o una regola da ricordare sempre, aggiungi a fine messaggio:\n"
                "  [[ACTION:MEMORY | Sintesi chiara del fatto da ricordare]]\n\n"
                "Contesto attuale:\n"
                + "\n".join(context_blocks)
            )

            # Fit chat history with TokenBudgetManager (preserves context window)
            recent_turns = self.get_chat_history(limit=10)
            fitted_history = budget_mgr.fit_chat_history(recent_turns, 600)
            messages = [{"role": "system", "content": system_prompt}]
            for turn in fitted_history:
                r = turn.get("role")
                t_content = turn.get("text") or turn.get("content")
                if r in ("user", "assistant") and t_content:
                    messages.append({"role": r, "content": t_content})

            fitted_clip = budget_mgr.fit_clipboard(clipboard_context, 400) if clipboard_context else ""
            user_msg = text + (f"\n{fitted_clip}" if fitted_clip else "")
            messages.append({"role": "user", "content": user_msg})

            payload = build_ollama_payload(
                model=model_to_use,
                messages=messages,
                stream=False,
                num_ctx=8192,
            )
            req = urllib.request.Request(
                f"{OLLAMA_BASE}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45.0) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                msg = res_data.get("message", {}).get("content", "").strip()
                clean_msg, llm_actions = self._execute_llm_action_tags(msg or "Miao! Fatto!", text)
                return self._finish_reply(text, clean_msg, model_to_use, actions=llm_actions)
        except Exception as e:
            return self._finish_reply(
                text, f"⚠️ Errore connessione Ollama ({model_to_use}): {str(e)}", model_to_use
            )
        finally:
            self.is_generating = False


class SystemStateMonitor:
    def __init__(
        self,
        audio: WindowsAudioCapture,
        ollama: OllamaLocalBridge,
        calendar: CalendarManager,
        mem_proj: MemoryAndProjectsManager,
        win_conn: WindowAndConnectionsManager,
    ):
        self.audio = audio
        self.ollama = ollama
        self.calendar = calendar
        self.mem_proj = mem_proj
        self.win_conn = win_conn
        self.telemetry = get_telemetry_service(
            ollama_host=OLLAMA_BASE,
            vram_threshold_pct=85.0,
            afk_threshold_sec=300.0,
        )
        self.cpu_percent = 12.0
        self.ram_percent = 38.0
        self.ai_working = False
        self.ai_source = "IDLE"
        self.ai_detail = ""
        self.code_version = 0.0

        self._last_idle = 0
        self._last_kernel = 0
        self._last_user = 0

        self._active_clients = {}
        self.shared_cat = {
            "worldCol": 60.0,
            "row": 22.0,
            "facing": 1,
            "isWalking": False,
            "walkPhase": 0.0,
            "emotion": "normal",
            "idleState": "stand",
            "toy": None,
            "remoteCommand": None,
            "cmdId": 0,
        }

        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _read_cpu_and_ram(self):
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(byref(mem))
        ram = float(mem.dwMemoryLoad)

        idle_ft = FILETIME()
        kernel_ft = FILETIME()
        user_ft = FILETIME()
        cpu = self.cpu_percent
        if ctypes.windll.kernel32.GetSystemTimes(byref(idle_ft), byref(kernel_ft), byref(user_ft)):
            i_now = _filetime_to_int(idle_ft)
            k_now = _filetime_to_int(kernel_ft)
            u_now = _filetime_to_int(user_ft)

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

            root_hwnd = user32.GetAncestor(hwnd, 2)
            root_cls_buf = ctypes.create_unicode_buffer(128)
            title_buf = ctypes.create_unicode_buffer(256)
            if root_hwnd:
                user32.GetClassNameW(root_hwnd, root_cls_buf, 128)
                user32.GetWindowTextW(root_hwnd, title_buf, 256)
            root_cls = root_cls_buf.value
            win_title = title_buf.value

            if root_cls in ("Progman", "WorkerW") or cls_name in ("SysListView32", "SHELLDLL_DefView"):
                is_desktop = True
            elif "ASCII AI Companion" in win_title or "AsciiNeko" in win_title:
                is_desktop = True
            else:
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
        cal_summary = self.calendar.get_summary_state()
        mems = self.ollama.get_memories()
        todos = self.ollama.load_todos()
        p_state = self.mem_proj.get_projects_state()
        active_proj = p_state.get("active_project")
        traits_count = len(self.mem_proj.profile.get("traits", []))
        proj_notes_count = sum(len(p.get("notes", [])) for p in p_state.get("projects", []))

        telem_data = self.telemetry.get_snapshot() if hasattr(self, "telemetry") else {}

        # Phase 3: Task Engine & Reactive Pet (F22, F23, F28, F29)
        te_engine = get_task_engine()
        active_task = te_engine.get_active_task()

        sr_registry = get_skill_registry()
        synthesis_candidates = sr_registry.get_synthesis_candidates()

        is_afk_active = bool(
            telem_data.get("afk", {}).get("is_afk", False)
            or telem_data.get("afk", {}).get("idle_seconds", 0.0) >= 300.0
            or (time.time() - getattr(self, "_last_desktop_action", time.time())) >= 300.0
        )
        vram_pct = float(telem_data.get("vram_percent", 0.0))
        has_task_error = bool(active_task and active_task.get("status") in ("FAILED", "ABORTED"))
        is_ollama_generating = bool(self.ai_working or getattr(self.ollama, "is_generating", False))
        is_task_running = bool(active_task and active_task.get("status") in ("RUNNING", "RETRY"))
        is_dcc_render = bool(
            telem_data.get("active_window", {}).get("is_dcc")
            and (telem_data.get("gpu_percent", 0.0) > 70.0 or vram_pct > 80.0)
        )

        has_imminent_deadline = False
        if cal_summary and cal_summary.get("imminent_events"):
            has_imminent_deadline = True

        if is_afk_active:
            cat_state = "SLEEPING"
        elif has_task_error or vram_pct >= 85.0 or has_imminent_deadline:
            cat_state = "ALERT"
        elif is_ollama_generating:
            cat_state = "THINKING"
        elif is_task_running or is_dcc_render:
            cat_state = "RUNNING"
        else:
            cat_state = "IDLE"

        with self._lock:
            snap = {
                "cat_state": cat_state,
                "catState": cat_state,
                "active_task": active_task,
                "task_progress": active_task["progress_pct"] if active_task else 0,
                "task_visualizer": active_task["visualizer"] if active_task else "",
                "task_status": active_task["status"] if active_task else "IDLE",
                "task_title": active_task["title"] if active_task else "",
                "synthesis_candidates": synthesis_candidates,
                "cpu": telem_data.get("cpu_percent", self.cpu_percent),
                "ram": telem_data.get("ram_percent", self.ram_percent),
                "gpu": telem_data.get("gpu_percent", 0.0),
                "vram_used_gb": telem_data.get("vram_used_gb", 0.0),
                "vram_total_gb": telem_data.get("vram_total_gb", 0.0),
                "vram_percent": telem_data.get("vram_percent", 0.0),
                "gpu_name": telem_data.get("gpu_name", "N/A"),
                "active_window": telem_data.get("active_window", {}),
                "afk": telem_data.get("afk", {"is_afk": False, "idle_seconds": 0.0}),
                "is_afk": telem_data.get("afk", {}).get("is_afk", False),
                "afk_idle_seconds": telem_data.get("afk", {}).get("idle_seconds", 0.0),
                "project_time": telem_data.get("project_time", {}),
                "project_time_today_sec": telem_data.get("project_time", {}).get("current_project_seconds", 0.0),
                "current_app_seconds_today": telem_data.get("project_time", {}).get("current_app_seconds", 0.0),
                "vram_guard": telem_data.get("vram_guard", {}),
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
                "ollama_online": self.ollama.ollama_online,
                "ollama_pull": self.win_conn.ollama_pull_status,
                "memory_count": len(mems) + traits_count + proj_notes_count,
                "todo_count": len(todos),
                "todos_list": list(todos[:15]),
                "projects_count": len(p_state.get("projects", [])),
                "archive_count": len(p_state.get("archive", [])),
                "stale_projects_count": len(p_state.get("stale_projects", [])),
                "active_project_name": telem_data.get("active_window", {}).get("project_name") or (active_proj["name"] if active_proj else ""),
                "profile_answered_count": len(self.mem_proj.profile.get("answers", {})),
                "hotkey": self.mem_proj.profile.get("hotkey", "Alt+N"),
                "window_mode": self.win_conn.window_mode,
                "focus_input_seq": self.win_conn.focus_input_seq,
                "calendar": cal_summary,
                "timestamp": time.time(),
            }
        snap.update(self.audio.get_audio_state())
        return snap


audio_capture = WindowsAudioCapture()
calendar_manager = CalendarManager()
mem_proj_manager = MemoryAndProjectsManager()
ollama_bridge = OllamaLocalBridge(calendar_manager, mem_proj_manager)
win_conn_manager = WindowAndConnectionsManager(ollama_bridge, calendar_manager, mem_proj_manager)
monitor = SystemStateMonitor(audio_capture, ollama_bridge, calendar_manager, mem_proj_manager, win_conn_manager)


class CompanionRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory or PROJECT_ROOT, **kwargs)

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def _send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
            monitor.update_shared_cat(body)
            return self._send_json({"ok": True})

        if self.path.startswith("/ai_chat"):
            prompt = body.get("prompt", "")
            model = body.get("model", "")
            result = ollama_bridge.handle_chat_or_command(
                prompt,
                requested_model=model,
                cpu=monitor.cpu_percent,
                ram=monitor.ram_percent,
            )
            return self._send_json(result)

        if self.path.startswith("/chat_clear"):
            ollama_bridge.clear_chat_history()
            return self._send_json({"ok": True, "history": []})

        if self.path.startswith("/calendar_config"):
            ical_url = body.get("ical_url")
            token = body.get("google_access_token")
            clear_urls = bool(body.get("clear_urls", False))
            cfg = calendar_manager.update_config(
                ical_url=ical_url, google_access_token=token, clear_urls=clear_urls
            )
            return self._send_json({"ok": True, "config": cfg, "calendar": calendar_manager.get_summary_state()})

        if self.path.startswith("/pc_action"):
            target = body.get("open_path") or body.get("target") or ""
            ok, msg = WindowsPCActions.open_target(target)
            return self._send_json({"ok": ok, "message": msg})

        if self.path.startswith("/conosciamoci"):
            action = body.get("action", "answer")
            category = body.get("category") or None
            if action == "random":
                exclude_id = body.get("exclude_id") or None
                gen_ai = bool(body.get("generate_ai", True))
                st = mem_proj_manager.get_onboarding_state(
                    category=category, exclude_id=exclude_id, generate_ai=gen_ai
                )
                return self._send_json({"ok": True, "onboarding": st})
            # Default: save answer
            qid = body.get("question_id", "")
            ans = body.get("answer", "")
            qtxt = body.get("question_text", "")
            st = mem_proj_manager.save_onboarding_answer(qid, ans, category=category or "", question_text=qtxt)
            return self._send_json({"ok": True, "onboarding": st})

        if self.path.startswith("/projects"):
            action = body.get("action", "list")
            if action == "create":
                res = mem_proj_manager.create_or_update_project(
                    name=body.get("name", ""),
                    client=body.get("client", ""),
                    note=body.get("note", ""),
                    pin_path=body.get("pin_path", ""),
                    pin_label=body.get("pin_label", ""),
                )
                return self._send_json(res)
            if action == "set_active":
                st = mem_proj_manager.set_active_project(body.get("project_id", ""))
                return self._send_json({"ok": True, "state": st})
            if action == "add_note":
                st = mem_proj_manager.add_project_note(body.get("project_id", ""), body.get("note", ""))
                return self._send_json({"ok": True, "state": st})
            if action == "delete_note":
                st = mem_proj_manager.delete_project_note(
                    body.get("project_id", ""), int(body.get("note_index", -1))
                )
                return self._send_json({"ok": True, "state": st})
            if action == "add_pin":
                st = mem_proj_manager.add_project_pin(
                    body.get("project_id", ""), body.get("label", ""), body.get("path", "")
                )
                return self._send_json({"ok": True, "state": st})
            if action == "delete_pin":
                st = mem_proj_manager.delete_project_pin(body.get("project_id", ""), body.get("pin_id", ""))
                return self._send_json({"ok": True, "state": st})
            if action == "capture_open":
                open_items = MemoryAndProjectsManager.capture_open_windows_paths()
                return self._send_json({"ok": True, "open_windows": open_items})
            if action == "archive":
                res = mem_proj_manager.archive_project(body.get("project_id", ""))
                return self._send_json(res)
            if action == "restore":
                res = mem_proj_manager.restore_from_archive(
                    archive_id=body.get("archive_id", ""),
                    mode=body.get("mode", "reactivate_project"),
                    target_project_id=body.get("target_project_id"),
                )
                return self._send_json(res)
            if action == "delete_archive":
                st = mem_proj_manager.delete_archived_project(body.get("archive_id", ""))
                return self._send_json({"ok": True, "state": st})

            return self._send_json({"ok": True, "state": mem_proj_manager.get_projects_state()})

        if self.path.startswith("/save_quick_memory"):
            fact = body.get("fact", "")
            proj_id = body.get("project_id", "")
            if proj_id:
                st = mem_proj_manager.add_project_note(proj_id, fact)
                return self._send_json(
                    {"ok": True, "message": f"🧠 Regola salvata nella scheda progetto!", "projects": st}
                )
            entry = ollama_bridge.add_memory(fact)
            return self._send_json(
                {
                    "ok": bool(entry),
                    "message": f"🧠 Salvato nella Memoria di Neko: \"{fact}\"",
                    "memories": ollama_bridge.get_memories(),
                }
            )

        if self.path.startswith("/connections"):
            action = body.get("action", "status")
            if action == "set_model":
                model = (body.get("model") or "").strip()
                if model:
                    ollama_bridge.active_model = model
                    with mem_proj_manager._lock:
                        mem_proj_manager.profile["preferred_model"] = model
                        mem_proj_manager._save_profile()
                return self._send_json({"ok": True, "connections": win_conn_manager.get_connections_state()})
            if action == "pull_model":
                model = (body.get("model") or "qwen2.5:7b").strip()
                pull_st = win_conn_manager.start_ollama_pull(model)
                return self._send_json(
                    {"ok": True, "pull_status": pull_st, "connections": win_conn_manager.get_connections_state()}
                )
            if action == "set_storage_dir":
                res = win_conn_manager.set_storage_dir(body.get("data_dir", ""))
                return self._send_json(res)
            if action == "pick_storage_dir":
                res = win_conn_manager.pick_storage_dir_dialog()
                return self._send_json(res)
            if action == "open_storage_dir":
                ok, msg = WindowsPCActions.open_target(DATA_DIR)
                return self._send_json({"ok": ok, "message": msg, "storage": win_conn_manager.get_storage_state()})
            if action == "add_link":
                links = mem_proj_manager.add_quick_link(body.get("label", ""), body.get("url", ""))
                return self._send_json({"ok": True, "quick_links": links})
            if action == "delete_link":
                links = mem_proj_manager.delete_quick_link(int(body.get("index", -1)))
                return self._send_json({"ok": True, "quick_links": links})
            return self._send_json({"ok": True, "connections": win_conn_manager.get_connections_state()})

        if self.path.startswith("/window_mode"):
            mode = body.get("mode", "dashboard")
            res = win_conn_manager.set_window_mode(mode)
            return self._send_json(res)

        if self.path.startswith("/settings"):
            action = body.get("action", "get_all")
            if action == "delete_profile_answer":
                mem_proj_manager.delete_onboarding_answer(body.get("question_id", ""))
            elif action == "delete_memory":
                ollama_bridge.delete_memory(body.get("target", ""))
            elif action == "add_memory":
                ollama_bridge.add_memory(body.get("text", ""))
            elif action == "delete_todo":
                todos = ollama_bridge.load_todos()
                idx = int(body.get("index", -1))
                if 0 <= idx < len(todos):
                    todos.pop(idx)
                    ollama_bridge.save_todos(todos)
            elif action == "add_todo":
                txt = (body.get("text") or "").strip()
                if txt:
                    todos = ollama_bridge.load_todos()
                    todos.append(txt)
                    ollama_bridge.save_todos(todos)
            elif action == "set_hotkey":
                mem_proj_manager.set_hotkey(body.get("hotkey", "Alt+N"))
            elif action == "reset":
                target = body.get("target", "")
                if target in ("chat", "all"):
                    ollama_bridge.clear_chat_history()
                if target in ("profile", "all"):
                    mem_proj_manager.reset_profile()
                if target in ("memories", "all"):
                    ollama_bridge.clear_memories()
                if target in ("todos", "all"):
                    ollama_bridge.save_todos([])
                if target in ("projects", "all"):
                    mem_proj_manager.reset_projects_and_archive()
                if target == "all":
                    calendar_manager.local_events = []
                    _save_json(EVENTS_FILE, [])

            return self._send_json(
                {
                    "ok": True,
                    "onboarding": mem_proj_manager.get_onboarding_state(),
                    "projects": mem_proj_manager.get_projects_state(),
                    "connections": win_conn_manager.get_connections_state(),
                    "memories": ollama_bridge.get_memories(),
                    "todos": ollama_bridge.load_todos(),
                    "hotkey": mem_proj_manager.profile.get("hotkey", "Ctrl+Alt+N"),
                }
            )

        if self.path.startswith("/api/colorsafe"):
            curr = bool(mem_proj_manager.profile.get("color_safe_mode", False))
            if "enabled" in body:
                new_mode = bool(body["enabled"])
            elif "color_safe_mode" in body:
                new_mode = bool(body["color_safe_mode"])
            else:
                new_mode = not curr
            with mem_proj_manager._lock:
                mem_proj_manager.profile["color_safe_mode"] = new_mode
                mem_proj_manager._save_profile()
            return self._send_json({"ok": True, "color_safe_mode": new_mode})

        if self.path.startswith("/api/profile/pin"):
            tid = body.get("trait_id") or body.get("id") or body.get("text") or ""
            pinned = bool(body.get("pinned", True))
            ok = mem_proj_manager.pin_profile_trait(tid, pinned=pinned)
            return self._send_json({"ok": ok, "trait_id": tid, "pinned": pinned})

        if self.path.startswith("/api/profile/edit"):
            tid = body.get("trait_id") or body.get("id") or ""
            new_text = body.get("text") or body.get("new_text") or ""
            ok = mem_proj_manager.edit_profile_trait(tid, new_text)
            return self._send_json({"ok": ok, "trait_id": tid, "text": new_text})

        if self.path.startswith("/api/profile/delete"):
            tid = body.get("trait_id") or body.get("id") or body.get("text") or ""
            ok = mem_proj_manager.delete_profile_trait(tid)
            return self._send_json({"ok": ok, "trait_id": tid})

        if self.path.startswith("/api/profile"):
            return self._send_json({"ok": True, "profile": mem_proj_manager.profile})

        # --- Phase 3: Task Engine & Skill Registry Endpoints (F22-F28) ---
        if self.path.startswith("/api/task/create"):
            te = get_task_engine()
            title = body.get("title", "Nuova Attività")
            steps = body.get("steps", [])
            skill_id = body.get("skill_id")
            project_id = body.get("project_id")
            priority = int(body.get("priority", 1))
            max_retries = int(body.get("max_retries", 3))
            tid = te.create_task(title, steps=steps, skill_id=skill_id, project_id=project_id, priority=priority, max_retries=max_retries)
            return self._send_json({"ok": True, "task_id": tid, "task": te.get_task(tid)})

        if self.path.startswith("/api/task/abort"):
            te = get_task_engine()
            tid = body.get("task_id")
            if not tid:
                active = te.get_active_task()
                if active:
                    tid = active["id"]
            if tid:
                ok = te.abort_task(tid)
                return self._send_json({"ok": ok, "task_id": tid, "task": te.get_task(tid)})
            return self._send_json({"ok": False, "message": "Nessun task attivo da annullare"})

        if self.path.startswith("/api/task/step"):
            te = get_task_engine()
            tid = body.get("task_id")
            if not tid:
                active = te.get_active_task()
                if active:
                    tid = active["id"]
            if tid:
                task = te.execute_next_step(tid)
                return self._send_json({"ok": True, "task": task})
            return self._send_json({"ok": False, "message": "Task ID non fornito"})

        if self.path.startswith("/api/task/hitl"):
            te = get_task_engine()
            tid = body.get("task_id")
            if not tid:
                active = te.get_active_task()
                if active:
                    tid = active["id"]
            choice = int(body.get("choice", 1))
            files = body.get("files") or body.get("partial_files") or []
            if tid:
                try:
                    res = te.handle_hitl_recovery(tid, choice, files)
                    return self._send_json({"ok": True, "recovery": res, "task": te.get_task(tid)})
                except ValueError as e:
                    return self._send_json({"ok": False, "error": str(e)})
            return self._send_json({"ok": False, "message": "Task ID non fornito"})

        if self.path.startswith("/api/task/action"):
            action = body.get("action", "")
            te = get_task_engine()
            tid = body.get("task_id")
            if not tid:
                active = te.get_active_task()
                if active:
                    tid = active["id"]
            if action == "abort":
                ok = te.abort_task(tid) if tid else False
                return self._send_json({"ok": ok, "task": te.get_task(tid) if tid else None})
            elif action == "step":
                t = te.execute_next_step(tid) if tid else None
                return self._send_json({"ok": bool(t), "task": t})
            elif action == "hitl":
                choice = int(body.get("choice", 1))
                files = body.get("files") or []
                res = te.handle_hitl_recovery(tid, choice, files) if tid else None
                return self._send_json({"ok": bool(res), "recovery": res, "task": te.get_task(tid) if tid else None})
            return self._send_json({"ok": False, "message": f"Azione non valida: {action}"})

        if self.path.startswith("/api/skills/execute"):
            sr = get_skill_registry()
            sid = body.get("skill_id") or body.get("id") or body.get("cmd") or ""
            if sid.startswith("/"):
                sid = sid[1:]
            params = body.get("params") or {}
            confirmed = bool(body.get("confirmed", False))
            auth_token = body.get("auth_token") or body.get("confirmation_text")
            res = sr.execute_skill(sid, params=params, confirmed=confirmed, auth_token=auth_token)
            sr.record_action(sid)
            return self._send_json({"ok": True, "result": res})

        if self.path.startswith("/api/skills/synthesize") or self.path.startswith("/api/skills/create"):
            sr = get_skill_registry()
            sig = body.get("signature") or body.get("id") or body.get("name") or ""
            name = body.get("name")
            tier = body.get("safety_tier", "CONFIRM_ONCE")
            template = body.get("command_template")
            skill = sr.synthesize_skill(sig, name=name, safety_tier=tier, command_template=template)
            return self._send_json({"ok": True, "skill": skill})

        if self.path.startswith("/api/skills/record_action"):
            sr = get_skill_registry()
            sig = body.get("signature", "")
            sr.record_action(sig)
            return self._send_json({"ok": True, "is_trigger": sr.check_synthesis_trigger(sig)})

        self.send_response(404)
        self.end_headers()

    def _extract_client_id(self):
        if "client_id=" in self.path:
            return self.path.split("client_id=")[1].split("&")[0]
        return None

    def do_GET(self):
        client_id = self._extract_client_id()
        if self.path.startswith("/api/colorsafe"):
            return self._send_json({"ok": True, "color_safe_mode": bool(mem_proj_manager.profile.get("color_safe_mode", False))})

        if self.path.startswith("/api/task/active"):
            te = get_task_engine()
            return self._send_json({"ok": True, "task": te.get_active_task()})

        if self.path.startswith("/api/task/list"):
            te = get_task_engine()
            return self._send_json({"ok": True, "tasks": te.list_tasks()})

        if self.path.startswith("/api/skills/synthesis_candidates"):
            sr = get_skill_registry()
            return self._send_json({"ok": True, "candidates": sr.get_synthesis_candidates()})

        if self.path.startswith("/api/skills"):
            sr = get_skill_registry()
            all_skills = sr.load_skills()
            formatted = []
            for s in all_skills:
                triggers = s.get("trigger_patterns", [])
                cmd = triggers[0] if triggers and triggers[0].startswith("/") else f"/{s['id']}"
                formatted.append({
                    "id": s["id"],
                    "cmd": cmd,
                    "name": s.get("name", s["id"]),
                    "desc": s.get("description", ""),
                    "tier": s.get("safety_tier", "CONFIRM_ONCE"),
                    "safety_tier": s.get("safety_tier", "CONFIRM_ONCE"),
                    "triggers": triggers,
                })
            return self._send_json({"ok": True, "skills": formatted})

        if self.path.startswith("/api/profile"):
            sm = get_storage_manager()
            all_mems = sm.get_memories()
            p_traits = list(mem_proj_manager.profile.get("traits", []))
            pinned_set = set(mem_proj_manager.profile.get("pinned_traits", []))
            for m in all_mems:
                if m.get("pinned"):
                    pinned_set.add(m.get("text"))
            items = []
            seen_texts = set()
            for m in all_mems:
                t_text = m.get("text", "")
                if t_text in seen_texts:
                    continue
                seen_texts.add(t_text)
                is_pin = bool(m.get("pinned") or t_text in pinned_set)
                items.append({
                    "id": m.get("id"),
                    "text": t_text,
                    "category": m.get("category", "Memoria"),
                    "origin": m.get("source", "user"),
                    "confidence": 1.0 if is_pin else (0.95 if m.get("importance", 0.5) >= 0.8 else 0.85),
                    "pinned": is_pin,
                    "updated_at": m.get("updated_at", 0)
                })
            for t in p_traits:
                if t not in seen_texts:
                    seen_texts.add(t)
                    is_pin = t in pinned_set
                    items.append({
                        "id": f"trait_{abs(hash(t)) % 1000000}",
                        "text": t,
                        "category": "Profilo",
                        "origin": "Dichiarato",
                        "confidence": 1.0 if is_pin else 0.90,
                        "pinned": is_pin,
                        "updated_at": time.time()
                    })
            return self._send_json({
                "ok": True,
                "traits": items,
                "profile": mem_proj_manager.profile,
                "color_safe_mode": bool(mem_proj_manager.profile.get("color_safe_mode", False))
            })

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
                    payload = json.dumps(monitor.get_snapshot(client_id), ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.04)
            except Exception:
                return

        if self.path.startswith("/status"):
            return self._send_json(monitor.get_snapshot(client_id))

        if self.path.startswith("/chat_history"):
            return self._send_json(
                {
                    "history": ollama_bridge.get_chat_history(limit=60),
                    "memories": ollama_bridge.get_memories(),
                    "todos": ollama_bridge.load_todos(),
                    "calendar": calendar_manager.get_summary_state(),
                    "projects": mem_proj_manager.get_projects_state(),
                    "onboarding": mem_proj_manager.get_onboarding_state(),
                }
            )

        if self.path.startswith("/app_data"):
            return self._send_json(
                {
                    "onboarding": mem_proj_manager.get_onboarding_state(),
                    "projects": mem_proj_manager.get_projects_state(),
                    "connections": win_conn_manager.get_connections_state(),
                    "memories": ollama_bridge.get_memories(),
                    "todos": ollama_bridge.load_todos(),
                }
            )

        return super().do_GET()


def start_background_server():
    """Avvia il server HTTP locale 127.0.0.1:8765 in un thread daemon nello stesso processo di pywebview."""
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), CompanionRequestHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv
    except Exception:
        return None


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), CompanionRequestHandler)
    print("==========================================================")
    print("  ASCII NEKO COMPANION // LOCAL AI + CALENDAR + PC BRIDGE")
    print(f"  - Stream Live 25fps : http://127.0.0.1:{PORT}/stream")
    print(f"  - Chat AI Locale    : http://127.0.0.1:{PORT}/ai_chat")
    print("==========================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
