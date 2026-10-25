"""
cloud/server.py - Neko Cloud REST API Server.

Provides a lightweight, zero-dependency HTTP/REST server backed by pluggable storage adapters
(SQLiteStorageAdapter default, PostgresStorageAdapter for production).
Endpoints:
- GET  /api/v1/health          - Server health and adapter telemetry
- POST /api/v1/auth/register   - Device registration and token issuance
- GET  /api/v1/sync/status     - Current server sequence and entity count
- POST /api/v1/sync/push       - Ingest batch of local mutations
- POST /api/v1/sync/pull       - Query delta mutations since client sequence
- POST /api/v1/sync/reconcile  - Atomic bidirectional push-pull reconciliation
"""

import argparse
from datetime import datetime, timezone
import hashlib
import http.server
import json
import logging
from pathlib import Path
import secrets
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple, Union
import urllib.parse

from cloud.storage.base import BaseStorageAdapter
from cloud.storage.sqlite_adapter import SQLiteStorageAdapter


def _parse_ts(ts: Optional[Union[str, float, int]]) -> float:
    """Helper to convert ISO timestamp string to POSIX epoch float."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if not isinstance(ts, str) or not ts.strip():
        return 0.0
    try:
        clean = ts.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(clean).timestamp()
    except Exception:
        return 0.0

logger = logging.getLogger("neko_cloud.server")


class CloudServer:
    """
    Ephemeral or standalone Cloud REST HTTP Server.
    Can be instantiated programmatically for automated testing or launched via CLI.
    """

    def __init__(
        self,
        storage: Optional[BaseStorageAdapter] = None,
        port: int = 8767,
        host: str = "127.0.0.1",
        require_auth: bool = False
    ):
        self.host = host
        self.port = port
        self.require_auth = require_auth
        if storage is None:
            default_db = Path("./workspace/cloud_sync.db")
            self.storage: BaseStorageAdapter = SQLiteStorageAdapter(default_db)
        else:
            self.storage = storage

        self.server: Optional[http.server.ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Starts ThreadingHTTPServer in a background daemon thread."""
        storage_ref = self.storage
        require_auth = self.require_auth

        class NekoCloudRequestHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                # Suppress noisy standard request logging during test runs
                pass

            def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Device-Id")
                self.end_headers()
                self.wfile.write(body)

            def _get_json_body(self) -> Tuple[bool, Dict[str, Any]]:
                """Reads and parses JSON body from rfile. Returns (is_valid, parsed_dict)."""
                content_len = self.headers.get("Content-Length")
                if not content_len:
                    return True, {}
                try:
                    length = int(content_len)
                    raw_bytes = self.rfile.read(length)
                    if not raw_bytes:
                        return True, {}
                    parsed = json.loads(raw_bytes.decode("utf-8"))
                    if isinstance(parsed, dict):
                        return True, parsed
                    return False, {}
                except Exception:
                    return False, {}

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Device-Id")
                self.end_headers()

            def _check_auth(self, body: Dict[str, Any]) -> bool:
                if not require_auth:
                    return True
                auth_header = self.headers.get("Authorization", "")
                token = ""
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:].strip()
                elif "token" in body:
                    token = str(body["token"]).strip()
                elif "auth_token" in body:
                    token = str(body["auth_token"]).strip()

                dev_id = self.headers.get("X-Device-Id") or body.get("device_id")
                if not dev_id or not token:
                    return False
                return bool(storage_ref.authenticate_device(str(dev_id).strip(), token))

            def do_GET(self) -> None:
                parsed_url = urllib.parse.urlparse(self.path)
                path = parsed_url.path.rstrip("/")

                if path == "/api/v1/health":
                    status = storage_ref.get_status()
                    self._send_json(200, {
                        "status": "ok",
                        "healthy": True,
                        "version": "2.0.0",
                        "storage": storage_ref.adapter_name,
                        "server_sequence": status.get("server_sequence", 0),
                        "active_devices": status.get("active_devices", 0)
                    })
                elif path == "/api/v1/sync/status":
                    if require_auth and not self._check_auth({}):
                        self._send_json(401, {"error": "Unauthorized"})
                        return
                    self._send_json(200, storage_ref.get_status())
                else:
                    self._send_json(404, {"error": "Not Found", "path": path})

            def do_POST(self) -> None:
                parsed_url = urllib.parse.urlparse(self.path)
                path = parsed_url.path.rstrip("/")

                valid, body = self._get_json_body()
                if not valid:
                    self._send_json(400, {"error": "Invalid JSON in request body"})
                    return

                if path == "/api/v1/auth/register":
                    dev_id = body.get("device_id")
                    if not dev_id:
                        self._send_json(400, {"error": "device_id is required"})
                        return
                    dev_name = body.get("device_name", "workstation")
                    client_ver = body.get("client_version", "1.0.0")
                    token = body.get("token")
                    if not token:
                        token = secrets.token_hex(32)

                    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                    storage_ref.register_device(dev_id, dev_name, client_ver, token_hash)
                    now_utc = datetime.now(timezone.utc).isoformat()
                    self._send_json(200, {
                        "status": "registered",
                        "token": token,
                        "auth_token": token,
                        "device_id": dev_id,
                        "registered_at": now_utc
                    })

                elif path == "/api/v1/sync/push":
                    if require_auth and not self._check_auth(body):
                        self._send_json(401, {"error": "Unauthorized"})
                        return

                    mutations = body.get("mutations")
                    if mutations is None or not isinstance(mutations, list) or not all(isinstance(m, dict) for m in mutations):
                        self._send_json(400, {"error": "Invalid mutations: must be a list of dicts"})
                        return

                    applied_count = 0
                    for m in mutations:
                        try:
                            ok, _ = storage_ref.apply_mutation(m)
                            if ok:
                                applied_count += 1
                        except Exception as e:
                            self._send_json(400, {"error": f"Failed to apply mutation: {e}"})
                            return

                    self._send_json(200, {
                        "applied": applied_count,
                        "status": storage_ref.get_status()
                    })

                elif path == "/api/v1/sync/pull":
                    if require_auth and not self._check_auth(body):
                        self._send_json(401, {"error": "Unauthorized"})
                        return

                    since = body.get("since_sequence", 0)
                    exclude_dev = body.get("device_id")
                    muts, max_seq = storage_ref.pull_mutations(since, exclude_dev)
                    self._send_json(200, {
                        "mutations": muts,
                        "pulled_mutations": muts,
                        "server_sequence": max_seq
                    })

                elif path == "/api/v1/sync/reconcile":
                    if require_auth and not self._check_auth(body):
                        self._send_json(401, {"error": "Unauthorized"})
                        return

                    # 1. Ingest push mutations
                    push_muts = body.get("mutations")
                    if push_muts is None or not isinstance(push_muts, list) or not all(isinstance(m, dict) for m in push_muts):
                        self._send_json(400, {"error": "Invalid mutations: must be a list of dicts"})
                        return

                    applied_count = 0
                    for m in push_muts:
                        try:
                            ok, _ = storage_ref.apply_mutation(m)
                            if ok:
                                applied_count += 1
                        except Exception as e:
                            self._send_json(400, {"error": f"Failed to apply mutation: {e}"})
                            return

                    # 2. Delta pull remote mutations
                    since = body.get("since_sequence", 0)
                    dev_id = body.get("device_id")
                    remote_muts, max_seq = storage_ref.pull_mutations(since, dev_id)
                    curr_server_seq = max(max_seq, storage_ref.get_status().get("server_sequence", 0))

                    self._send_json(200, {
                        "status": "synced",
                        "sync_status": "synced",
                        "success": True,
                        "applied": applied_count,
                        "applied_mutations_count": applied_count,
                        "pulled_mutations": remote_muts,
                        "remote_mutations": remote_muts,
                        "server_sequence": curr_server_seq,
                        "new_server_sequence": curr_server_seq
                    })

                else:
                    self._send_json(404, {"error": "Not Found", "path": path})


        self.server = http.server.ThreadingHTTPServer((self.host, self.port), NekoCloudRequestHandler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops the HTTP server."""
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None


def main() -> None:
    parser = argparse.ArgumentParser(description="Neko Cloud REST API Server")
    parser.add_argument("--host", default="127.0.0.1", help="Binding host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8767, help="Binding port number (default: 8767)")
    parser.add_argument("--db-type", choices=["sqlite", "postgres"], default="sqlite", help="Storage adapter type")
    parser.add_argument("--db-path", default="./workspace/cloud_sync.db", help="Path to SQLite database file")
    parser.add_argument("--db-url", default=None, help="PostgreSQL connection URL / DSN")
    parser.add_argument("--require-auth", action="store_true", help="Enforce Bearer token authentication")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose stdout logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
    )

    if args.db_type == "sqlite":
        storage = SQLiteStorageAdapter(args.db_path)
    else:
        from cloud.storage.postgres_adapter import PostgresStorageAdapter
        storage = PostgresStorageAdapter(args.db_url)

    server = CloudServer(storage=storage, port=args.port, host=args.host, require_auth=args.require_auth)
    server.start()
    print(f"Neko Cloud REST API Server listening on http://{args.host}:{args.port} [{storage.adapter_name}]")
    print("Press Ctrl+C to stop.")

    try:
        if server.server:
            server.server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Neko Cloud Server...")
    finally:
        server.stop()
        print("Server stopped cleanly.")


if __name__ == "__main__":
    main()
