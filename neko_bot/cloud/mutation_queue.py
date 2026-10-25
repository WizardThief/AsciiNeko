"""
neko_bot.cloud.mutation_queue forwarding stub.
"""

try:
    from neko_bot.neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso
except ImportError:
    from neko_bot.cloud.mutation_queue import OfflineMutationQueue, now_utc_iso  # type: ignore

__all__ = ["OfflineMutationQueue", "now_utc_iso"]
