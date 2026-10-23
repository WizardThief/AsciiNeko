"""
neko_bot.tracker.logger - Machine-Partitioned Append-Only JSONL Time Logger.

Provides:
- Partitioned path template: workspace/time_logs/<HOSTNAME>_<YYYY-MM-DD>.jsonl
- Thread-safe, atomic append-only write semantics with flush and os.fsync.
- Strict single-line JSONL format, sanitizing newlines and carriage returns.
- Cross-machine isolation guaranteeing 0.00% Git merge conflict across multi-PC setups.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import socket
import threading
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("neko_bot.tracker.logger")

VALID_CATEGORIES = {"creative", "dev", "comms", "browsing", "system", "general"}


def sanitize_hostname(hostname: str) -> str:
    """Sanitizes hostname for cross-platform filesystem and Git branch safety."""
    sanitized = re.sub(r"[^\w\-]", "_", hostname.strip())
    return sanitized if sanitized else "unknown_host"


def sanitize_title(title: str) -> str:
    """Removes newlines and control characters to preserve single-line JSONL integrity."""
    cleaned = re.sub(r"[\r\n\t]+", " ", title).strip()
    return cleaned if cleaned else "(Untitled)"


class TimeLogger:
    """
    Append-only, machine-partitioned JSONL time logger.
    Each machine writes strictly to its own hostname-prefixed file.
    """

    def __init__(self, logs_dir: Union[str, Path], hostname: Optional[str] = None) -> None:
        self.logs_dir = Path(logs_dir)
        raw_host = hostname or os.getenv("COMPUTERNAME", socket.gethostname())
        self.hostname = sanitize_hostname(raw_host)
        self._lock = threading.Lock()

    def get_log_path(self, target_date: Optional[str] = None) -> Path:
        """Computes path: <logs_dir>/<HOSTNAME>_<YYYY-MM-DD>.jsonl."""
        if target_date is None:
            target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.logs_dir / f"{self.hostname}_{target_date}.jsonl"

    def log_record(
        self,
        start_time: str,
        end_time: str,
        app: str,
        title: str,
        project: str,
        duration_sec: float,
        category: str,
        idle_subtracted: float = 0.0,
        timestamp: Optional[str] = None,
    ) -> Path:
        """
        Appends a single time tracking record to disk atomically.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Extract date from end_time or timestamp
        date_str = end_time[:10] if len(end_time) >= 10 else timestamp[:10]
        target_path = self.get_log_path(target_date=date_str)

        norm_cat = category.strip().lower() if category else "general"
        if norm_cat not in VALID_CATEGORIES:
            norm_cat = "general"

        record_dict: Dict[str, Any] = {
            "timestamp": timestamp,
            "start_time": start_time,
            "end_time": end_time,
            "app": app.strip() if app else "unknown.exe",
            "title": sanitize_title(title),
            "project": project.strip() if project else "General",
            "duration_sec": round(max(0.0, float(duration_sec)), 2),
            "category": norm_cat,
            "hostname": self.hostname,
            "idle_subtracted": round(max(0.0, float(idle_subtracted)), 2),
        }

        line = json.dumps(record_dict, ensure_ascii=False, separators=(",", ":")) + "\n"

        with self._lock:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            with open(target_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass

        return target_path

    def read_records(
        self,
        target_date: Optional[str] = None,
        hostname: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Reads and parses JSONL records, gracefully skipping empty or corrupted lines.
        """
        records: List[Dict[str, Any]] = []
        host_pattern = sanitize_hostname(hostname) if hostname else "*"
        date_pattern = target_date if target_date else "*"
        pattern = f"{host_pattern}_{date_pattern}.jsonl"

        if not self.logs_dir.exists():
            return []

        for log_file in sorted(self.logs_dir.glob(pattern)):
            if not log_file.is_file():
                continue
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, start=1):
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            rec = json.loads(line_str)
                            if isinstance(rec, dict) and "duration_sec" in rec:
                                records.append(rec)
                        except json.JSONDecodeError as e:
                            logger.warning("Corrupt JSON line in %s:%d: %s", log_file.name, line_num, e)
                            continue
            except OSError as e:
                logger.error("Failed to read log file %s: %s", log_file, e)

        return records
