"""
neko_bot.tracker - Universal Window-Title Time Tracker & AFK Detection Subsystem.
"""

from neko_bot.tracker.win32 import (
    WindowState,
    IWin32Provider,
    NativeWin32Provider,
    MockWin32Provider,
    get_win32_provider,
    set_win32_provider,
    reset_win32_provider,
    mock_win32_scope,
    calculate_idle_seconds,
)
from neko_bot.tracker.afk import (
    AFKStatus,
    TimeSlice,
    AFKManager,
)
from neko_bot.tracker.rules import (
    ClassificationResult,
    CustomProjectRule,
    IgnoredWindowRule,
    RulesEngine,
)
from neko_bot.tracker.logger import (
    TimeLogger,
    sanitize_hostname,
    sanitize_title,
)
from neko_bot.tracker.aggregator import (
    TimeEntry,
    AggregatedReport,
    TimeAggregator,
    format_duration,
    get_today_tracked_seconds,
    get_tracking_status_metrics,
)

__all__ = [
    "WindowState",
    "IWin32Provider",
    "NativeWin32Provider",
    "MockWin32Provider",
    "get_win32_provider",
    "set_win32_provider",
    "reset_win32_provider",
    "mock_win32_scope",
    "calculate_idle_seconds",
    "AFKStatus",
    "TimeSlice",
    "AFKManager",
    "ClassificationResult",
    "CustomProjectRule",
    "IgnoredWindowRule",
    "RulesEngine",
    "TimeLogger",
    "sanitize_hostname",
    "sanitize_title",
    "TimeEntry",
    "AggregatedReport",
    "TimeAggregator",
    "format_duration",
    "get_today_tracked_seconds",
    "get_tracking_status_metrics",
]
