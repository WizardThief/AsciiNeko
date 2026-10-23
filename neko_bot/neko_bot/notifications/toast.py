"""
neko_bot.notifications.toast - Native Windows Toast Dispatcher with Zero-Crash Fallbacks.

Features:
- Single-worker ThreadPoolExecutor for non-blocking asynchronous execution (< 1ms dispatch).
- PowerShell WinRT snippet with CREATE_NO_WINDOW (0x08000000) for native Windows 10/11 toasts.
- Safe XML creation using CreateTextNode to avoid XML injection or character corruption.
- 3-tier zero-crash fallback: WinRT Toast -> Console/logging alert -> In-memory history buffer.
- Hermetic MockToastDispatcher for unit tests and CI.
- Global singleton management: get_toast_dispatcher(), set_toast_dispatcher(), reset_toast_dispatcher(), mock_toast_scope().
"""

from abc import ABC, abstractmethod
import base64
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, Generator, List, Optional, Protocol, Tuple, Union
import uuid

logger = logging.getLogger("neko_bot.notifications.toast")

# Base PowerShell WinRT script template
POWERSHELL_WINRT_SCRIPT = """
param(
    [string]$Title,
    [string]$Message,
    [string]$AppId = "NekoBot"
)
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
    $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
    $textElements = $xml.GetElementsByTagName("text")
    $textElements.Item(0).AppendChild($xml.CreateTextNode($Title)) > $null
    $textElements.Item(1).AppendChild($xml.CreateTextNode($Message)) > $null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""


@dataclass
class ToastRecord:
    """Audit record of a dispatched notification."""
    id: str
    title: str
    message: str
    level: str
    category: str
    status: str  # "delivered_winrt", "delivered_fallback", "failed", "mocked"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "level": self.level,
            "category": self.category,
            "status": self.status,
            "timestamp": self.timestamp,
            "error": self.error,
        }


class IToastDispatcher(Protocol):
    """Protocol defining the public interface for toast dispatchers."""

    def show(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        timeout: float = 4.0,
    ) -> bool:
        """Synchronously dispatch toast (blocks up to timeout)."""
        ...

    def show_async(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        on_complete: Optional[Callable[[bool, Optional[Exception]], None]] = None,
    ) -> Future:
        """Asynchronously dispatch toast without blocking the caller."""
        ...

    def get_history(self) -> List[ToastRecord]:
        """Returns delivery history."""
        ...

    def clear_history(self) -> None:
        """Clears delivery history."""
        ...


def escape_powershell_xml_text(text: str) -> str:
    """
    Escapes XML entities and special characters for PowerShell script generation.

    DEPRECATED for WinRT DOM operations: Do NOT use with WinRT CreateTextNode(),
    which automatically handles XML escaping upon DOM serialization. Pre-escaping
    causes double-escaping (&quot;, &amp;) in Windows Toast notifications.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


class NativeToastDispatcher:
    """
    Production Toast Dispatcher using PowerShell WinRT snippet with non-blocking execution
    and a 3-tier zero-crash fallback hierarchy.
    """

    def __init__(self, app_id: str = "NekoBot", max_history: int = 50, mock_mode: bool = False) -> None:
        self.app_id = app_id
        self.max_history = max_history
        self.mock_mode = mock_mode
        self.last_fallback_level: Optional[str] = None
        self._history: List[ToastRecord] = []
        self._mock_dispatched: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="neko_toast")
        self._encoded_script = base64.b64encode(POWERSHELL_WINRT_SCRIPT.encode("utf-16le")).decode("ascii")

    def enable_mock_mode(self, enabled: bool = True) -> None:
        """Enables or disables in-memory mock mode."""
        self.mock_mode = enabled

    def get_mock_dispatched(self) -> List[Dict[str, Any]]:
        """Returns all mock-dispatched toasts."""
        with self._lock:
            return list(self._mock_dispatched)

    def _build_full_powershell_script(self, title: str, message: str, app_id: Optional[str] = None) -> str:
        """
        Builds self-contained WinRT Toast PowerShell script block.
        Parameters are encoded as Base64 UTF-8 and decoded in PowerShell via
        [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("..."))
        and passed directly into $xml.CreateTextNode without manual XML entity escaping.
        """
        target_app = app_id or self.app_id
        title_b64 = base64.b64encode(title.encode("utf-8")).decode("ascii")
        msg_b64 = base64.b64encode(message.encode("utf-8")).decode("ascii")
        app_b64 = base64.b64encode(target_app.encode("utf-8")).decode("ascii")

        return (
            f'$Title = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{title_b64}"))\n'
            f'$Message = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{msg_b64}"))\n'
            f'$AppId = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{app_b64}"))\n'
            f'try {{\n'
            f'    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null\n'
            f'    $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02\n'
            f'    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)\n'
            f'    $textElements = $xml.GetElementsByTagName("text")\n'
            f'    $textElements.Item(0).AppendChild($xml.CreateTextNode($Title)) > $null\n'
            f'    $textElements.Item(1).AppendChild($xml.CreateTextNode($Message)) > $null\n'
            f'    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n'
            f'    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)\n'
            f'    exit 0\n'
            f'}} catch {{\n'
            f'    [Console]::Error.WriteLine($_.Exception.Message)\n'
            f'    exit 1\n'
            f'}}\n'
        )

    def build_powershell_script(self, title: str, message: str, app_id: Optional[str] = None) -> str:
        """
        Builds raw PowerShell script for inspection and testing.
        Uses Base64 UTF-8 parameter decoding and WinRT CreateTextNode without double-escaping.
        Retained for backward compatibility.
        """
        return self._build_full_powershell_script(title, message, app_id=app_id)

    def build_powershell_command(
        self,
        title: str,
        message: str,
        app_id: Optional[str] = None,
    ) -> List[str]:
        """
        Constructs the complete powershell.exe CLI argument list using -EncodedCommand
        with Base64 UTF-16LE encoding and strictly NO trailing script arguments.
        """
        full_script = self._build_full_powershell_script(title, message, app_id=app_id)
        encoded_cmd = base64.b64encode(full_script.encode("utf-16le")).decode("ascii")
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_cmd,
        ]

    def _record_delivery(self, record: ToastRecord) -> None:
        with self._lock:
            self._history.append(record)
            if len(self._history) > self.max_history:
                self._history.pop(0)

    def _execute_winrt_powershell(self, title: str, message: str, timeout: float = 4.0) -> bool:
        """
        Executes WinRT toast notification via powershell.exe -EncodedCommand.
        Guarantees CREATE_NO_WINDOW (0x08000000) on Windows and strictly NO trailing arguments.
        """
        if sys.platform != "win32":
            return False

        cmd = self.build_powershell_command(title, message, app_id=self.app_id)

        # CREATE_NO_WINDOW = 0x08000000 on Windows
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creationflags,
        )
        if res.returncode != 0 and res.stderr:
            logger.debug("WinRT PowerShell toast failed (code %d): %s", res.returncode, res.stderr.strip())
        return res.returncode == 0

    def show(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        timeout: float = 4.0,
    ) -> bool:
        """
        Dispatches toast notification through 3-tier fallback hierarchy.
        Returns True if delivered (via WinRT, mock, or logger fallback).
        """
        toast_id = uuid.uuid4().hex[:8]

        if self.mock_mode:
            with self._lock:
                rec = ToastRecord(
                    id=toast_id, title=title, message=message, level=level,
                    category=category, status="mocked"
                )
                self._mock_dispatched.append(rec.to_dict())
                self._record_delivery(rec)
            return True

        # Tier 1: Native WinRT via PowerShell
        success = False
        error_msg = None
        try:
            success = self._execute_winrt_powershell(title, message, timeout=timeout)
        except Exception as e:
            error_msg = str(e)
            success = False

        if success:
            self.last_fallback_level = "winrt"
            self._record_delivery(ToastRecord(
                id=toast_id, title=title, message=message, level=level,
                category=category, status="delivered_winrt"
            ))
            return True

        # Tier 2: Console / Logger Fallback
        try:
            self.last_fallback_level = "logger"
            log_msg = f"[TOAST:{level.upper()}] [{category.upper()}] {title} — {message}"
            if level.lower() in ("alert", "error", "critical"):
                logger.error(log_msg)
            elif level.lower() in ("warning", "warn"):
                logger.warning(log_msg)
            else:
                logger.info(log_msg)

            self._record_delivery(ToastRecord(
                id=toast_id, title=title, message=message, level=level,
                category=category, status="delivered_fallback", error=error_msg
            ))
            return True
        except Exception as fallback_err:
            # Tier 3: In-Memory Dead-Letter Buffer
            self.last_fallback_level = "memory_buffer"
            self._record_delivery(ToastRecord(
                id=toast_id, title=title, message=message, level=level,
                category=category, status="failed", error=str(fallback_err)
            ))
            return False

    def show_async(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        on_complete: Optional[Callable[[bool, Optional[Exception]], None]] = None,
    ) -> Future:
        """Asynchronously dispatches toast via single-worker pool, returning immediately."""
        def _task() -> bool:
            try:
                ok = self.show(title, message, level=level, category=category)
                if on_complete:
                    on_complete(ok, None)
                return ok
            except Exception as ex:
                if on_complete:
                    on_complete(False, ex)
                return False

        return self._executor.submit(_task)

    def send_toast(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        async_dispatch: bool = True,
        timeout: float = 4.0,
    ) -> bool:
        """Convenience method supporting both synchronous and asynchronous calls."""
        if async_dispatch:
            self.show_async(title, message, level=level, category=category)
            return True
        return self.show(title, message, level=level, category=category, timeout=timeout)

    def get_history(self) -> List[ToastRecord]:
        with self._lock:
            return list(self._history)

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
            self._mock_dispatched.clear()

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


class MockToastDispatcher:
    """Hermetic in-memory toast dispatcher for unit tests and CI."""

    def __init__(self, should_fail: bool = False, delay: float = 0.0) -> None:
        self.should_fail = should_fail
        self.delay = delay
        self.notifications: List[Dict[str, Any]] = []
        self.calls: List[Tuple[str, str, str, str]] = []
        self.last_fallback_level: Optional[str] = "mock"
        self._lock = threading.Lock()

    def _build_full_powershell_script(self, title: str, message: str, app_id: Optional[str] = None) -> str:
        """Builds self-contained WinRT Toast PowerShell script block with Base64 UTF-8 parameters."""
        target_app = app_id or "NekoBot"
        title_b64 = base64.b64encode(title.encode("utf-8")).decode("ascii")
        msg_b64 = base64.b64encode(message.encode("utf-8")).decode("ascii")
        app_b64 = base64.b64encode(target_app.encode("utf-8")).decode("ascii")
        return (
            f'$Title = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{title_b64}"))\n'
            f'$Message = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{msg_b64}"))\n'
            f'$AppId = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("{app_b64}"))\n'
            f'try {{\n'
            f'    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null\n'
            f'    $template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02\n'
            f'    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)\n'
            f'    $textElements = $xml.GetElementsByTagName("text")\n'
            f'    $textElements.Item(0).AppendChild($xml.CreateTextNode($Title)) > $null\n'
            f'    $textElements.Item(1).AppendChild($xml.CreateTextNode($Message)) > $null\n'
            f'    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n'
            f'    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)\n'
            f'    exit 0\n'
            f'}} catch {{\n'
            f'    [Console]::Error.WriteLine($_.Exception.Message)\n'
            f'    exit 1\n'
            f'}}\n'
        )

    def build_powershell_script(self, title: str, message: str, app_id: Optional[str] = None) -> str:
        return self._build_full_powershell_script(title, message, app_id=app_id)

    def enable_mock_mode(self, enabled: bool = True) -> None:
        pass

    def get_mock_dispatched(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.notifications)

    def show(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        timeout: float = 4.0,
    ) -> bool:
        if self.delay > 0:
            time.sleep(self.delay)
        with self._lock:
            record = {
                "id": uuid.uuid4().hex[:8],
                "title": title,
                "message": message,
                "level": level,
                "category": category,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "failed" if self.should_fail else "mocked",
            }
            self.notifications.append(record)
            self.calls.append((title, message, level, category))
        return not self.should_fail

    def show_async(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        on_complete: Optional[Callable[[bool, Optional[Exception]], None]] = None,
    ) -> Future:
        fut: Future = Future()
        ok = self.show(title, message, level=level, category=category)
        fut.set_result(ok)
        if on_complete:
            on_complete(ok, None if ok else RuntimeError("Mock toast delivery failed"))
        return fut

    def send_toast(
        self,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        async_dispatch: bool = True,
        timeout: float = 4.0,
    ) -> bool:
        if async_dispatch:
            self.show_async(title, message, level=level, category=category)
            return True
        return self.show(title, message, level=level, category=category, timeout=timeout)

    def get_history(self) -> List[ToastRecord]:
        with self._lock:
            return [
                ToastRecord(
                    id=n["id"], title=n["title"], message=n["message"],
                    level=n["level"], category=n["category"], status=n["status"],
                    timestamp=n["timestamp"]
                )
                for n in self.notifications
            ]

    def clear_history(self) -> None:
        with self._lock:
            self.notifications.clear()
            self.calls.clear()

    @property
    def last_toast(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.notifications[-1] if self.notifications else None


ToastDispatcher = NativeToastDispatcher

_global_toast_dispatcher: Optional[IToastDispatcher] = None


def get_toast_dispatcher() -> IToastDispatcher:
    """Returns active global toast dispatcher singleton."""
    global _global_toast_dispatcher
    if _global_toast_dispatcher is None:
        if sys.platform == "win32":
            _global_toast_dispatcher = NativeToastDispatcher()
        else:
            _global_toast_dispatcher = MockToastDispatcher()
    return _global_toast_dispatcher


def set_toast_dispatcher(dispatcher: IToastDispatcher) -> None:
    """Overrides the global toast dispatcher."""
    global _global_toast_dispatcher
    _global_toast_dispatcher = dispatcher


def reset_toast_dispatcher() -> None:
    """Resets the global toast dispatcher to default."""
    global _global_toast_dispatcher
    _global_toast_dispatcher = None


@contextmanager
def mock_toast_scope(mock: Optional[MockToastDispatcher] = None) -> Generator[MockToastDispatcher, None, None]:
    """Context manager overriding toast dispatcher with a mock during tests."""
    target = mock or MockToastDispatcher()
    prev = get_toast_dispatcher()
    set_toast_dispatcher(target)
    try:
        yield target
    finally:
        set_toast_dispatcher(prev)
