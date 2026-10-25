"""
neko_bot.core.config - Central configuration management and directory path resolution.
"""

from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Optional
import uuid

logger = logging.getLogger("neko_bot.config")


def atomic_write_json_file(file_path: Path, data: Dict[str, Any], max_retries: int = 60) -> None:
    """Safely writes a JSON dictionary to file_path atomically via os.replace with retry for Windows locks."""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_name(
        f"{file_path.name}.tmp.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}_{uuid.uuid4().hex[:6]}"
    )
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    for attempt in range(max_retries):
        try:
            os.replace(tmp_path, file_path)
            return
        except (PermissionError, OSError):
            if attempt == max_retries - 1:
                # If final attempt fails, clean up temp file and re-raise
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except Exception:
                    pass
                raise
            time.sleep(0.005)


@dataclass
class Config:
    """Global configuration settings and canonical filesystem layout for neko_bot."""

    # Project directories
    project_root: Path
    workspace_dir: Path
    neko_dir: Path

    # IPC HTTP Server
    host: str = "127.0.0.1"
    port: int = 8766

    # Ollama & Dynamic Router
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.8:latest"
    ollama_lightweight_model: str = "clef:latest"
    ollama_timeout: float = 1.5

    # Universal Window Tracking & AFK Filter
    tracking_interval: float = 1.0
    afk_threshold: int = 180  # 3 minutes

    # Focus Timers & Break Reminders
    break_interval: int = 3300  # 55 minutes
    break_duration: int = 300   # 5 minutes

    # Multi-PC Conflict-Free Git Auto-Sync
    git_sync_interval: int = 300  # 5 minutes
    git_remote: str = "origin"
    git_branch: str = "main"

    # Cloud-First Ecosystem Sync (Milestone 4)
    cloud_url: str = "http://127.0.0.1:8767"
    cloud_sync_interval: int = 15  # seconds between daemon background sync ticks
    cloud_device_id: Optional[str] = None
    cloud_device_name: Optional[str] = None
    cloud_auth_token: Optional[str] = None

    # Logging
    log_level: str = "INFO"

    @property
    def pid_file(self) -> Path:
        return self.neko_dir / "daemon.pid"

    @property
    def status_file(self) -> Path:
        return self.neko_dir / "status.json"

    @property
    def cloud_mutations_file(self) -> Path:
        return self.neko_dir / "cloud_mutations.json"

    @property
    def notifications_file(self) -> Path:
        return self.neko_dir / "notifications.json"

    @property
    def log_file(self) -> Path:
        return self.neko_dir / "logs" / "daemon.log"

    @property
    def tasks_file(self) -> Path:
        return self.workspace_dir / "tasks.json"

    @property
    def tasks_md_file(self) -> Path:
        return self.workspace_dir / "TASKS.md"

    @property
    def time_logs_dir(self) -> Path:
        return self.workspace_dir / "time_logs"

    def ensure_dirs(self) -> None:
        """Creates necessary runtime and workspace directories if not present."""
        self.neko_dir.mkdir(parents=True, exist_ok=True)
        (self.neko_dir / "logs").mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.time_logs_dir.mkdir(parents=True, exist_ok=True)

    def save(self, target: Optional[Path] = None) -> None:
        """Saves current configuration to JSON atomically."""
        self.ensure_dirs()
        path = target if target is not None else (self.neko_dir / "config.json")
        data: Dict[str, Any] = {}
        for k, v in asdict(self).items():
            if not isinstance(v, Path):
                data[k] = v
        atomic_write_json_file(path, data)

    @classmethod
    def load(
        cls,
        config_path: Optional[Path] = None,
        root_dir: Optional[Path] = None,
    ) -> "Config":
        """Loads configuration from disk, repairing corrupt files and applying defaults."""
        root = Path(root_dir).resolve() if root_dir is not None else cls.find_project_root()
        neko_dir = root / ".neko"
        workspace_dir = root / "workspace"
        target = Path(config_path) if config_path is not None else (neko_dir / "config.json")

        cfg = cls(
            project_root=root,
            workspace_dir=workspace_dir,
            neko_dir=neko_dir,
        )

        if target.exists():
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if hasattr(cfg, k) and k not in ("project_root", "workspace_dir", "neko_dir"):
                            setattr(cfg, k, v)
            except Exception as e:
                logger.warning("Corrupt config file at %s: %s. Resetting with defaults.", target, e)
                cfg.ensure_dirs()
                cfg.save(target)
        else:
            cfg.ensure_dirs()
            cfg.save(target)

        return cfg

    @staticmethod
    def find_project_root() -> Path:
        """Searches upward from current working directory or file for project root."""
        curr = Path.cwd().resolve()
        for candidate in [curr] + list(curr.parents):
            if (
                (candidate / "pyproject.toml").exists()
                or (candidate / "neko.bat").exists()
                or (candidate / ".neko").exists()
            ):
                return candidate
        # Fallback to current directory
        return curr


_global_config: Optional[Config] = None


def get_config(root_dir: Optional[Path] = None) -> Config:
    """Returns singleton Config instance."""
    global _global_config
    if _global_config is None or root_dir is not None:
        _global_config = Config.load(root_dir=root_dir)
    return _global_config
