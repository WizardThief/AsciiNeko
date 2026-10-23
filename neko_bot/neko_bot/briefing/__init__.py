"""
neko_bot.briefing - Proactive Briefings, Focus Timers & Continuous Work Monitoring.
"""

from neko_bot.briefing.generator import (
    BriefingData,
    BriefingResult,
    BriefingGenerator,
    BriefingTriggerManager,
)
from neko_bot.briefing.timers import (
    BreakAlert,
    TimerEvent,
    PomodoroSession,
    ContinuousWorkMonitor,
    ContinuousWorkTimer,
    ContinuousWorkTracker,
)

__all__ = [
    "BriefingData",
    "BriefingResult",
    "BriefingGenerator",
    "BriefingTriggerManager",
    "BreakAlert",
    "TimerEvent",
    "PomodoroSession",
    "ContinuousWorkMonitor",
    "ContinuousWorkTimer",
    "ContinuousWorkTracker",
]
