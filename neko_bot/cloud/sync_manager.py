"""
neko_bot.cloud.sync_manager forwarding stub.
"""

try:
    from neko_bot.neko_bot.cloud.sync_manager import CloudSyncManager
except ImportError:
    from neko_bot.cloud.sync_manager import CloudSyncManager  # type: ignore

__all__ = ["CloudSyncManager"]
