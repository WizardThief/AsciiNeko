"""
neko_bot.tracker.aggregator - Multi-Machine Time Aggregation & Reporting Engine.

Provides:
- Daily, weekly, and custom date range rollups over partitioned JSONL logs.
- Seamless multi-machine (HOSTNAME) log combination with 0.0% conflict risk.
- Breakdown by project, category, application, and hostname.
- Human-readable duration formatting ('Xh Ym').
- Stand-alone status helpers for `neko status --json` integration.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("neko_bot.tracker.aggregator")


def format_duration(seconds: Union[int, float], include_seconds: bool = False) -> str:
    """
    Formats seconds into standard human-readable format.
    Default: 'Xh Ym' (e.g., '2h 15m', '0h 45m', '0h 00m').
    With include_seconds: 'Xh Ym Zs' (e.g., '2h 15m 30s').
    """
    total_sec = max(0, int(round(seconds)))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60

    if include_seconds:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{hours}h {minutes:02d}m"


@dataclass
class TimeEntry:
    """Represents a single parsed time slice from a partitioned JSONL log."""
    timestamp: str
    start_time: str
    end_time: str
    app: str
    title: str
    project: str
    duration_sec: float
    category: str
    hostname: str
    idle_subtracted: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any], default_hostname: str = "unknown") -> "TimeEntry":
        """Constructs TimeEntry from raw dict, supporting both duration_sec and duration_seconds."""
        dur = data.get("duration_sec")
        if dur is None:
            dur = data.get("duration_seconds", 0.0)
        try:
            dur_val = float(dur)
        except (ValueError, TypeError):
            dur_val = 0.0

        idle = data.get("idle_subtracted", 0.0)
        try:
            idle_val = float(idle)
        except (ValueError, TypeError):
            idle_val = 0.0

        return cls(
            timestamp=str(data.get("timestamp", "")),
            start_time=str(data.get("start_time", "")),
            end_time=str(data.get("end_time", "")),
            app=str(data.get("app", "unknown")),
            title=str(data.get("title", "")),
            project=str(data.get("project", "default") or "default"),
            duration_sec=max(0.0, dur_val),
            category=str(data.get("category", "other") or "other"),
            hostname=str(data.get("hostname", default_hostname)),
            idle_subtracted=max(0.0, idle_val),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "app": self.app,
            "title": self.title,
            "project": self.project,
            "duration_sec": round(self.duration_sec, 2),
            "category": self.category,
            "hostname": self.hostname,
            "idle_subtracted": round(self.idle_subtracted, 2),
        }


@dataclass
class AggregatedReport:
    """Aggregated time metrics over a date range across machines."""
    start_date: str
    end_date: str
    total_seconds: float
    total_human: str
    by_project: Dict[str, float]
    by_project_human: Dict[str, str]
    by_category: Dict[str, float]
    by_category_human: Dict[str, str]
    by_app: Dict[str, float]
    by_app_human: Dict[str, str]
    by_hostname: Dict[str, float]
    by_hostname_human: Dict[str, str]
    by_date: Dict[str, float]
    by_date_human: Dict[str, str]
    idle_subtracted_total: float
    idle_subtracted_human: str
    entry_count: int
    machines: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_seconds": round(self.total_seconds, 2),
            "total_human": self.total_human,
            "by_project": {k: round(v, 2) for k, v in self.by_project.items()},
            "by_project_human": self.by_project_human,
            "by_category": {k: round(v, 2) for k, v in self.by_category.items()},
            "by_category_human": self.by_category_human,
            "by_app": {k: round(v, 2) for k, v in self.by_app.items()},
            "by_app_human": self.by_app_human,
            "by_hostname": {k: round(v, 2) for k, v in self.by_hostname.items()},
            "by_hostname_human": self.by_hostname_human,
            "by_date": {k: round(v, 2) for k, v in self.by_date.items()},
            "by_date_human": self.by_date_human,
            "idle_subtracted_total": round(self.idle_subtracted_total, 2),
            "idle_subtracted_human": self.idle_subtracted_human,
            "entry_count": self.entry_count,
            "machines": sorted(self.machines),
        }


class TimeAggregator:
    """
    Aggregates time logs across partitioned JSONL files:
    workspace/time_logs/<HOSTNAME>_<YYYY-MM-DD>.jsonl
    """

    FILENAME_PATTERN = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2})\.jsonl$", re.IGNORECASE)

    def __init__(self, time_logs_dir: Union[str, Path]) -> None:
        self.time_logs_dir = Path(time_logs_dir)

    def discover_log_files(
        self,
        start_date: Optional[Union[str, date]] = None,
        end_date: Optional[Union[str, date]] = None,
        hostname: Optional[str] = None,
    ) -> List[Tuple[Path, str, str]]:
        """
        Discovers matching log files in time_logs_dir.
        Returns tuples: (file_path, hostname, date_str).
        """
        if not self.time_logs_dir.exists():
            return []

        start_str = str(start_date) if start_date else None
        end_str = str(end_date) if end_date else None

        matched: List[Tuple[Path, str, str]] = []
        for file_path in self.time_logs_dir.glob("*.jsonl"):
            if not file_path.is_file():
                continue

            match = self.FILENAME_PATTERN.match(file_path.name)
            if match:
                f_host = match.group(1)
                f_date = match.group(2)
            else:
                f_host = "unknown"
                try:
                    f_date = datetime.fromtimestamp(file_path.stat().st_mtime).strftime("%Y-%m-%d")
                except OSError:
                    f_date = datetime.now().strftime("%Y-%m-%d")

            if hostname and f_host.lower() != hostname.lower():
                continue

            if start_str and f_date < start_str:
                continue
            if end_str and f_date > end_str:
                continue

            matched.append((file_path, f_host, f_date))

        matched.sort(key=lambda x: (x[2], x[1], x[0].name))
        return matched

    def read_entries(self, file_tuples: List[Tuple[Path, str, str]]) -> List[TimeEntry]:
        """
        Reads entries from log files, skipping corrupted lines gracefully.
        """
        entries: List[TimeEntry] = []
        for file_path, f_host, _ in file_tuples:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    for line_num, line in enumerate(f, start=1):
                        sline = line.strip()
                        if not sline:
                            continue
                        try:
                            raw = json.loads(sline)
                            if isinstance(raw, dict):
                                entries.append(TimeEntry.from_dict(raw, default_hostname=f_host))
                        except json.JSONDecodeError as err:
                            logger.warning("Corrupt JSON in %s:%d: %s", file_path.name, line_num, err)
            except OSError as err:
                logger.error("Failed to read log file %s: %s", file_path, err)

        return entries

    def aggregate(
        self,
        start_date: Optional[Union[str, date]] = None,
        end_date: Optional[Union[str, date]] = None,
        hostname: Optional[str] = None,
        project_filter: Optional[str] = None,
    ) -> AggregatedReport:
        """
        Calculates complete aggregation report across matching log files.
        """
        file_tuples = self.discover_log_files(start_date=start_date, end_date=end_date, hostname=hostname)
        entries = self.read_entries(file_tuples)

        start_str = str(start_date) if start_date else None
        end_str = str(end_date) if end_date else None

        # Filter entries by start/end date and project
        filtered_entries: List[TimeEntry] = []
        for e in entries:
            dt_key = e.start_time[:10] if len(e.start_time) >= 10 else e.timestamp[:10]
            if start_str and dt_key and dt_key < start_str:
                continue
            if end_str and dt_key and dt_key > end_str:
                continue
            if project_filter:
                if (e.project or "").lower() != project_filter.lower():
                    continue
            filtered_entries.append(e)

        dates_seen = {t[2] for t in file_tuples}
        final_start = start_str if start_str else (min(dates_seen) if dates_seen else datetime.now().strftime("%Y-%m-%d"))
        final_end = end_str if end_str else (max(dates_seen) if dates_seen else datetime.now().strftime("%Y-%m-%d"))

        total_sec = 0.0
        idle_sub_total = 0.0
        by_proj: Dict[str, float] = {}
        by_cat: Dict[str, float] = {}
        by_app: Dict[str, float] = {}
        by_host: Dict[str, float] = {}
        by_dt: Dict[str, float] = {}
        machines: Set[str] = set()

        for e in filtered_entries:
            dur = e.duration_sec
            total_sec += dur
            idle_sub_total += e.idle_subtracted
            machines.add(e.hostname)

            p = e.project or "default"
            by_proj[p] = by_proj.get(p, 0.0) + dur

            c = e.category or "other"
            by_cat[c] = by_cat.get(c, 0.0) + dur

            a = e.app or "unknown"
            by_app[a] = by_app.get(a, 0.0) + dur

            h = e.hostname or "unknown"
            by_host[h] = by_host.get(h, 0.0) + dur

            dt_key = e.start_time[:10] if len(e.start_time) >= 10 else e.timestamp[:10]
            if not dt_key or len(dt_key) < 10:
                dt_key = final_start
            by_dt[dt_key] = by_dt.get(dt_key, 0.0) + dur

        by_proj_sorted = dict(sorted(by_proj.items(), key=lambda x: x[1], reverse=True))
        by_cat_sorted = dict(sorted(by_cat.items(), key=lambda x: x[1], reverse=True))
        by_app_sorted = dict(sorted(by_app.items(), key=lambda x: x[1], reverse=True))
        by_host_sorted = dict(sorted(by_host.items(), key=lambda x: x[1], reverse=True))
        by_dt_sorted = dict(sorted(by_dt.items(), key=lambda x: x[0]))

        return AggregatedReport(
            start_date=final_start,
            end_date=final_end,
            total_seconds=total_sec,
            total_human=format_duration(total_sec),
            by_project=by_proj_sorted,
            by_project_human={k: format_duration(v) for k, v in by_proj_sorted.items()},
            by_category=by_cat_sorted,
            by_category_human={k: format_duration(v) for k, v in by_cat_sorted.items()},
            by_app=by_app_sorted,
            by_app_human={k: format_duration(v) for k, v in by_app_sorted.items()},
            by_hostname=by_host_sorted,
            by_hostname_human={k: format_duration(v) for k, v in by_host_sorted.items()},
            by_date=by_dt_sorted,
            by_date_human={k: format_duration(v) for k, v in by_dt_sorted.items()},
            idle_subtracted_total=idle_sub_total,
            idle_subtracted_human=format_duration(idle_sub_total),
            entry_count=len(filtered_entries),
            machines=list(machines),
        )

    def aggregate_today(self, hostname: Optional[str] = None) -> AggregatedReport:
        """Aggregates all logs for today's date."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        return self.aggregate(start_date=today_str, end_date=today_str, hostname=hostname)

    def aggregate_week(
        self,
        hostname: Optional[str] = None,
        end_date: Optional[Union[str, date]] = None,
    ) -> AggregatedReport:
        """Aggregates logs for a 7-day period ending on end_date (default: today)."""
        if end_date is None:
            end_dt = date.today()
        elif isinstance(end_date, str):
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
        else:
            end_dt = end_date

        start_dt = end_dt - timedelta(days=6)
        return self.aggregate(
            start_date=start_dt.strftime("%Y-%m-%d"),
            end_date=end_dt.strftime("%Y-%m-%d"),
            hostname=hostname,
        )

    def aggregate_project(
        self,
        project_name: str,
        start_date: Optional[Union[str, date]] = None,
        end_date: Optional[Union[str, date]] = None,
    ) -> Dict[str, Any]:
        """Drills into a specific project across dates and machines."""
        report = self.aggregate(start_date=start_date, end_date=end_date, project_filter=project_name)
        return {
            "project": project_name,
            "start_date": report.start_date,
            "end_date": report.end_date,
            "total_seconds": report.total_seconds,
            "total_human": report.total_human,
            "by_category": report.by_category,
            "by_category_human": report.by_category_human,
            "by_app": report.by_app,
            "by_app_human": report.by_app_human,
            "by_date": report.by_date,
            "by_date_human": report.by_date_human,
            "by_hostname": report.by_hostname,
            "by_hostname_human": report.by_hostname_human,
            "idle_subtracted_total": report.idle_subtracted_total,
            "entry_count": report.entry_count,
        }

    def get_today_tracked_seconds(self) -> int:
        """Returns total active seconds tracked today across all hosts."""
        rep = self.aggregate_today()
        return int(round(rep.total_seconds))


def get_today_tracked_seconds(time_logs_dir: Union[str, Path], target_date: Optional[str] = None) -> int:
    """Convenience helper for fast scalar status retrieval."""
    dt_str = target_date or datetime.now().strftime("%Y-%m-%d")
    agg = TimeAggregator(time_logs_dir)
    rep = agg.aggregate(start_date=dt_str, end_date=dt_str)
    return int(round(rep.total_seconds))


def get_tracking_status_metrics(time_logs_dir: Union[str, Path], hostname: Optional[str] = None) -> Dict[str, Any]:
    """Helper retrieving real-time tracking summary metrics."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    agg = TimeAggregator(time_logs_dir)
    rep = agg.aggregate(start_date=today_str, end_date=today_str, hostname=hostname)
    top_p = list(rep.by_project.keys())[0] if rep.by_project else None
    top_a = list(rep.by_app.keys())[0] if rep.by_app else None
    return {
        "today_tracked_seconds": int(round(rep.total_seconds)),
        "tracked_time_today_sec": int(round(rep.total_seconds)),
        "active_application": top_a,
        "active_app": top_a,
        "active_project": top_p,
    }
