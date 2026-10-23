"""
neko_bot.notifications - Dual Notification Pipeline (Windows Native Toast & Local JSON Queue).
"""

from neko_bot.notifications.toast import (
    IToastDispatcher,
    NativeToastDispatcher,
    MockToastDispatcher,
    ToastDispatcher,
    ToastRecord,
    get_toast_dispatcher,
    set_toast_dispatcher,
    reset_toast_dispatcher,
    mock_toast_scope,
    escape_powershell_xml_text,
)
from neko_bot.notifications.queue import (
    NotificationCategory,
    NotificationLevel,
    NotificationItem,
    NotificationQueue,
    now_utc_iso,
)

__all__ = [
    "IToastDispatcher",
    "NativeToastDispatcher",
    "MockToastDispatcher",
    "ToastDispatcher",
    "ToastRecord",
    "get_toast_dispatcher",
    "set_toast_dispatcher",
    "reset_toast_dispatcher",
    "mock_toast_scope",
    "escape_powershell_xml_text",
    "NotificationCategory",
    "NotificationLevel",
    "NotificationItem",
    "NotificationQueue",
    "now_utc_iso",
]
