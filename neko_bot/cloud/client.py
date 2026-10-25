"""
neko_bot.cloud.client forwarding stub.
"""

try:
    from neko_bot.neko_bot.cloud.client import CloudClient, CloudClientError, CloudConnectionError
except ImportError:
    from neko_bot.cloud.client import CloudClient, CloudClientError, CloudConnectionError  # type: ignore

__all__ = ["CloudClient", "CloudClientError", "CloudConnectionError"]
