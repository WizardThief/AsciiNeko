"""
bridge/telemetry.py
===================
Hardware Telemetry, Dynamic VRAM Guard, Window Title Tracker & AFK Detector.

Part of AsciiNeko Golden Architecture (Phases 1, 2, 3).
Provides:
1. HardwareTelemetryCollector: CPU, RAM, GPU %, VRAM used/total GB via NVML ctypes & psutil/Win32.
2. DynamicVRAMGuard: Ollama VRAM unload (keep_alive: 0) on VRAM > 85% or heavy DCC running.
3. WindowAndAFKTracker: Foreground window parsing (3ds Max, Blender, Unreal, etc.) & Win32 AFK idle detection.
4. TelemetryService: Background 1.0s sampler and integration hub for ai_monitor.py.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes, byref, sizeof, Structure, c_uint, c_ulonglong, c_void_p
import datetime
import json
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("neko.telemetry")

# ---------------------------------------------------------------------------
# Optional psutil acceleration
# ---------------------------------------------------------------------------
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    psutil = None
    _HAS_PSUTIL = False

# ---------------------------------------------------------------------------
# Win32 Ctypes Structures & APIs
# ---------------------------------------------------------------------------
try:
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
except (AttributeError, OSError):
    kernel32 = None
    user32 = None


class FILETIME(Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class MEMORYSTATUSEX(Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", c_ulonglong),
        ("ullAvailPhys", c_ulonglong),
        ("ullTotalPageFile", c_ulonglong),
        ("ullAvailPageFile", c_ulonglong),
        ("ullTotalVirtual", c_ulonglong),
        ("ullAvailVirtual", c_ulonglong),
        ("ullAvailExtendedVirtual", c_ulonglong),
    ]


class LASTINPUTINFO(Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class PROCESSENTRY32(Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002


def _filetime_to_int(ft: FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


# ---------------------------------------------------------------------------
# NVML Ctypes Structures & Loader
# ---------------------------------------------------------------------------
class nvmlUtilization_t(Structure):
    _fields_ = [
        ("gpu", c_uint),      # Percent of time over past sample period GPU was active (0 - 100)
        ("memory", c_uint),   # Percent of time memory controller was active (0 - 100)
    ]


class nvmlMemory_t(Structure):
    _fields_ = [
        ("total", c_ulonglong),  # Total installed FB memory in bytes
        ("free", c_ulonglong),   # Unallocated FB memory in bytes
        ("used", c_ulonglong),   # Allocated FB memory in bytes
    ]


class NVMLDriver:
    """Singleton wrapper for NVIDIA nvml.dll via ctypes."""

    def __init__(self):
        self.available = False
        self.dll = None
        self.device = None
        self.gpu_name = "N/A"
        self._init_nvml()

    def _init_nvml(self):
        try:
            self.dll = ctypes.CDLL("nvml.dll")
            init_fn = getattr(self.dll, "nvmlInit_v2", getattr(self.dll, "nvmlInit", None))
            if not init_fn or init_fn() != 0:
                self.available = False
                return

            self.device = c_void_p()
            get_handle = getattr(
                self.dll,
                "nvmlDeviceGetHandleByIndex_v2",
                getattr(self.dll, "nvmlDeviceGetHandleByIndex", None),
            )
            if not get_handle or get_handle(0, byref(self.device)) != 0:
                self.available = False
                return

            name_buf = ctypes.create_string_buffer(64)
            if hasattr(self.dll, "nvmlDeviceGetName"):
                if self.dll.nvmlDeviceGetName(self.device, name_buf, 64) == 0:
                    self.gpu_name = name_buf.value.decode("utf-8", errors="ignore").strip()

            self.available = True
        except Exception as e:
            logger.debug(f"NVML unavailable: {e}")
            self.available = False
            self.dll = None
            self.device = None

    def query(self) -> Tuple[float, float, float, float]:
        """Returns (gpu_percent, vram_used_gb, vram_total_gb, vram_percent)."""
        if not self.available or not self.dll or not self.device:
            return 0.0, 0.0, 0.0, 0.0

        try:
            util = nvmlUtilization_t()
            mem = nvmlMemory_t()

            ret_u = self.dll.nvmlDeviceGetUtilizationRates(self.device, byref(util))
            ret_m = self.dll.nvmlDeviceGetMemoryInfo(self.device, byref(mem))

            gpu_pct = float(util.gpu) if ret_u == 0 else 0.0
            if ret_m == 0 and mem.total > 0:
                used_gb = round(mem.used / (1024.0 ** 3), 2)
                total_gb = round(mem.total / (1024.0 ** 3), 2)
                vram_pct = round((mem.used / mem.total) * 100.0, 1)
            else:
                used_gb, total_gb, vram_pct = 0.0, 0.0, 0.0

            return gpu_pct, used_gb, total_gb, vram_pct
        except Exception:
            return 0.0, 0.0, 0.0, 0.0

    def shutdown(self):
        if self.available and self.dll and hasattr(self.dll, "nvmlShutdown"):
            try:
                self.dll.nvmlShutdown()
            except Exception:
                pass
            self.available = False


# ---------------------------------------------------------------------------
# Hardware Telemetry Collector
# ---------------------------------------------------------------------------
class HardwareTelemetryCollector:
    """Collects CPU, RAM, GPU %, and VRAM metrics with zero-overhead fallback."""

    def __init__(self):
        self.nvml = NVMLDriver()
        self._last_idle = 0
        self._last_kernel = 0
        self._last_user = 0
        self._last_cpu = 10.0
        self._last_ram = 35.0

    def get_cpu_and_ram(self) -> Tuple[float, float]:
        if _HAS_PSUTIL and psutil:
            try:
                cpu = float(psutil.cpu_percent(interval=None))
                ram = float(psutil.virtual_memory().percent)
                self._last_cpu, self._last_ram = cpu, ram
                return round(cpu, 1), round(ram, 1)
            except Exception:
                pass

        # Win32 ctypes fallback
        if kernel32:
            try:
                mem = MEMORYSTATUSEX()
                mem.dwLength = sizeof(MEMORYSTATUSEX)
                kernel32.GlobalMemoryStatusEx(byref(mem))
                ram = float(mem.dwMemoryLoad)

                idle_ft = FILETIME()
                kernel_ft = FILETIME()
                user_ft = FILETIME()
                cpu = self._last_cpu
                if kernel32.GetSystemTimes(byref(idle_ft), byref(kernel_ft), byref(user_ft)):
                    i_now = _filetime_to_int(idle_ft)
                    k_now = _filetime_to_int(kernel_ft)
                    u_now = _filetime_to_int(user_ft)

                    total = (k_now - self._last_kernel) + (u_now - self._last_user)
                    i_diff = i_now - self._last_idle
                    self._last_idle, self._last_kernel, self._last_user = i_now, k_now, u_now

                    if total > 0:
                        busy = max(0, total - i_diff)
                        cpu = min(100.0, max(1.0, (busy / total) * 100.0))

                self._last_cpu, self._last_ram = cpu, ram
                return round(cpu, 1), round(ram, 1)
            except Exception:
                pass

        return round(self._last_cpu, 1), round(self._last_ram, 1)

    def get_metrics(self) -> Dict[str, Any]:
        cpu, ram = self.get_cpu_and_ram()
        gpu, vram_used, vram_total, vram_pct = self.nvml.query()
        return {
            "cpu_percent": cpu,
            "ram_percent": ram,
            "gpu_percent": gpu,
            "vram_used_gb": vram_used,
            "vram_total_gb": vram_total,
            "vram_percent": vram_pct,
            "gpu_name": self.nvml.gpu_name,
        }


# ---------------------------------------------------------------------------
# Dynamic VRAM Guard
# ---------------------------------------------------------------------------
HEAVY_DCC_PROCESSES = {
    "3dsmax.exe",
    "blender.exe",
    "unrealeditor.exe",
    "vray.exe",
    "vrayneostandalone.exe",
    "corona.exe",
    "coronabatch.exe",
    "photoshop.exe",
    "maya.exe",
    "c4d.exe",
    "houdini.exe",
    "substance3d.exe",
}


class DynamicVRAMGuard:
    """Monitors VRAM load and active heavy 3D DCC apps; automatically unloads Ollama."""

    def __init__(
        self,
        ollama_host: str = "http://127.0.0.1:11434",
        threshold_percent: float = 85.0,
        cooldown_seconds: float = 30.0,
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.threshold_percent = threshold_percent
        self.cooldown_seconds = cooldown_seconds
        self.last_unload_time = 0.0
        self.last_unload_reason = ""
        self.is_unloaded = False

    def is_heavy_dcc_running(self, active_proc_name: str = "") -> Tuple[bool, str]:
        if active_proc_name and active_proc_name.lower() in HEAVY_DCC_PROCESSES:
            return True, active_proc_name.lower()

        # Check full process list via psutil
        if _HAS_PSUTIL and psutil:
            try:
                for p in psutil.process_iter(["name"]):
                    name = p.info.get("name")
                    if name and name.lower() in HEAVY_DCC_PROCESSES:
                        return True, name.lower()
            except Exception:
                pass
            return False, ""

        # Win32 Toolhelp fallback
        if kernel32:
            try:
                h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
                if h_snap:
                    pe = PROCESSENTRY32()
                    pe.dwSize = sizeof(PROCESSENTRY32)
                    if kernel32.Process32First(h_snap, byref(pe)):
                        while True:
                            exe = pe.szExeFile.decode("utf-8", errors="ignore").lower()
                            if exe in HEAVY_DCC_PROCESSES:
                                kernel32.CloseHandle(h_snap)
                                return True, exe
                            if not kernel32.Process32Next(h_snap, byref(pe)):
                                break
                    kernel32.CloseHandle(h_snap)
            except Exception:
                pass

        return False, ""

    def unload_ollama(self, reason: str = "") -> bool:
        """Issues keep_alive: 0 to Ollama via /api/generate or /api/ps."""
        now = time.time()
        if now - self.last_unload_time < self.cooldown_seconds and self.is_unloaded:
            return False

        models_to_unload = set()
        # 1. Discover models in memory via /api/ps
        try:
            ps_url = f"{self.ollama_host}/api/ps"
            req = urllib.request.Request(ps_url, method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("models", []):
                    m_name = m.get("name") or m.get("model")
                    if m_name:
                        models_to_unload.add(m_name)
        except Exception:
            pass

        # Fallback default model if none discovered
        if not models_to_unload:
            models_to_unload.add("qwen2.5:7b")

        success = False
        for model in models_to_unload:
            try:
                gen_url = f"{self.ollama_host}/api/generate"
                payload = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
                req = urllib.request.Request(
                    gen_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    if resp.status == 200:
                        success = True
            except Exception as e:
                logger.debug(f"Failed to unload Ollama model {model}: {e}")

        if success:
            self.last_unload_time = now
            self.last_unload_reason = reason
            self.is_unloaded = True
            logger.info(f"Ollama VRAM unloaded successfully. Reason: {reason}")
        return success

    def check_and_guard(
        self, vram_percent: float, active_proc_name: str = ""
    ) -> Tuple[bool, str]:
        """Checks conditions and unloads Ollama if necessary."""
        if vram_percent >= self.threshold_percent:
            reason = f"VRAM saturation ({vram_percent}% >= {self.threshold_percent}%)"
            triggered = self.unload_ollama(reason)
            return triggered, reason

        is_dcc, dcc_name = self.is_heavy_dcc_running(active_proc_name)
        if is_dcc:
            reason = f"Heavy DCC rendering active ({dcc_name})"
            triggered = self.unload_ollama(reason)
            return triggered, reason

        if vram_percent < 70.0 and self.is_unloaded:
            # System has cooled down
            self.is_unloaded = False

        return False, ""


# ---------------------------------------------------------------------------
# Window Title Project Tracker & AFK Detector
# ---------------------------------------------------------------------------
DCC_APP_MAP = {
    "3dsmax.exe": "3ds Max",
    "blender.exe": "Blender",
    "unrealeditor.exe": "Unreal Engine",
    "photoshop.exe": "Photoshop",
    "maya.exe": "Maya",
    "c4d.exe": "Cinema 4D",
    "houdini.exe": "Houdini",
}


def parse_window_title(title: str, proc_name: str = "") -> Dict[str, Any]:
    """Extracts application name, project/document name, and DCC flag from window title."""
    title = (title or "").strip()
    proc_lower = (proc_name or "").lower()
    app_name = DCC_APP_MAP.get(proc_lower, "")
    is_dcc = proc_lower in DCC_APP_MAP
    project_name = ""

    if not title:
        return {
            "app_name": app_name or "Desktop",
            "window_title": "",
            "project_name": "",
            "is_dcc": is_dcc,
        }

    # 3ds Max
    if "3ds max" in title.lower() or proc_lower == "3dsmax.exe":
        app_name = "3ds Max"
        is_dcc = True
        m = re.search(r"^(.*?)\s*-\s*Autodesk 3ds Max", title, re.IGNORECASE) or re.search(
            r"Autodesk 3ds Max.*?-\s*\[(.*?)\]", title, re.IGNORECASE
        )
        if m:
            raw = m.group(1).strip()
            project_name = re.sub(r"\.max$", "", raw.split("\\")[-1].split("/")[-1], flags=re.IGNORECASE)
        else:
            project_name = title

    # Blender
    elif "blender" in title.lower() or proc_lower == "blender.exe":
        app_name = "Blender"
        is_dcc = True
        m = re.search(r"\[(.*?)\]", title)
        if m:
            raw = m.group(1).strip()
            project_name = re.sub(r"\.blend$", "", raw.split("\\")[-1].split("/")[-1], flags=re.IGNORECASE)
        else:
            m2 = re.search(r"Blender.*?\s*-\s*(.*?)$", title, re.IGNORECASE)
            if m2:
                project_name = m2.group(1).strip()
            else:
                project_name = title

    # Unreal Engine
    elif "unreal editor" in title.lower() or proc_lower == "unrealeditor.exe":
        app_name = "Unreal Engine"
        is_dcc = True
        m = re.search(r"^(.*?)\s*-\s*Unreal Editor", title, re.IGNORECASE)
        if m:
            project_name = m.group(1).strip()
        else:
            project_name = title

    # Photoshop
    elif "photoshop" in title.lower() or proc_lower == "photoshop.exe":
        app_name = "Photoshop"
        is_dcc = True
        m = re.search(r"^(.*?)\s*@.*-\s*Adobe Photoshop", title, re.IGNORECASE) or re.search(
            r"^(.*?)\s*-\s*Adobe Photoshop", title, re.IGNORECASE
        )
        if m:
            raw = m.group(1).strip()
            project_name = re.sub(r"\.psd[tb]?$", "", raw, flags=re.IGNORECASE)
        else:
            project_name = title

    # Visual Studio Code
    elif "visual studio code" in title.lower() or proc_lower == "code.exe":
        app_name = "VS Code"
        parts = [p.strip() for p in title.split(" - ")]
        if len(parts) >= 3:
            project_name = parts[-2]
        elif len(parts) >= 2:
            project_name = parts[0]
        else:
            project_name = title

    # Generic Window Formats (e.g. "DocName - AppName")
    else:
        parts = [p.strip() for p in title.split(" - ")]
        if len(parts) > 1:
            app_name = app_name or parts[-1]
            project_name = parts[0]
        else:
            app_name = app_name or "Application"
            project_name = title

    return {
        "app_name": app_name,
        "window_title": title,
        "project_name": project_name,
        "is_dcc": is_dcc,
    }


class WindowAndAFKTracker:
    """Inspects Win32 foreground window and tracks AFK idle duration."""

    def __init__(self, afk_threshold_seconds: float = 300.0):
        self.afk_threshold_seconds = afk_threshold_seconds
        self.current_window = {
            "app_name": "Desktop",
            "window_title": "",
            "project_name": "",
            "is_dcc": False,
            "pid": 0,
        }
        self.is_afk = False
        self.idle_seconds = 0.0

        # Project Time Tracking Persistence (per day)
        self.today_date = datetime.date.today().isoformat()
        self.app_time_today: Dict[str, float] = {}
        self.project_time_today: Dict[str, float] = {}
        self._last_tick_time = time.time()
        self._lock = threading.Lock()

    def get_afk_idle_seconds(self) -> float:
        """Returns seconds since last user input via Win32 GetLastInputInfo."""
        if user32 and kernel32:
            try:
                lii = LASTINPUTINFO()
                lii.cbSize = sizeof(LASTINPUTINFO)
                if user32.GetLastInputInfo(byref(lii)):
                    tick = kernel32.GetTickCount()
                    idle_ms = (tick - lii.dwTime) & 0xFFFFFFFF
                    return float(idle_ms / 1000.0)
            except Exception:
                pass
        return 0.0

    def get_active_window(self) -> Dict[str, Any]:
        """Queries Win32 foreground window and resolves executable name."""
        if not user32:
            return {
                "app_name": "Desktop",
                "window_title": "",
                "project_name": "",
                "is_dcc": False,
                "pid": 0,
            }

        try:
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return {
                    "app_name": "Desktop",
                    "window_title": "",
                    "project_name": "",
                    "is_dcc": False,
                    "pid": 0,
                }

            title_buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title_buf, 512)
            title = title_buf.value

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, byref(pid))
            proc_id = pid.value

            proc_name = ""
            if proc_id > 0:
                if _HAS_PSUTIL and psutil:
                    try:
                        proc_name = psutil.Process(proc_id).name()
                    except Exception:
                        pass
                if not proc_name and kernel32:
                    h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, proc_id)
                    if h_proc:
                        buf = ctypes.create_unicode_buffer(1024)
                        size = wintypes.DWORD(1024)
                        if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, byref(size)):
                            proc_name = buf.value.split("\\")[-1]
                        kernel32.CloseHandle(h_proc)

            parsed = parse_window_title(title, proc_name)
            parsed["pid"] = proc_id
            return parsed
        except Exception as e:
            logger.debug(f"Failed to inspect foreground window: {e}")
            return {
                "app_name": "Desktop",
                "window_title": "",
                "project_name": "",
                "is_dcc": False,
                "pid": 0,
            }

    def update(self) -> Tuple[Dict[str, Any], bool, float]:
        """Ticks window inspection and accumulates active work time."""
        now = time.time()
        dt = max(0.0, now - self._last_tick_time)
        self._last_tick_time = now

        today = datetime.date.today().isoformat()
        if today != self.today_date:
            with self._lock:
                self.today_date = today
                self.app_time_today.clear()
                self.project_time_today.clear()

        idle_sec = self.get_afk_idle_seconds()
        is_afk = idle_sec >= self.afk_threshold_seconds
        win_info = self.get_active_window()

        with self._lock:
            self.current_window = win_info
            self.is_afk = is_afk
            self.idle_seconds = round(idle_sec, 1)

            # Time Accumulator: Pauses when AFK!
            if not is_afk and dt > 0:
                app = win_info.get("app_name") or "Desktop"
                proj = win_info.get("project_name") or ""

                self.app_time_today[app] = self.app_time_today.get(app, 0.0) + dt
                if proj:
                    key = f"{app}: {proj}" if app != "Application" else proj
                    self.project_time_today[key] = self.project_time_today.get(key, 0.0) + dt

        return win_info, is_afk, idle_sec

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            app = self.current_window.get("app_name", "")
            proj = self.current_window.get("project_name", "")
            key = f"{app}: {proj}" if app and proj else proj
            return {
                "active_window": dict(self.current_window),
                "is_afk": self.is_afk,
                "idle_seconds": self.idle_seconds,
                "current_app_seconds_today": round(self.app_time_today.get(app, 0.0), 1),
                "current_project_seconds_today": round(self.project_time_today.get(key, 0.0), 1),
            }


# ---------------------------------------------------------------------------
# Unified Telemetry Service
# ---------------------------------------------------------------------------
class TelemetryService:
    """Unified background service running a 1.0s telemetry sampling thread."""

    def __init__(
        self,
        ollama_host: str = "http://127.0.0.1:11434",
        vram_threshold_pct: float = 85.0,
        afk_threshold_sec: float = 300.0,
    ):
        self.collector = HardwareTelemetryCollector()
        self.guard = DynamicVRAMGuard(
            ollama_host=ollama_host,
            threshold_percent=vram_threshold_pct,
        )
        self.tracker = WindowAndAFKTracker(afk_threshold_seconds=afk_threshold_sec)

        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Cached state snapshot
        self._snapshot: Dict[str, Any] = {
            "cpu_percent": 10.0,
            "ram_percent": 35.0,
            "gpu_percent": 0.0,
            "vram_used_gb": 0.0,
            "vram_total_gb": 0.0,
            "vram_percent": 0.0,
            "gpu_name": "N/A",
            "active_window": {
                "app_name": "Desktop",
                "window_title": "",
                "project_name": "",
                "is_dcc": False,
                "pid": 0,
            },
            "afk": {"is_afk": False, "idle_seconds": 0.0},
            "project_time": {
                "current_app_seconds": 0.0,
                "current_project_seconds": 0.0,
            },
            "vram_guard": {
                "guard_triggered": False,
                "reason": "",
                "last_unload_time": 0.0,
            },
        }

    def start(self):
        """Starts background 1.0s telemetry thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TelemetryServiceThread")
        self._thread.start()
        logger.info("TelemetryService background thread started.")

    def stop(self):
        """Stops background thread and shuts down NVML."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self.collector.nvml.shutdown()

    def _loop(self):
        while self._running:
            t0 = time.time()
            try:
                metrics = self.collector.get_metrics()
                win_info, is_afk, idle_sec = self.tracker.update()
                guard_triggered, guard_reason = self.guard.check_and_guard(
                    metrics["vram_percent"],
                    active_proc_name=win_info.get("app_name", ""),
                )
                summary = self.tracker.get_summary()

                with self._lock:
                    self._snapshot = {
                        "cpu_percent": metrics["cpu_percent"],
                        "ram_percent": metrics["ram_percent"],
                        "gpu_percent": metrics["gpu_percent"],
                        "vram_used_gb": metrics["vram_used_gb"],
                        "vram_total_gb": metrics["vram_total_gb"],
                        "vram_percent": metrics["vram_percent"],
                        "gpu_name": metrics["gpu_name"],
                        "active_window": win_info,
                        "afk": {"is_afk": is_afk, "idle_seconds": round(idle_sec, 1)},
                        "project_time": {
                            "current_app_seconds": summary["current_app_seconds_today"],
                            "current_project_seconds": summary["current_project_seconds_today"],
                        },
                        "vram_guard": {
                            "guard_triggered": guard_triggered,
                            "reason": guard_reason,
                            "last_unload_time": self.guard.last_unload_time,
                        },
                    }
            except Exception as e:
                logger.error(f"Error in TelemetryService loop: {e}", exc_info=True)

            elapsed = time.time() - t0
            time.sleep(max(0.1, 1.0 - elapsed))

    def get_snapshot(self) -> Dict[str, Any]:
        """Thread-safe snapshot getter for SSE and HTTP endpoints."""
        with self._lock:
            return dict(self._snapshot)


# ---------------------------------------------------------------------------
# Global Singleton & Public Interface Wrappers (PROJECT.md Compliant)
# ---------------------------------------------------------------------------
_TELEMETRY_INSTANCE: Optional[TelemetryService] = None
_INSTANCE_LOCK = threading.Lock()


def get_telemetry_service(
    ollama_host: str = "http://127.0.0.1:11434",
    vram_threshold_pct: float = 85.0,
    afk_threshold_sec: float = 300.0,
) -> TelemetryService:
    """Returns the process-wide TelemetryService singleton and starts its loop."""
    global _TELEMETRY_INSTANCE
    with _INSTANCE_LOCK:
        if _TELEMETRY_INSTANCE is None:
            _TELEMETRY_INSTANCE = TelemetryService(
                ollama_host=ollama_host,
                vram_threshold_pct=vram_threshold_pct,
                afk_threshold_sec=afk_threshold_sec,
            )
            _TELEMETRY_INSTANCE.start()
        return _TELEMETRY_INSTANCE


def get_hardware_telemetry() -> Dict[str, Any]:
    """Returns {"cpu_percent": float, "ram_percent": float, "gpu_percent": float,
               "vram_used_gb": float, "vram_total_gb": float, "vram_percent": float,
               "gpu_name": str}"""
    svc = get_telemetry_service()
    snap = svc.get_snapshot()
    return {
        "cpu_percent": snap["cpu_percent"],
        "ram_percent": snap["ram_percent"],
        "gpu_percent": snap["gpu_percent"],
        "vram_used_gb": snap["vram_used_gb"],
        "vram_total_gb": snap["vram_total_gb"],
        "vram_percent": snap["vram_percent"],
        "gpu_name": snap["gpu_name"],
    }


def get_active_window_info() -> Dict[str, Any]:
    """Returns {"app_name": str, "window_title": str, "project_name": str,
               "is_dcc": bool, "pid": int}"""
    svc = get_telemetry_service()
    return dict(svc.get_snapshot()["active_window"])


def get_afk_idle_seconds() -> float:
    """Returns seconds since last user keyboard or mouse input."""
    svc = get_telemetry_service()
    return float(svc.get_snapshot()["afk"]["idle_seconds"])


def check_vram_and_unload_ollama(threshold_percent: float = 85.0) -> bool:
    """Evaluates VRAM and running DCCs; triggers Ollama keep_alive: 0 if needed."""
    svc = get_telemetry_service()
    snap = svc.get_snapshot()
    triggered, _ = svc.guard.check_and_guard(
        snap["vram_percent"], active_proc_name=snap["active_window"].get("app_name", "")
    )
    return triggered
