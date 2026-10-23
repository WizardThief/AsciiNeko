"""
neko_bot.core.resource_monitor - Dual-engine process scanner and system load monitor.

Scans for heavy creative/production software (Unreal Engine, Blender, Photoshop,
3ds Max, After Effects, Premiere, DaVinci Resolve, V-Ray, Corona) and monitors
system CPU/RAM load to adapt AI router behavior and protect GPU/system resources.
"""

from dataclasses import dataclass
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("neko_bot.resource_monitor")

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
else:
    ctypes = None
    wintypes = None


DEFAULT_HEAVY_PROCESSES: List[str] = [
    "unrealeditor.exe",
    "blender.exe",
    "photoshop.exe",
    "3dsmax.exe",
    "afterfx.exe",
    "premiere.exe",
    "resolve.exe",
    "vray.exe",
    "corona.exe",
]


class ResourceMonitor:
    """Resource Monitor scanning Windows processes and evaluating system load."""

    _global_mock_apps: Optional[List[str]] = None
    _global_mock_cpu: Optional[float] = None
    _global_mock_ram: Optional[float] = None

    def __init__(
        self,
        heavy_processes: Optional[List[str]] = None,
        cpu_threshold: float = 80.0,
        ram_threshold: float = 85.0,
        cache_ttl: float = 1.0,
    ) -> None:
        raw_list = heavy_processes if heavy_processes is not None else DEFAULT_HEAVY_PROCESSES
        self.heavy_processes: Set[str] = {
            self._normalize_proc_name(p) for p in raw_list
        }
        self.cpu_threshold = float(cpu_threshold)
        self.ram_threshold = float(ram_threshold)
        self.cache_ttl = float(cache_ttl)

        self._last_scan: float = 0.0
        self._cached_apps: List[str] = []
        self._cached_cpu: float = 0.0
        self._cached_ram: float = 0.0

        # Mocking hooks for automated testing
        self._mock_apps: Optional[List[str]] = None
        self._mock_cpu: Optional[float] = None
        self._mock_ram: Optional[float] = None

        # Win32 fallback CPU state tracking
        self._prev_idle_time: Optional[int] = None
        self._prev_kernel_time: Optional[int] = None
        self._prev_user_time: Optional[int] = None

        # Baseline CPU initialization to avoid blocking calls later
        if HAS_PSUTIL:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass
        elif sys.platform == "win32":
            self._sample_win32_cpu()

    @staticmethod
    def _normalize_proc_name(name: str) -> str:
        s = name.strip().lower()
        if s.endswith(".exe"):
            s = s[:-4]
        return s

    def set_mock_override(
        self,
        detected_apps: Optional[List[str]] = None,
        cpu_percent: Optional[float] = None,
        ram_percent: Optional[float] = None,
    ) -> None:
        """Sets synthetic process and load states for hermetic test execution."""
        self._mock_apps = list(detected_apps) if detected_apps is not None else None
        self._mock_cpu = float(cpu_percent) if cpu_percent is not None else None
        self._mock_ram = float(ram_percent) if ram_percent is not None else None
        ResourceMonitor._global_mock_apps = self._mock_apps
        ResourceMonitor._global_mock_cpu = self._mock_cpu
        ResourceMonitor._global_mock_ram = self._mock_ram
        # Invalidate scan timestamp so next call uses the mock immediately
        self._last_scan = 0.0

    def clear_mock_override(self) -> None:
        """Clears all mock overrides."""
        self._mock_apps = None
        self._mock_cpu = None
        self._mock_ram = None
        ResourceMonitor._global_mock_apps = None
        ResourceMonitor._global_mock_cpu = None
        ResourceMonitor._global_mock_ram = None
        self._last_scan = 0.0

    def _refresh_if_needed(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_scan < self.cache_ttl):
            return

        # Handle active mock overrides (instance or global)
        mock_apps = self._mock_apps if self._mock_apps is not None else ResourceMonitor._global_mock_apps
        mock_cpu = self._mock_cpu if self._mock_cpu is not None else ResourceMonitor._global_mock_cpu
        mock_ram = self._mock_ram if self._mock_ram is not None else ResourceMonitor._global_mock_ram

        if mock_apps is not None or mock_cpu is not None or mock_ram is not None:
            self._cached_apps = list(mock_apps or [])
            self._cached_cpu = float(mock_cpu if mock_cpu is not None else 10.0)
            self._cached_ram = float(mock_ram if mock_ram is not None else 30.0)
            self._last_scan = now
            return

        # Real OS scanning
        self._cached_apps = self._scan_running_processes()
        self._cached_cpu, self._cached_ram = self._sample_cpu_and_ram()
        self._last_scan = now

    def _scan_running_processes(self) -> List[str]:
        """Dual-engine process scan: psutil primary, Win32 ctypes fallback."""
        detected: List[str] = []

        if HAS_PSUTIL:
            try:
                for proc in psutil.process_iter(["name"]):
                    try:
                        name = proc.info.get("name")
                        if name:
                            norm = self._normalize_proc_name(name)
                            if norm in self.heavy_processes:
                                detected.append(name)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
                return sorted(list(set(detected)))
            except Exception as e:
                logger.debug("psutil process scan failed: %s, falling back to Win32 ctypes", e)

        # Win32 Toolhelp snapshot fallback
        if sys.platform == "win32" and ctypes:
            try:
                detected = self._scan_processes_win32()
                return sorted(list(set(detected)))
            except Exception as e:
                logger.debug("Win32 ctypes process scan failed: %s", e)

        return detected

    def _scan_processes_win32(self) -> List[str]:
        """Scans running processes on Windows using CreateToolhelp32Snapshot."""
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
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
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        h_snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h_snapshot == -1 or h_snapshot == wintypes.HANDLE(-1).value:
            return []

        detected = []
        try:
            pe32 = PROCESSENTRY32W()
            pe32.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            success = ctypes.windll.kernel32.Process32FirstW(h_snapshot, ctypes.byref(pe32))
            while success:
                exe_name = str(pe32.szExeFile)
                if exe_name:
                    norm = self._normalize_proc_name(exe_name)
                    if norm in self.heavy_processes:
                        detected.append(exe_name)
                success = ctypes.windll.kernel32.Process32NextW(h_snapshot, ctypes.byref(pe32))
        finally:
            ctypes.windll.kernel32.CloseHandle(h_snapshot)

        return detected

    def _sample_cpu_and_ram(self) -> Tuple[float, float]:
        """Samples current CPU and RAM percentages."""
        cpu: float = 0.0
        ram: float = 0.0

        if HAS_PSUTIL:
            try:
                cpu = float(psutil.cpu_percent(interval=None))
                ram = float(psutil.virtual_memory().percent)
                return cpu, ram
            except Exception as e:
                logger.debug("psutil CPU/RAM sampling failed: %s", e)

        # Win32 fallback
        if sys.platform == "win32" and ctypes:
            try:
                ram = self._sample_win32_ram()
                cpu = self._sample_win32_cpu()
            except Exception as e:
                logger.debug("Win32 CPU/RAM sampling failed: %s", e)

        return cpu, ram

    def _sample_win32_ram(self) -> float:
        """Retrieves physical memory load percentage via GlobalMemoryStatusEx."""
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return float(stat.dwMemoryLoad)
        return 0.0

    def _sample_win32_cpu(self) -> float:
        """Computes CPU percentage via kernel32.GetSystemTimes."""
        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        def _to_int(ft: FILETIME) -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        idle_ft = FILETIME()
        kernel_ft = FILETIME()
        user_ft = FILETIME()

        if ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle_ft), ctypes.byref(kernel_ft), ctypes.byref(user_ft)
        ):
            idle = _to_int(idle_ft)
            kernel = _to_int(kernel_ft)
            user = _to_int(user_ft)

            if (
                self._prev_idle_time is not None
                and self._prev_kernel_time is not None
                and self._prev_user_time is not None
            ):
                idle_diff = idle - self._prev_idle_time
                kernel_diff = kernel - self._prev_kernel_time
                user_diff = user - self._prev_user_time
                total_sys = kernel_diff + user_diff

                self._prev_idle_time = idle
                self._prev_kernel_time = kernel
                self._prev_user_time = user

                if total_sys > 0:
                    pct = ((total_sys - idle_diff) / total_sys) * 100.0
                    return max(0.0, min(100.0, float(pct)))
                return 0.0
            else:
                self._prev_idle_time = idle
                self._prev_kernel_time = kernel
                self._prev_user_time = user
                return 0.0

        return 0.0

    def is_heavy_creative_running(self, force_refresh: bool = False) -> Tuple[bool, List[str]]:
        """Returns (is_heavy, list_of_detected_process_names)."""
        self._refresh_if_needed(force_refresh)
        return len(self._cached_apps) > 0, list(self._cached_apps)

    def get_system_load(self, force_refresh: bool = False) -> Tuple[float, float]:
        """Returns (cpu_percent, ram_percent)."""
        self._refresh_if_needed(force_refresh)
        return self._cached_cpu, self._cached_ram

    def get_resource_mode(self, force_refresh: bool = False) -> str:
        """Returns 'creative_detected', 'low_vram', or 'normal'."""
        self._refresh_if_needed(force_refresh)
        if self._cached_apps:
            return "creative_detected"
        if self._cached_cpu > self.cpu_threshold or self._cached_ram > self.ram_threshold:
            return "low_vram"
        return "normal"

    def get_status_dict(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Returns complete resource status dictionary."""
        self._refresh_if_needed(force_refresh)
        return {
            "resource_mode": self.get_resource_mode(),
            "is_heavy": len(self._cached_apps) > 0,
            "detected_apps": list(self._cached_apps),
            "cpu_percent": round(self._cached_cpu, 1),
            "ram_percent": round(self._cached_ram, 1),
        }
