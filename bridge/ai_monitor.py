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

PORT = 8765
OLLAMA_BASE = "http://127.0.0.1:11434"
BRIDGE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(BRIDGE_DIR, ".."))

TODO_FILE = os.path.join(BRIDGE_DIR, "todo.json")
CHAT_HISTORY_FILE = os.path.join(BRIDGE_DIR, "chat_history.json")
MEMORY_FILE = os.path.join(BRIDGE_DIR, "memory.json")
CALENDAR_CONFIG_FILE = os.path.join(BRIDGE_DIR, "calendar_config.json")
EVENTS_FILE = os.path.join(BRIDGE_DIR, "events.json")
USER_HOME = os.path.expanduser("~")


# --- Helpers JSON su disco ---
def _load_json(path, default):
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


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

        # 3. Percorso diretto su disco
        if os.path.exists(raw):
            os.startfile(raw)
            return True, f"📂 Ho aperto `{raw}`"

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


class OllamaLocalBridge:
    """
    Gestisce il cervello locale di Neko:
    - Conversazione con Ollama + Cronologia Chat persistente (chat_history.json)
    - Memoria a lungo termine (memory.json)
    - Calendario (Google Calendar + Eventi locali)
    - Azioni rapide sul PC (App, File, Appunti, To-Do, Briefing)
    """

    def __init__(self, calendar_mgr: CalendarManager):
        self.calendar = calendar_mgr
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
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                self.models = models
                self.ollama_online = True
                if models and not self.active_model:
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

    def _append_chat_turn(self, user_text, reply_text, model_used, actions=None):
        now_str = datetime.now().strftime("%H:%M")
        ts = time.time()
        with self._lock:
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
            # Evita duplicati identici
            for m in self.memories:
                if m.get("text", "").lower() == clean.lower():
                    return m
            entry = {
                "id": f"mem_{int(time.time() * 1000)}",
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
            if str(index_or_query).isdigit():
                idx = int(index_or_query) - 1
                if 0 <= idx < len(self.memories):
                    self.memories.pop(idx)
                    _save_json(MEMORY_FILE, self.memories)
                    return True
            q = str(index_or_query).lower().strip()
            before = len(self.memories)
            self.memories = [m for m in self.memories if q not in m.get("text", "").lower()]
            if len(self.memories) < before:
                _save_json(MEMORY_FILE, self.memories)
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
        """Estrae data e ora da frasi come 'domani alle 15:30', 'oggi alle 18', '2026-10-28 14:00'."""
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

        hour, minute = 9, 0
        m_time = re.search(r"(?:alle|ore|\b)(\d{1,2})[:\.](\d{2})\b", low)
        m_hour_only = re.search(r"\b(?:alle|ore)\s+(\d{1,2})\b", low)
        if m_time:
            hour = min(23, max(0, int(m_time.group(1))))
            minute = min(59, max(0, int(m_time.group(2))))
        elif m_hour_only:
            hour = min(23, max(0, int(m_hour_only.group(1))))
            minute = 0

        # Pulisci il titolo rimuovendo parole temporali
        clean_title = re.sub(
            r"\b(oggi|domani|dopodomani|alle\s+\d{1,2}([:\.]\d{2})?|ore\s+\d{1,2}([:\.]\d{2})?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(/\d{2,4})?)\b",
            "",
            text,
            flags=re.IGNORECASE,
        )
        clean_title = re.sub(r"\s+", " ", clean_title).strip(" -:,")
        if not clean_title:
            clean_title = "Impegno a calendario"

        return datetime(target_date.year, target_date.month, target_date.day, hour, minute), clean_title

    def build_daily_briefing(self, cpu=0, ram=0):
        now = datetime.now()
        cal_state = self.calendar.get_summary_state()
        upcoming = cal_state.get("upcoming_events", [])
        today_evs = [e for e in upcoming if e.get("is_today")]
        todos = self.load_todos()
        mems = self.get_memories()

        parts = [f"☀️ **Briefing di Neko ({now.strftime('%d/%m/%Y %H:%M')})**"]

        if today_evs:
            ev_strs = [f"• {e['time_label']} — {e['title']}" for e in today_evs[:5]]
            parts.append("📅 **Impegni di Oggi:** " + " | ".join(ev_strs))
        elif upcoming:
            nxt = upcoming[0]
            parts.append(f"📅 **Oggi nessun impegno.** Prossimo: {nxt['day_label']} {nxt['time_label']} — {nxt['title']}")
        else:
            parts.append("📅 **Agenda:** Nessun evento in programma (usa `cal add ...` o collega Google Calendar con `⚙️ CAL`).")

        if todos:
            td_strs = [f"[{i+1}] {t}" for i, t in enumerate(todos[:5])]
            parts.append("📝 **To-Do Attivi:** " + " · ".join(td_strs))
        else:
            parts.append("📝 **To-Do:** Tutti completati!")

        if mems:
            parts.append(f"🧠 **Memoria Attiva:** {len(mems)} note salvate (ultima: \"{mems[-1]['text']}\")")

        parts.append(f"💻 **Sistema:** CPU {cpu}% · RAM {ram}% · Modello: {self.active_model or 'Ollama'}")
        return "\n".join(parts)

    def _finish_reply(self, prompt_text, reply_text, model_name="LOCAL-TOOL", actions=None):
        self._append_chat_turn(prompt_text, reply_text, model_name, actions=actions)
        return {
            "reply": reply_text,
            "model": model_name,
            "actions": actions or [],
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
                        text, "📝 La tua lista To-Do è vuota! Scrivi `todo: <cosa da fare>` per aggiungere."
                    )
                formatted = " | ".join([f"[{i+1}] {t}" for i, t in enumerate(todos)])
                return self._finish_reply(
                    text, f"📝 To-Do attivi ({len(todos)}): {formatted} (usa `todo done 1` o `todo clear`)"
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
        if low.startswith(("/mem", "ricorda che ", "ricordati che ", "memorizza ", "nota: ", "salva in memoria")):
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

        # 5. Gestione Calendario & Google Calendar (/cal, "che impegni ho...", "fissa/aggiungi impegno...")
        if (
            low.startswith(("/cal", "cal:", "calendario", "agenda"))
            or any(
                p in low
                for p in (
                    "che impegni ho",
                    "cosa ho in calendario",
                    "prossimi appuntamenti",
                    "prossimi impegni",
                    "aggiungi impegno",
                    "aggiungi evento",
                    "fissa appuntamento",
                    "fissa call",
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
            is_add = low.startswith(("/cal add ", "cal add ", "aggiungi impegno", "aggiungi evento", "fissa appuntamento", "fissa call"))
            if is_add:
                raw_ev = text
                for pref in ("/cal add ", "cal add ", "aggiungi impegno:", "aggiungi impegno", "aggiungi evento:", "aggiungi evento", "fissa appuntamento:", "fissa appuntamento", "fissa:"):
                    if low.startswith(pref):
                        raw_ev = text[len(prefix) if False else len(pref) :].strip()
                        break
                if raw_ev.lower().startswith("fissa "):
                    raw_ev = raw_ev[6:].strip()
                dt_start, ev_title = self._parse_natural_date_time(raw_ev)
                ev = self.calendar.add_event(ev_title, dt_start)
                g_note = " (sincronizzato anche su Google Calendar API!)" if ev.get("created_on_google") else ""
                actions = [
                    {
                        "label": "📅 + Aggiungi su Google Calendar Web",
                        "url": ev["gcal_url"],
                    }
                ]
                return self._finish_reply(
                    text,
                    f"📅 Impegno salvato in agenda per il **{dt_start.strftime('%d/%m/%Y alle %H:%M')}**: **\"{ev['title']}\"**{g_note}",
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
                    else ""
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

        # 9. Richiesta al Modello LLM Locale (Ollama) con Cronologia Conversazionale + Contesto Memoria & Agenda
        if not self.ollama_online:
            self._fetch_models()

        model_to_use = requested_model or self.active_model
        if not model_to_use:
            return self._finish_reply(
                text,
                "⚠️ Ollama è installato ma non ho trovato modelli scaricati (esegui `ollama pull llama3.2`). Intanto puoi usare `/briefing`, `/cal`, `todo: ...`, `Ricorda che...` e `Apri <programma>`!",
                "OFFLINE",
            )

        self.is_generating = True
        try:
            now_dt = datetime.now()
            mems = self.get_memories()
            todos = self.load_todos()
            upcoming = self.calendar.get_upcoming_events(days_ahead=3)

            context_blocks = [
                f"Data e ora attuali: {now_dt.strftime('%A %d/%m/%Y, ore %H:%M')}.",
                f"Stato PC: CPU {cpu}%, RAM {ram}%.",
            ]
            if mems:
                context_blocks.append(
                    "Fatti e preferenze che ricordi sull'utente (Diego): "
                    + " | ".join([m["text"] for m in mems[-12:]])
                )
            if todos:
                context_blocks.append("To-Do attivi dell'utente: " + " | ".join(todos[:8]))
            if upcoming:
                context_blocks.append(
                    "Prossimi impegni in calendario: "
                    + " | ".join([f"{e['day_label']} {e['time_label']} {e['title']}" for e in upcoming[:6]])
                )

            system_prompt = (
                "Sei Neko AI, il gatto-assistente personale da scrivania di Diego. "
                "Vivi sul suo Desktop Windows. Rispondi sempre in italiano in modo naturale, "
                "intelligente, concreto e conciso (2-4 frasi ben formattate). "
                "Ricorda il contesto della conversazione e i dati seguenti:\n"
                + "\n".join(context_blocks)
            )

            # Includi gli ultimi 10 messaggi della cronologia per avere una vera conversazione continua!
            recent_turns = self.get_chat_history(limit=10)
            messages = [{"role": "system", "content": system_prompt}]
            for turn in recent_turns:
                r = turn.get("role")
                if r in ("user", "assistant") and turn.get("text"):
                    messages.append({"role": r, "content": turn["text"]})

            messages.append({"role": "user", "content": text + clipboard_context})

            payload = {
                "model": model_to_use,
                "messages": messages,
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
                return self._finish_reply(text, msg or "Miao! Fatto!", model_to_use)
        except Exception as e:
            return self._finish_reply(
                text, f"⚠️ Errore connessione Ollama ({model_to_use}): {str(e)}", model_to_use
            )
        finally:
            self.is_generating = False


class SystemStateMonitor:
    def __init__(self, audio: WindowsAudioCapture, ollama: OllamaLocalBridge, calendar: CalendarManager):
        self.audio = audio
        self.ollama = ollama
        self.calendar = calendar
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
                "memory_count": len(mems),
                "todo_count": len(todos),
                "calendar": cal_summary,
                "timestamp": time.time(),
            }
        snap.update(self.audio.get_audio_state())
        return snap


audio_capture = WindowsAudioCapture()
calendar_manager = CalendarManager()
ollama_bridge = OllamaLocalBridge(calendar_manager)
monitor = SystemStateMonitor(audio_capture, ollama_bridge, calendar_manager)


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
                }
            )

        return super().do_GET()


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
