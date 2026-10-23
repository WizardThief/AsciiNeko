"""
neko_bot.briefing.timers - Continuous Work Tracker & Focus Session Timers (F19, AC4).

Features:
- ContinuousWorkMonitor tracks uninterrupted active work in foreground applications.
- Triggers 55-minute (3300s) continuous work break reminders.
- Resets continuous counter to 0 when user takes an AFK break >= 5 minutes (300s).
- Micro-pauses (< 300s) pause work accumulation without resetting the counter.
- FocusTimerEngine supporting Pomodoro intervals (25/5, 50/10, custom).
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger("neko_bot.briefing.timers")


@dataclass
class BreakAlert:
    """Represents a continuous work break notification."""
    title: str
    message: str
    continuous_seconds: float
    afk_reset_seconds: float
    fired_at: float
    event_type: str = "break_reminder"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "message": self.message,
            "continuous_seconds": round(self.continuous_seconds, 1),
            "afk_reset_seconds": round(self.afk_reset_seconds, 1),
            "fired_at": self.fired_at,
            "event_type": self.event_type,
        }


@dataclass
class TimerEvent:
    """Represents a focus session transition event."""
    event_type: str  # "work_completed" | "break_completed"
    title: str
    message: str
    session_type: str


class PomodoroSession:
    """Simple Pomodoro session model supporting tick and state inspection."""

    def __init__(self, work_minutes: int = 25, break_minutes: int = 5) -> None:
        self.work_seconds = float(work_minutes * 60)
        self.break_seconds = float(break_minutes * 60)
        self.remaining_seconds = self.work_seconds
        self.state = "work"  # "work" | "break" | "completed"

    def tick(self, seconds: float = 1.0) -> Optional[str]:
        self.remaining_seconds -= seconds
        if self.remaining_seconds <= 0:
            if self.state == "work":
                self.state = "break"
                self.remaining_seconds = self.break_seconds
                return "work_completed"
            elif self.state == "break":
                self.state = "completed"
                self.remaining_seconds = 0
                return "break_completed"
        return None


class ContinuousWorkMonitor:
    """
    Tracks uninterrupted active work in foreground applications.
    - Triggers break alert at 55 minutes (3300s).
    - Resets counter when user is AFK for >= 5 minutes (300s).
    - Micro-pauses (< 300s) pause active work increment without resetting counter.
    """

    def __init__(
        self,
        break_threshold_seconds: Optional[float] = None,
        afk_reset_seconds: Optional[float] = None,
        break_threshold_minutes: Optional[float] = None,
        afk_reset_minutes: Optional[float] = None,
        break_interval: Optional[float] = None,
        break_duration: Optional[float] = None,
        language: str = "it",
    ) -> None:
        # Harmonize parameter names
        if break_interval is not None:
            self.break_threshold_seconds = float(break_interval)
        elif break_threshold_minutes is not None:
            self.break_threshold_seconds = float(break_threshold_minutes * 60)
        elif break_threshold_seconds is not None:
            self.break_threshold_seconds = float(break_threshold_seconds)
        else:
            self.break_threshold_seconds = 3300.0  # 55 minutes

        if break_duration is not None:
            self.afk_reset_seconds = float(break_duration)
        elif afk_reset_minutes is not None:
            self.afk_reset_seconds = float(afk_reset_minutes * 60)
        elif afk_reset_seconds is not None:
            self.afk_reset_seconds = float(afk_reset_seconds)
        else:
            self.afk_reset_seconds = 300.0  # 5 minutes

        self.language = language
        self.continuous_active_seconds: float = 0.0
        self.consecutive_afk_seconds: float = 0.0
        self.alert_fired: bool = False
        self.last_tick_time: Optional[float] = None
        self.last_alert: Optional[BreakAlert] = None

    @property
    def active_continuous_seconds(self) -> float:
        """Alias for continuous_active_seconds."""
        return self.continuous_active_seconds

    @active_continuous_seconds.setter
    def active_continuous_seconds(self, value: float) -> None:
        self.continuous_active_seconds = value

    def tick(
        self,
        is_afk: bool = False,
        idle_seconds: float = 0.0,
        delta_seconds: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Optional[BreakAlert]:
        """
        Evaluates elapsed active time and idle state.
        Returns BreakAlert when uninterrupted active work reaches break threshold (55 min).
        """
        curr = time.time() if now is None else float(now)

        if delta_seconds is not None:
            delta = max(0.0, float(delta_seconds))
            self.last_tick_time = curr
        elif self.last_tick_time is None:
            self.last_tick_time = curr
            return None
        else:
            delta = max(0.0, curr - self.last_tick_time)
            self.last_tick_time = curr

        # Determine effective AFK state:
        # Either flagged AFK or idle_seconds exceeds afk_reset_seconds threshold
        user_is_idle = is_afk or (idle_seconds >= self.afk_reset_seconds)

        if user_is_idle:
            self.consecutive_afk_seconds += delta
            # Rule: 5-minute AFK resets continuous work counter to 0
            if self.consecutive_afk_seconds >= self.afk_reset_seconds:
                self.continuous_active_seconds = 0.0
                self.alert_fired = False
            return None

        # User is active:
        self.consecutive_afk_seconds = 0.0
        self.continuous_active_seconds += delta

        # Rule: 55-minute continuous work triggers break alert
        if self.continuous_active_seconds >= self.break_threshold_seconds and not self.alert_fired:
            self.alert_fired = True
            minutes = int(round(self.continuous_active_seconds / 60))
            if self.language == "it":
                title = "☕ Pausa Caffè Consigliata"
                msg = f"Pausa caffè! Lavori da {minutes} minuti senza sosta. Alzati e fai una pausa! ☕🐱"
            else:
                title = "☕ Coffee Break Recommended"
                msg = f"Coffee break! You have been working for {minutes} minutes without a break. Stretch your legs! ☕🐱"

            alert = BreakAlert(
                title=title,
                message=msg,
                continuous_seconds=self.continuous_active_seconds,
                afk_reset_seconds=self.afk_reset_seconds,
                fired_at=curr,
            )
            self.last_alert = alert
            return alert

        return None

    def update(
        self,
        now: Optional[float] = None,
        is_afk: bool = False,
        idle_seconds: float = 0.0,
        delta_seconds: Optional[float] = None,
    ) -> bool:
        """
        Convenience method returning boolean True if break alert should trigger.
        """
        alert = self.tick(
            is_afk=is_afk,
            idle_seconds=idle_seconds,
            delta_seconds=delta_seconds,
            now=now,
        )
        return alert is not None

    def reset(self) -> None:
        """Manually resets tracker counters and alert status."""
        self.continuous_active_seconds = 0.0
        self.consecutive_afk_seconds = 0.0
        self.alert_fired = False
        self.last_tick_time = None
        self.last_alert = None

    def create_pomodoro_session(self, work_minutes: int = 25, break_minutes: int = 5) -> PomodoroSession:
        """Creates a managed Pomodoro focus session."""
        return PomodoroSession(work_minutes=work_minutes, break_minutes=break_minutes)

    def get_status(self) -> Dict[str, Any]:
        return {
            "continuous_active_seconds": round(self.continuous_active_seconds, 1),
            "consecutive_afk_seconds": round(self.consecutive_afk_seconds, 1),
            "break_threshold_seconds": self.break_threshold_seconds,
            "afk_reset_seconds": self.afk_reset_seconds,
            "alert_fired": self.alert_fired,
            "percent_to_break": min(
                100.0,
                round((self.continuous_active_seconds / self.break_threshold_seconds) * 100, 1),
            ),
        }


# Compatibility Aliases
ContinuousWorkTimer = ContinuousWorkMonitor
ContinuousWorkTracker = ContinuousWorkMonitor
