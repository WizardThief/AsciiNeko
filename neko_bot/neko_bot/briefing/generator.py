"""
neko_bot.briefing.generator - Proactive Startup & Daily Briefing Generator (F18, AC4).

Features:
- Queries TimeAggregator across multi-machine JSONL logs for yesterday and today.
- Queries TaskStore and ranking engine for active, overdue, and top priority tasks.
- Tier 1 Deterministic Template (< 2ms latency SLA): Natural Italian and English phrasing.
- Tier 2 LLM Phrasing via OllamaRouter: Strictly bypassed in creative/low_vram mode
  to guarantee zero GPU VRAM impact and < 2s SLA.
- Morning & Evening briefing generation.
- Persistent BriefingTriggerManager for once-a-day morning triggers (.neko/briefing_state.json).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from neko_bot.core.config import Config, atomic_write_json_file, get_config
from neko_bot.core.resource_monitor import ResourceMonitor
from neko_bot.core.router import OllamaRouter, get_default_router
from neko_bot.task.ranking import get_task_summary, rank_tasks
from neko_bot.task.store import TaskStore
from neko_bot.tracker.aggregator import AggregatedReport, TimeAggregator, format_duration

logger = logging.getLogger("neko_bot.briefing.generator")


@dataclass
class BriefingData:
    """Consolidated metrics gathered across machines and task stores."""
    date_str: str
    yesterday_str: str
    yesterday_total_seconds: float
    yesterday_total_human: str
    yesterday_by_project: Dict[str, str]
    today_total_seconds: float
    today_total_human: str
    today_by_project: Dict[str, str]
    active_tasks_count: int
    overdue_tasks_count: int
    due_today_tasks_count: int
    high_priority_tasks_count: int
    top_tasks: List[Dict[str, Any]]
    machines: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date_str,
            "yesterday_date": self.yesterday_str,
            "yesterday_hours": self.yesterday_total_human,
            "yesterday_seconds": round(self.yesterday_total_seconds, 1),
            "yesterday_projects": self.yesterday_by_project,
            "today_hours": self.today_total_human,
            "today_seconds": round(self.today_total_seconds, 1),
            "today_projects": self.today_by_project,
            "active_tasks": self.active_tasks_count,
            "overdue_tasks": self.overdue_tasks_count,
            "due_today_tasks": self.due_today_tasks_count,
            "high_priority_tasks": self.high_priority_tasks_count,
            "top_tasks": self.top_tasks,
            "machines": self.machines,
        }


@dataclass
class BriefingResult:
    """Formatted briefing output with metadata."""
    title: str
    body: str
    short_body: str
    tier: str  # "tier1_deterministic" | "tier2_llm"
    resource_mode: str  # "normal" | "creative_detected" | "low_vram"
    zero_vram_preserved: bool
    language: str  # "it" | "en"
    elapsed_ms: float
    data: BriefingData

    @property
    def text(self) -> str:
        """Alias for body text for consumer compatibility."""
        return self.body

    @property
    def generator_mode(self) -> str:
        """Indicates generator strategy for audit and testing."""
        if self.zero_vram_preserved and self.resource_mode in ("creative_detected", "low_vram"):
            return "deterministic_creative_bypass"
        return self.tier

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "text": self.body,
            "short_body": self.short_body,
            "tier": self.tier,
            "generator_mode": self.generator_mode,
            "resource_mode": self.resource_mode,
            "zero_vram_preserved": self.zero_vram_preserved,
            "language": self.language,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "data": self.data.to_dict(),
        }


class BriefingGenerator:
    """
    Dual-Tier Briefing Generation Engine.
    Tier 1: Instant deterministic template (< 2ms) in IT/EN.
    Tier 2: LLM-enhanced generation via OllamaRouter (strictly bypassed in creative/low_vram mode).
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        aggregator: Optional[TimeAggregator] = None,
        task_store: Optional[TaskStore] = None,
        router: Optional[OllamaRouter] = None,
        resource_monitor: Optional[ResourceMonitor] = None,
    ) -> None:
        self.config = config or get_config()
        self.aggregator = aggregator or TimeAggregator(self.config.time_logs_dir)
        self.task_store = task_store or TaskStore(self.config.workspace_dir, config=self.config)
        self.router = router or get_default_router(self.config)
        self.resource_monitor = resource_monitor or self.router.resource_monitor

    def collect_data(self, reference_time: Optional[datetime] = None) -> BriefingData:
        """Collects yesterday and today's tracked hours and task summaries."""
        now = reference_time or datetime.now(timezone.utc)
        today_date = now.date()
        yesterday_date = today_date - timedelta(days=1)
        today_str = today_date.strftime("%Y-%m-%d")
        yesterday_str = yesterday_date.strftime("%Y-%m-%d")

        # 1. Aggregate time across machines
        rep_yesterday = self.aggregator.aggregate(start_date=yesterday_str, end_date=yesterday_str)
        if rep_yesterday.total_seconds == 0 and reference_time is None:
            local_yesterday_str = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
            if local_yesterday_str != yesterday_str:
                rep_local = self.aggregator.aggregate(start_date=local_yesterday_str, end_date=local_yesterday_str)
                if rep_local.total_seconds > 0:
                    rep_yesterday = rep_local
                    yesterday_str = local_yesterday_str

        rep_today = self.aggregator.aggregate(start_date=today_str, end_date=today_str)
        if rep_today.total_seconds == 0 and reference_time is None:
            local_today_str = datetime.now().date().strftime("%Y-%m-%d")
            if local_today_str != today_str:
                rep_local_today = self.aggregator.aggregate(start_date=local_today_str, end_date=local_today_str)
                if rep_local_today.total_seconds > 0:
                    rep_today = rep_local_today
                    today_str = local_today_str

        # 2. Extract active and overdue tasks
        if hasattr(self.task_store, "load"):
            self.task_store.load()
        tasks_summary = get_task_summary(self.task_store, reference_time=now)
        active_tasks = self.task_store.get_active_tasks()
        ranked = rank_tasks(active_tasks, reference_time=now)

        top_tasks_data: List[Dict[str, Any]] = []
        for t in ranked[:5]:
            prio = getattr(t, "priority", "medium")
            prio_val = prio.value if hasattr(prio, "value") else str(prio)
            top_tasks_data.append({
                "id": t.id,
                "title": t.title,
                "project": t.project,
                "priority": prio_val,
                "due_date": t.due_date,
            })

        machines = sorted(list(set(rep_yesterday.machines + rep_today.machines)))

        return BriefingData(
            date_str=today_str,
            yesterday_str=yesterday_str,
            yesterday_total_seconds=rep_yesterday.total_seconds,
            yesterday_total_human=rep_yesterday.total_human,
            yesterday_by_project=rep_yesterday.by_project_human,
            today_total_seconds=rep_today.total_seconds,
            today_total_human=rep_today.total_human,
            today_by_project=rep_today.by_project_human,
            active_tasks_count=tasks_summary.get("total_active", 0),
            overdue_tasks_count=tasks_summary.get("overdue_count", 0),
            due_today_tasks_count=tasks_summary.get("due_today_count", 0),
            high_priority_tasks_count=tasks_summary.get("high_priority_count", 0),
            top_tasks=top_tasks_data,
            machines=machines,
        )

    def generate_tier1_template(
        self,
        data: BriefingData,
        language: str = "it",
        hour: int = 9,
        is_evening: bool = False,
    ) -> Tuple[str, str, str]:
        """
        Instant deterministic template (< 2ms execution).
        Returns (title, body, short_body).
        """
        is_it = language.lower() == "it"

        if is_evening:
            # Evening wrap-up template
            if is_it:
                title = "🌆 Neko Riepilogo Serale"
                greeting = "Buonasera!"
                if data.today_total_seconds > 0:
                    proj_items = list(data.today_by_project.items())[:3]
                    proj_str = f" ({', '.join(f'#{k}: {v}' for k, v in proj_items)})" if proj_items else ""
                    work_line = f"Oggi hai lavorato {data.today_total_human}{proj_str}."
                else:
                    work_line = "Nessuna sessione di lavoro registrata oggi."

                task_line = f"Task attivi rimanenti: {data.active_tasks_count}."
                body = f"{greeting} {work_line}\n{task_line}"
                short_body = f"Oggi: {data.today_total_human}. Task rimanenti: {data.active_tasks_count}."
            else:
                title = "🌆 Neko Evening Wrap-up"
                greeting = "Good evening!"
                if data.today_total_seconds > 0:
                    proj_items = list(data.today_by_project.items())[:3]
                    proj_str = f" ({', '.join(f'#{k}: {v}' for k, v in proj_items)})" if proj_items else ""
                    work_line = f"Today you worked {data.today_total_human}{proj_str}."
                else:
                    work_line = "No work sessions logged today."

                task_line = f"Remaining active tasks: {data.active_tasks_count}."
                body = f"{greeting} {work_line}\n{task_line}"
                short_body = f"Today: {data.today_total_human}. Remaining tasks: {data.active_tasks_count}."

            return title, body, short_body

        # Morning / Startup Briefing Template
        if is_it:
            if hour < 12:
                greeting = "Buongiorno!"
            elif hour < 18:
                greeting = "Buon pomeriggio!"
            else:
                greeting = "Buonasera!"
            title = "🌅 Neko Briefing Mattutino"
        else:
            if hour < 12:
                greeting = "Good morning!"
            elif hour < 18:
                greeting = "Good afternoon!"
            else:
                greeting = "Good evening!"
            title = "🌅 Neko Morning Briefing"

        # Yesterday's hours breakdown
        proj_items = list(data.yesterday_by_project.items())[:3]
        if proj_items:
            proj_str = ", ".join(f"#{k}: {v}" for k, v in proj_items)
            proj_clause = f" ({proj_str})"
        else:
            proj_clause = ""

        if is_it:
            if data.yesterday_total_seconds > 0:
                work_line = f"Ieri hai lavorato {data.yesterday_total_human}{proj_clause}."
            else:
                work_line = "Nessuna sessione di lavoro registrata ieri."
        else:
            if data.yesterday_total_seconds > 0:
                work_line = f"Yesterday you worked {data.yesterday_total_human}{proj_clause}."
            else:
                work_line = "No work logged yesterday."

        # Tasks section
        urgent_count = data.overdue_tasks_count + data.high_priority_tasks_count
        task_lines: List[str] = []

        if is_it:
            if urgent_count > 0:
                task_header = f"Oggi hai {urgent_count} task urgenti:"
            elif data.active_tasks_count > 0:
                task_header = f"Oggi hai {data.active_tasks_count} task attivi:"
            else:
                task_header = "Nessun task urgente in sospeso. Ottimo lavoro! ✨"

            task_lines.append(task_header)
            for idx, t in enumerate(data.top_tasks[:3], start=1):
                p_tag = f" (#{t['project']})" if t.get("project") else ""
                prio_tag = f", !{t['priority']}" if t.get("priority") == "high" else ""
                task_lines.append(f"{idx}. {t['title']}{p_tag}{prio_tag}")
        else:
            if urgent_count > 0:
                task_header = f"You have {urgent_count} urgent tasks today:"
            elif data.active_tasks_count > 0:
                task_header = f"You have {data.active_tasks_count} active tasks today:"
            else:
                task_header = "No urgent tasks pending today. Great job! ✨"

            task_lines.append(task_header)
            for idx, t in enumerate(data.top_tasks[:3], start=1):
                p_tag = f" (#{t['project']})" if t.get("project") else ""
                prio_tag = f", !{t['priority']}" if t.get("priority") == "high" else ""
                task_lines.append(f"{idx}. {t['title']}{p_tag}{prio_tag}")

        body = f"{greeting} {work_line}\n" + "\n".join(task_lines)

        # Short body for Toast
        top_name = data.top_tasks[0]["title"] if data.top_tasks else ("Nessuno" if is_it else "None")
        if is_it:
            short_body = f"Ieri: {data.yesterday_total_human}. Oggi: {urgent_count or data.active_tasks_count} task. Top: {top_name}"
        else:
            short_body = f"Yesterday: {data.yesterday_total_human}. Today: {urgent_count or data.active_tasks_count} tasks. Top: {top_name}"

        return title, body, short_body

    def generate(
        self,
        language: str = "it",
        force_tier1: bool = False,
        reference_time: Optional[datetime] = None,
        is_evening: bool = False,
    ) -> BriefingResult:
        """
        Executes briefing generation adhering strictly to the Zero-VRAM guarantee.
        """
        start_ts = time.perf_counter()
        now = reference_time or datetime.now()
        data = self.collect_data(reference_time=now)
        mode = self.resource_monitor.get_resource_mode()

        t1_title, t1_body, t1_short = self.generate_tier1_template(
            data, language=language, hour=now.hour, is_evening=is_evening
        )

        # Zero-VRAM Enforcement:
        # If creative apps are running or system load is high, strictly bypass Ollama.
        if force_tier1 or mode in ("creative_detected", "low_vram"):
            elapsed = (time.perf_counter() - start_ts) * 1000
            return BriefingResult(
                title=t1_title,
                body=t1_body,
                short_body=t1_short,
                tier="tier1_deterministic",
                resource_mode=mode,
                zero_vram_preserved=True,
                language=language,
                elapsed_ms=elapsed,
                data=data,
            )

        # Tier 2: LLM-Enhanced phrasing in normal mode
        sys_prompt = (
            "Sei Neko, assistente di produttività personale giapponese/italiano cordiale e conciso (🐱). "
            "Genera un briefing mattutino amichevole basato ESCLUSIVAMENTE sui dati reali forniti. "
            "Non allucinare task inventati. Massimo 3 frasi."
            if language == "it" else
            "You are Neko, a friendly, concise productivity assistant (🐱). "
            "Generate a morning briefing based STRICTLY on the provided data. "
            "Do not hallucinate tasks. Maximum 3 sentences."
        )

        user_prompt = json.dumps(data.to_dict(), ensure_ascii=False)

        def _fallback() -> str:
            return t1_body

        route_res = self.router.route_request(
            prompt=user_prompt,
            intent="briefing",
            fallback_func=_fallback,
            system_prompt=sys_prompt,
        )

        elapsed = (time.perf_counter() - start_ts) * 1000
        if route_res.get("source") == "ollama" and route_res.get("data"):
            llm_text = str(route_res["data"]).strip()
            return BriefingResult(
                title=t1_title,
                body=llm_text,
                short_body=t1_short,
                tier="tier2_llm",
                resource_mode=mode,
                zero_vram_preserved=False,
                language=language,
                elapsed_ms=elapsed,
                data=data,
            )

        # Fallback to tier 1
        return BriefingResult(
            title=t1_title,
            body=t1_body,
            short_body=t1_short,
            tier="tier1_deterministic",
            resource_mode=mode,
            zero_vram_preserved=True,
            language=language,
            elapsed_ms=elapsed,
            data=data,
        )

    def generate_morning_briefing(
        self,
        force_deterministic: bool = False,
        language: str = "it",
        reference_time: Optional[datetime] = None,
    ) -> BriefingResult:
        """Generates proactive morning briefing."""
        ref = reference_time or datetime.now().replace(hour=9, minute=0, second=0)
        return self.generate(
            language=language,
            force_tier1=force_deterministic,
            reference_time=ref,
            is_evening=False,
        )

    def generate_evening_wrapup(
        self,
        force_deterministic: bool = False,
        language: str = "it",
        reference_time: Optional[datetime] = None,
    ) -> BriefingResult:
        """Generates evening wrap-up briefing."""
        return self.generate(
            language=language,
            force_tier1=force_deterministic,
            reference_time=reference_time,
            is_evening=True,
        )


class BriefingTriggerManager:
    """Manages morning briefing trigger schedule and first-startup state persistence."""

    def __init__(self, state_file: Optional[Path] = None, config: Optional[Config] = None) -> None:
        self.config = config or get_config()
        self.state_file = Path(state_file) if state_file is not None else (self.config.neko_dir / "briefing_state.json")
        self.last_briefing_date: Optional[str] = None
        self._load_state()

    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.last_briefing_date = data.get("last_briefing_date")
            except Exception:
                self.last_briefing_date = None

    def _save_state(self, date_str: str) -> None:
        data = {
            "last_briefing_date": date_str,
            "last_briefing_timestamp": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_json_file(self.state_file, data)
        self.last_briefing_date = date_str

    def should_trigger(self, now: Optional[datetime] = None, morning_hour: int = 9) -> bool:
        """
        Determines if briefing should trigger:
        1. On first daemon startup of the day (last_briefing_date != today).
        2. Or when reaching the scheduled morning hour.
        """
        curr = now or datetime.now()
        today_str = curr.strftime("%Y-%m-%d")

        if self.last_briefing_date == today_str:
            return False

        # First startup of the day OR scheduled morning hour reached
        if self.last_briefing_date is None or curr.hour >= morning_hour:
            return True

        return False

    def mark_triggered(self, now: Optional[datetime] = None) -> None:
        """Records briefing delivery date."""
        curr = now or datetime.now()
        self._save_state(curr.strftime("%Y-%m-%d"))

    def reset(self) -> None:
        """Resets trigger state (for testing)."""
        self.last_briefing_date = None
        if self.state_file.exists():
            try:
                self.state_file.unlink()
            except Exception:
                pass
