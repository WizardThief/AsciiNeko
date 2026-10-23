"""
neko_bot.tracker.win32 - 64-bit safe Win32 API ctypes wrappers & mockable window inspection.

Provides low-level interfaces to query the current foreground window, process metadata,
and system-wide user idle time via GetLastInputInfo, with seamless fallbacks for
non-Windows platforms, unit tests, and elevated processes.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
import sys
import time
from typing import Generator, Optional, Protocol, Tuple, Union

logger = logging.getLogger("neko_bot.tracker.win32")

# Windows ctypes imports
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
else:
    ctypes = None
    wintypes = None


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH = 1024


if sys.platform == "win32" and ctypes:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("dwTime", wintypes.DWORD),
        ]

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND

    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int

    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        wintypes.LPDWORD,
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL

    kernel32.GetTickCount.argtypes = []
    kernel32.GetTickCount.restype = wintypes.DWORD


def calculate_idle_seconds(current_tick: int, last_input_tick: int) -> float:
    """
    Calculates idle seconds from tick counts with unsigned 32-bit rollover safety (49.7 days).
    """
    elapsed_ms = (int(current_tick) - int(last_input_tick)) & 0xFFFFFFFF
    return float(elapsed_ms) / 1000.0


@dataclass
class WindowState:
    """Represents an instantaneous snapshot of the Windows foreground window."""
    hwnd: int
    title: str
    pid: int
    exe_name: str
    exe_path: str
    timestamp: float = 0.0
    idle_seconds: float = 0.0


class IWin32Provider(Protocol):
    """Protocol defining the interface for Win32 window and idle queries."""

    def get_foreground_window(self) -> int: ...
    def get_window_title(self, hwnd: int) -> str: ...
    def get_window_pid(self, hwnd: int) -> int: ...
    def get_process_info(self, pid: int) -> Tuple[str, str]: ...
    def get_foreground_window_state(self) -> Optional[WindowState]: ...
    def get_idle_seconds(self) -> float: ...


class NativeWin32Provider:
    """Production provider calling Windows API via 64-bit safe ctypes with metadata caching."""

    def __init__(self) -> None:
        self._last_hwnd: int = 0
        self._cached_pid: int = 0
        self._cached_exe_name: str = ""
        self._cached_exe_path: str = ""

    def get_foreground_window(self) -> int:
        if not (sys.platform == "win32" and ctypes):
            return 0
        try:
            hwnd = user32.GetForegroundWindow()
            return int(hwnd) if hwnd else 0
        except Exception as e:
            logger.debug("GetForegroundWindow error: %s", e)
            return 0

    def get_window_title(self, hwnd: int) -> str:
        if not (sys.platform == "win32" and ctypes) or not hwnd:
            return ""
        try:
            length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
            buf_size = length + 1 if length > 0 else 512
            buf = ctypes.create_unicode_buffer(buf_size)
            copied = user32.GetWindowTextW(wintypes.HWND(hwnd), buf, buf_size)
            return buf.value[:copied] if copied > 0 else ""
        except Exception as e:
            logger.debug("GetWindowTextW error for hwnd %s: %s", hwnd, e)
            return ""

    def get_window_pid(self, hwnd: int) -> int:
        if not (sys.platform == "win32" and ctypes) or not hwnd:
            return 0
        try:
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            return int(pid.value)
        except Exception as e:
            logger.debug("GetWindowThreadProcessId error for hwnd %s: %s", hwnd, e)
            return 0

    def get_process_info(self, pid: int) -> Tuple[str, str]:
        if not pid or pid <= 0:
            return "", ""

        # 1. Native Win32 QueryFullProcessImageNameW with PROCESS_QUERY_LIMITED_INFORMATION
        if sys.platform == "win32" and ctypes:
            h_process = None
            try:
                h_process = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION,
                    False,
                    wintypes.DWORD(pid),
                )
                if h_process and h_process != wintypes.HANDLE(-1).value:
                    size = wintypes.DWORD(MAX_PATH)
                    buf = ctypes.create_unicode_buffer(size.value)
                    if kernel32.QueryFullProcessImageNameW(
                        h_process,
                        wintypes.DWORD(0),
                        buf,
                        ctypes.byref(size),
                    ):
                        exe_path = buf.value
                        exe_name = os.path.basename(exe_path)
                        return exe_path, exe_name
            except Exception as e:
                logger.debug("QueryFullProcessImageNameW failed for pid %d: %s", pid, e)
            finally:
                if h_process and h_process != wintypes.HANDLE(-1).value:
                    try:
                        kernel32.CloseHandle(h_process)
                    except Exception:
                        pass

        # 2. Secondary fallback via psutil
        try:
            import psutil
            proc = psutil.Process(pid)
            name = proc.name()
            try:
                exe_path = proc.exe()
            except Exception:
                exe_path = ""
            return exe_path, name
        except Exception as e:
            logger.debug("psutil fallback failed for pid %d: %s", pid, e)

        # 3. Tertiary fallback
        return "", f"pid_{pid}.exe"

    def get_foreground_window_state(self) -> Optional[WindowState]:
        hwnd = self.get_foreground_window()
        if not hwnd:
            return None

        title = self.get_window_title(hwnd)

        # Fast-path cache if hwnd has not changed
        if hwnd == self._last_hwnd and self._cached_pid:
            pid = self._cached_pid
            exe_name = self._cached_exe_name
            exe_path = self._cached_exe_path
        else:
            pid = self.get_window_pid(hwnd)
            exe_path, exe_name = self.get_process_info(pid)
            self._last_hwnd = hwnd
            self._cached_pid = pid
            self._cached_exe_name = exe_name
            self._cached_exe_path = exe_path

        idle_sec = self.get_idle_seconds()
        return WindowState(
            hwnd=hwnd,
            title=title,
            pid=pid,
            exe_name=exe_name,
            exe_path=exe_path,
            timestamp=time.time(),
            idle_seconds=idle_sec,
        )

    def get_idle_seconds(self) -> float:
        if not (sys.platform == "win32" and ctypes):
            return 0.0
        try:
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if not user32.GetLastInputInfo(ctypes.byref(lii)):
                return 0.0
            current_tick = kernel32.GetTickCount()
            return calculate_idle_seconds(current_tick, lii.dwTime)
        except Exception as e:
            logger.debug("GetLastInputInfo error: %s", e)
            return 0.0


class MockWin32Provider:
    """Deterministic in-memory provider for unit tests and non-Windows execution."""

    def __init__(self) -> None:
        self.hwnd: int = 1001
        self.title: str = "CyberpunkCity - Unreal Editor"
        self.pid: int = 5432
        self.exe_name: str = "UnrealEditor.exe"
        self.exe_path: str = "C:\\Program Files\\Epic Games\\UE_5.4\\Engine\\Binaries\\Win64\\UnrealEditor.exe"
        self.idle_seconds: float = 0.0

    def set_foreground_window(
        self,
        hwnd: int = 1001,
        title: str = "CyberpunkCity - Unreal Editor",
        exe_name: str = "UnrealEditor.exe",
        exe_path: str = "",
        pid: int = 5432,
        idle_seconds: float = 0.0,
    ) -> None:
        """Sets the mocked foreground window state (supports positional and keyword arguments)."""
        self.hwnd = int(hwnd)
        self.title = str(title)
        self.exe_name = str(exe_name)
        self.exe_path = str(exe_path) if exe_path else f"C:\\Program Files\\{exe_name}"
        self.pid = int(pid)
        self.idle_seconds = float(idle_seconds)

    def set_state(
        self,
        title: Optional[str] = None,
        exe_name: Optional[str] = None,
        exe_path: Optional[str] = None,
        pid: Optional[int] = None,
        hwnd: Optional[int] = None,
        idle_seconds: Optional[float] = None,
    ) -> None:
        """Selectively updates mocked state fields."""
        if title is not None:
            self.title = title
        if exe_name is not None:
            self.exe_name = exe_name
        if exe_path is not None:
            self.exe_path = exe_path
        if pid is not None:
            self.pid = pid
        if hwnd is not None:
            self.hwnd = hwnd
        if idle_seconds is not None:
            self.idle_seconds = float(idle_seconds)

    def set_idle(self, seconds: float) -> None:
        """Sets the mocked user idle seconds."""
        self.idle_seconds = float(seconds)

    def get_foreground_window(self) -> int:
        return self.hwnd

    def get_window_title(self, hwnd: int) -> str:
        return self.title if hwnd == self.hwnd else ""

    def get_window_pid(self, hwnd: int) -> int:
        return self.pid if hwnd == self.hwnd else 0

    def get_process_info(self, pid: int) -> Tuple[str, str]:
        return (self.exe_path, self.exe_name) if pid == self.pid else ("", "unknown.exe")

    def get_foreground_window_state(self) -> Optional[WindowState]:
        if not self.hwnd:
            return None
        return WindowState(
            hwnd=self.hwnd,
            title=self.title,
            pid=self.pid,
            exe_name=self.exe_name,
            exe_path=self.exe_path,
            timestamp=time.time(),
            idle_seconds=self.idle_seconds,
        )

    def get_idle_seconds(self) -> float:
        return self.idle_seconds


_global_provider: Optional[IWin32Provider] = None


def get_win32_provider() -> IWin32Provider:
    """Returns the active global Win32 provider instance (native on Windows, mock on non-Windows)."""
    global _global_provider
    if _global_provider is None:
        if sys.platform == "win32" and ctypes is not None:
            _global_provider = NativeWin32Provider()
        else:
            _global_provider = MockWin32Provider()
    return _global_provider


def set_win32_provider(provider: IWin32Provider) -> None:
    """Overrides the global Win32 provider (e.g. for testing)."""
    global _global_provider
    _global_provider = provider


def reset_win32_provider() -> None:
    """Resets the global Win32 provider back to default lazy initialization."""
    global _global_provider
    _global_provider = None


@contextmanager
def mock_win32_scope(mock_provider: Optional[MockWin32Provider] = None) -> Generator[MockWin32Provider, None, None]:
    """Context manager overriding Win32 provider with mock instance during tests."""
    provider = mock_provider or MockWin32Provider()
    prev = get_win32_provider()
    set_win32_provider(provider)
    try:
        yield provider
    finally:
        set_win32_provider(prev)
