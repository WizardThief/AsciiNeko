"""
bridge/mcp_registry.py
======================
Modular MCP Registry & Control Plane (Requirements R3 / Features F14–F17).
Provides:
- Built-in System Connectors (mcp_filesystem_scanner, mcp_calendar_sync, mcp_dcc_telemetry, mcp_vram_guard)
- External MCP Server Registry (mcpServers in mcp_config.json)
- Thread-safe in-memory state and persistent atomic storage
- Real-time operational effects and token estimation
- Singleton accessor get_mcp_registry(data_dir=None)
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AsciiNeko.MCPRegistry")

DEFAULT_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))

# 4 Default Built-in System Connectors (F14)
BUILTIN_CONNECTORS: List[Dict[str, Any]] = [
    {
        "id": "mcp_filesystem_scanner",
        "name": "Workspace & Asset Scanner",
        "description": "Scans project workspace folders, detects DCC/code/export assets, and provides live radar",
        "type": "builtin",
        "enabled": True,
        "estimated_tokens": 45,
    },
    {
        "id": "mcp_calendar_sync",
        "name": "Google Calendar .ics Sync",
        "description": "Bidirectional calendar sync and deadline management",
        "type": "builtin",
        "enabled": True,
        "estimated_tokens": 40,
    },
    {
        "id": "mcp_dcc_telemetry",
        "name": "Desktop & DCC Telemetry",
        "description": "Tracks active DCC windows and matches to Kanban tasks",
        "type": "builtin",
        "enabled": True,
        "estimated_tokens": 35,
    },
    {
        "id": "mcp_vram_guard",
        "name": "RTX 4090 VRAM Guard",
        "description": "Automatic Ollama model unload safeguard during heavy GPU/VRAM usage",
        "type": "builtin",
        "enabled": True,
        "estimated_tokens": 20,
    },
]


class MCPRegistry:
    """
    Manages built-in and external Model Context Protocol (MCP) connectors.
    Provides persistence via bridge/data/mcp_config.json and real-time state querying.
    """

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
        os.makedirs(self.data_dir, exist_ok=True)
        self.config_path = os.path.join(self.data_dir, "mcp_config.json")
        self._lock = threading.RLock()

        # In-memory connectors: id -> Dict[str, Any]
        self._connectors: Dict[str, Dict[str, Any]] = {}
        self._load_config()

    def _get_default_builtins(self) -> Dict[str, Dict[str, Any]]:
        """Returns fresh dictionary of default built-in connectors."""
        return {c["id"]: copy.deepcopy(c) for c in BUILTIN_CONNECTORS}

    def _load_config(self) -> None:
        """Loads configuration from mcp_config.json with safe fallbacks."""
        with self._lock:
            builtins_map = self._get_default_builtins()
            external_map: Dict[str, Dict[str, Any]] = {}

            if os.path.isfile(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict):
                        # Merge built-ins overrides if present
                        b_overrides = raw.get("builtins", {})
                        if isinstance(b_overrides, dict):
                            for bid, bcfg in b_overrides.items():
                                if bid in builtins_map and isinstance(bcfg, dict):
                                    if "enabled" in bcfg:
                                        builtins_map[bid]["enabled"] = bool(bcfg["enabled"])
                                    if "estimated_tokens" in bcfg:
                                        try:
                                            builtins_map[bid]["estimated_tokens"] = int(bcfg["estimated_tokens"])
                                        except (ValueError, TypeError):
                                            pass
                                    if "name" in bcfg:
                                        builtins_map[bid]["name"] = str(bcfg["name"])
                                    if "description" in bcfg:
                                        builtins_map[bid]["description"] = str(bcfg["description"])

                        # Load external servers from mcpServers block
                        mcp_servers = raw.get("mcpServers", {})
                        if isinstance(mcp_servers, dict):
                            for sid, scfg in mcp_servers.items():
                                if isinstance(scfg, dict):
                                    external_map[sid] = {
                                        "id": sid,
                                        "name": scfg.get("name") or sid,
                                        "description": scfg.get("description", ""),
                                        "command": scfg.get("command", ""),
                                        "args": scfg.get("args", []),
                                        "url": scfg.get("url", ""),
                                        "type": "external",
                                        "enabled": bool(scfg.get("enabled", True)),
                                        "estimated_tokens": int(scfg.get("estimated_tokens", 30)),
                                    }
                except Exception as ex:
                    logger.warning(f"Failed to read {self.config_path}, using defaults: {ex}")

            # Assemble merged connectors map
            self._connectors = dict(builtins_map)
            self._connectors.update(external_map)

    def _save_config(self) -> None:
        """Atomically saves current connector states to mcp_config.json."""
        with self._lock:
            builtins_out: Dict[str, Any] = {}
            mcp_servers_out: Dict[str, Any] = {}

            for cid, cdata in self._connectors.items():
                if cdata.get("type") == "builtin":
                    builtins_out[cid] = {
                        "enabled": bool(cdata.get("enabled", True)),
                        "estimated_tokens": int(cdata.get("estimated_tokens", 30)),
                        "name": cdata.get("name", cid),
                        "description": cdata.get("description", ""),
                    }
                else:
                    mcp_servers_out[cid] = {
                        "name": cdata.get("name", cid),
                        "description": cdata.get("description", ""),
                        "command": cdata.get("command", ""),
                        "args": cdata.get("args", []),
                        "url": cdata.get("url", ""),
                        "enabled": bool(cdata.get("enabled", True)),
                        "estimated_tokens": int(cdata.get("estimated_tokens", 30)),
                    }

            out_data = {
                "builtins": builtins_out,
                "mcpServers": mcp_servers_out,
            }

            tmp_path = os.path.join(self.data_dir, f"mcp_config_{uuid.uuid4().hex[:8]}.tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(out_data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.config_path)
            except Exception as ex:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                logger.error(f"Failed to save {self.config_path}: {ex}")
                raise

    def get_all_connectors(self) -> List[Dict[str, Any]]:
        """Returns list of all registered connectors (both built-in and external)."""
        with self._lock:
            # Re-check file if external server modified file directly
            if os.path.isfile(self.config_path):
                self._load_config()
            return [copy.deepcopy(c) for c in self._connectors.values()]

    def get_connector(self, connector_id: str) -> Optional[Dict[str, Any]]:
        """Returns connector dict for given ID, or None if not registered."""
        with self._lock:
            c = self._connectors.get(connector_id)
            return copy.deepcopy(c) if c else None

    def toggle_connector(self, connector_id: str, enabled: bool) -> bool:
        """
        Toggles connector state (True/False).
        Persists state to mcp_config.json.
        Returns True if connector exists and was updated, False otherwise.
        """
        with self._lock:
            if connector_id not in self._connectors:
                return False
            self._connectors[connector_id]["enabled"] = bool(enabled)
            self._save_config()
            return True

    def is_connector_enabled(self, connector_id: str) -> bool:
        """Returns True if connector exists and is enabled, False otherwise."""
        with self._lock:
            c = self._connectors.get(connector_id)
            if not c:
                return False
            return bool(c.get("enabled", True))

    def is_enabled(self, connector_id: str) -> bool:
        """Alias for is_connector_enabled."""
        return self.is_connector_enabled(connector_id)

    def get_total_mcp_tokens(self) -> int:
        """Calculates total estimated tokens for all currently enabled connectors."""
        with self._lock:
            total = 0
            for c in self._connectors.values():
                if c.get("enabled", True):
                    try:
                        total += int(c.get("estimated_tokens", 0))
                    except (ValueError, TypeError):
                        pass
            return total

    def add_external_server(self, server_id: str, server_config: Dict[str, Any]) -> bool:
        """
        Registers an external MCP server conforming to mcpServers schema.
        Persists to mcp_config.json.
        """
        if not server_id or not isinstance(server_id, str):
            return False
        sid = server_id.strip()
        if not sid or not isinstance(server_config, dict):
            return False

        with self._lock:
            connector_entry = {
                "id": sid,
                "name": server_config.get("name") or sid,
                "description": server_config.get("description", ""),
                "command": server_config.get("command", ""),
                "args": server_config.get("args", []),
                "url": server_config.get("url", ""),
                "type": "external",
                "enabled": bool(server_config.get("enabled", True)),
                "estimated_tokens": int(server_config.get("estimated_tokens", 30)),
            }
            self._connectors[sid] = connector_entry
            self._save_config()
            return True

    def add_connector(self, connector_dict: Dict[str, Any]) -> bool:
        """Convenience method to register a connector dictionary."""
        if not isinstance(connector_dict, dict):
            return False
        cid = connector_dict.get("id")
        if not cid:
            return False
        return self.add_external_server(str(cid), connector_dict)


# --- Global Singleton Registry Cache ---
_registry_cache: Dict[str, MCPRegistry] = {}
_cache_lock = threading.Lock()


def get_mcp_registry(data_dir: Optional[str] = None) -> MCPRegistry:
    """Returns singleton MCPRegistry instance for given data_dir."""
    target_dir = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
    with _cache_lock:
        if target_dir not in _registry_cache:
            _registry_cache[target_dir] = MCPRegistry(data_dir=target_dir)
        return _registry_cache[target_dir]
