"""
neko_bot.cloud.client - Zero-Dependency HTTP/REST Client for Cloud Server Sync.

Uses standard library urllib.request for fast startup (< 15ms) and zero external dependencies.
Supports:
- Dual-mode authentication (Bearer token headers + request body token fallback).
- Health inspection, workstation registration, sync status, push, pull, reconcile.
- Clean offline / network failure detection.
"""

import json
import logging
import socket
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("neko_bot.cloud.client")


class CloudClientError(Exception):
    """Base exception for cloud client errors."""
    pass


class CloudConnectionError(CloudClientError):
    """Raised when cloud server is unreachable or offline."""
    pass


class CloudClient:
    """
    HTTP REST Client communicating with CloudServer (:8767).
    """

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8767",
        device_id: str = "workstation_local",
        token: str = "default_token",
        timeout: float = 3.0,
    ):
        self.server_url = server_url.rstrip("/")
        self.device_id = device_id
        self.token = token
        self.timeout = timeout

    def _make_request(
        self,
        endpoint: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Sends an HTTP request with dual authentication headers and json body."""
        url = f"{self.server_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "neko_bot-sync/2.0.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.device_id:
            headers["X-Device-Id"] = self.device_id

        data_bytes = None
        if payload is not None:
            # Guarantee compatibility with servers reading token from body
            body_dict = dict(payload)
            if "device_id" not in body_dict and self.device_id:
                body_dict["device_id"] = self.device_id
            if "token" not in body_dict and self.token:
                body_dict["token"] = self.token
            data_bytes = json.dumps(body_dict).encode("utf-8")

        req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_bytes = resp.read()
                if not resp_bytes:
                    return {}
                return json.loads(resp_bytes.decode("utf-8"))
        except (urllib.error.URLError, socket.timeout, ConnectionRefusedError, OSError) as e:
            logger.debug("Cloud connection error for %s: %s", url, e)
            raise CloudConnectionError(str(e)) from e

    def health(self) -> Dict[str, Any]:
        """GET /api/v1/health"""
        return self._make_request("/api/v1/health", method="GET")

    def register(
        self,
        device_id: str,
        device_name: str = "workstation",
        client_version: str = "2.0.0",
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v1/auth/register"""
        payload = {
            "device_id": device_id,
            "device_name": device_name,
            "client_version": client_version,
            "token": token or self.token,
        }
        return self._make_request("/api/v1/auth/register", method="POST", payload=payload)

    def status(self) -> Dict[str, Any]:
        """GET /api/v1/sync/status"""
        return self._make_request("/api/v1/sync/status", method="GET")

    def push(self, mutations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """POST /api/v1/sync/push"""
        payload = {"mutations": mutations}
        return self._make_request("/api/v1/sync/push", method="POST", payload=payload)

    def pull(self, since_sequence: int = 0, exclude_device: Optional[str] = None) -> Dict[str, Any]:
        """POST /api/v1/sync/pull"""
        payload = {
            "since_sequence": since_sequence,
            "device_id": exclude_device or self.device_id,
        }
        return self._make_request("/api/v1/sync/pull", method="POST", payload=payload)

    def reconcile(self, mutations: List[Dict[str, Any]], since_sequence: int = 0) -> Dict[str, Any]:
        """POST /api/v1/sync/reconcile (Atomic Bidirectional Exchange)"""
        payload = {
            "since_sequence": since_sequence,
            "mutations": mutations,
            "device_id": self.device_id,
        }
        return self._make_request("/api/v1/sync/reconcile", method="POST", payload=payload)
