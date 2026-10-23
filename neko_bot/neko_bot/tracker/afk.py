"""
neko_bot.tracker.afk - AFK State Machine, Session Slices & Retroactive Idle Subtraction.

Provides:
- AFKStatus: Enum ("active", "afk")
- TimeSlice: Dataclass capturing an active work session slice with start/end timestamps,
  duration in seconds, and any subtracted idle duration.
- AFKManager: State machine tracking window focus durations, evaluating idle thresholds,
  and executing retroactive idle subtraction when the user goes AFK.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import logging
import os
import socket
import time
from typing import List, Optional, Union

from neko_bot.tracker.win32 import WindowState

logger = logging.getLogger("neko_bot.tracker.afk")


class AFKStatus(str, Enum):
    ACTIVE = "active"
    AFK = "afk"


@dataclass
class TimeSlice:
    """Represents a discrete completed work duration for an active window."""
    app: str
    title: str
    start_time: float       # Epoch seconds (float)
    end_time: float         # Epoch seconds (float)
    duration_sec: float     # Net active seconds (excluding idle)
    idle_subtracted: float = 0.0
    hostname: str = ""

    @property
    def start_iso(self) -> str:
        return datetime.fromtimestamp(self.start_time, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def end_iso(self) -> str:
        return datetime.fromtimestamp(self.end_time, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AFKManager:
    """
    Manages user activity state (ACTIVE vs AFK) and window focus sessions.

    Key responsibilities:
    1. Tracks active window focus time between ticks.
    2. Detects transition to AFK when idle_seconds >= afk_threshold (default 180s).
    3. Performs RETROACTIVE IDLE SUBTRACTION:
       When transitioning to AFK, subtracts the idle duration (>= threshold)
       from the active session so idle time (e.g. lunch break) is not falsely credited
       as project work time.
    4. Handles transition from AFK back to ACTIVE upon user input (idle_seconds < threshold).
    5. Flushes completed TimeSlice objects when window switches, AFK begins, or daemon stops.
    """

    def __init__(
        self,
        afk_threshold: float = 180.0,
        hostname: str = "",
        afk_threshold_sec: Optional[float] = None,
    ) -> None:
        threshold = afk_threshold_sec if afk_threshold_sec is not None else afk_threshold
        self.afk_threshold: float = float(threshold)
        self.hostname: str = hostname or os.getenv("COMPUTERNAME", socket.gethostname())
        self.status: AFKStatus = AFKStatus.ACTIVE

        # Active session state
        self._current_window: Optional[WindowState] = None
        self._session_start_time: Optional[float] = None
        self._last_active_time: Optional[float] = None

    @property
    def is_afk(self) -> bool:
        return self.status == AFKStatus.AFK

    @property
    def is_active(self) -> bool:
        return self.status == AFKStatus.ACTIVE

    def start_session(
        self,
        app: str,
        title: str,
        start_time: Optional[float] = None,
    ) -> None:
        """Explicitly starts a tracked window session (used in testing and explicit tracking)."""
        ts = start_time if start_time is not None else time.time()
        self._current_window = WindowState(
            hwnd=1,
            title=title,
            pid=1,
            exe_name=app,
            exe_path="",
            timestamp=ts,
        )
        self._session_start_time = ts
        self._last_active_time = ts
        self.status = AFKStatus.ACTIVE

    def update(
        self,
        now: Optional[float] = None,
        window: Optional[WindowState] = None,
        idle_seconds: float = 0.0,
        elapsed_session_sec: Optional[float] = None,
    ) -> Union[Optional[TimeSlice], List[TimeSlice]]:
        """
        Updates the AFK state machine and session tracker on each daemon tick.

        Supports standard daemon invocation:
            update(now=now, window=window, idle_seconds=idle_sec) -> Optional[TimeSlice]
        And test invocation:
            update(idle_seconds=sec, elapsed_session_sec=elapsed) -> List[TimeSlice]
        """
        idle_seconds = max(0.0, float(idle_seconds))
        return_as_list = (elapsed_session_sec is not None)

        if elapsed_session_sec is not None:
            if self._session_start_time is None:
                self._session_start_time = 1000.0
            now = self._session_start_time + elapsed_session_sec
        elif now is None:
            now = time.time()

        slice_result: Optional[TimeSlice] = None

        # Initial startup tick
        if self._session_start_time is None:
            if idle_seconds >= self.afk_threshold:
                self.status = AFKStatus.AFK
                return [] if return_as_list else None
            else:
                self.status = AFKStatus.ACTIVE
                self._current_window = window
                self._session_start_time = now - idle_seconds
                self._last_active_time = now - idle_seconds
                return [] if return_as_list else None

        # -------------------------------------------------------------
        # Transition 1: ACTIVE -> AFK (Threshold Exceeded)
        # -------------------------------------------------------------
        if self.status == AFKStatus.ACTIVE and idle_seconds >= self.afk_threshold:
            self.status = AFKStatus.AFK

            if self._current_window and self._session_start_time is not None:
                # The user ceased active work at (now - idle_seconds)
                effective_end = max(self._session_start_time, now - idle_seconds)
                duration = max(0.0, effective_end - self._session_start_time)

                if duration > 0.0:
                    slice_result = TimeSlice(
                        app=self._current_window.exe_name,
                        title=self._current_window.title,
                        start_time=self._session_start_time,
                        end_time=effective_end,
                        duration_sec=round(duration, 2),
                        idle_subtracted=round(idle_seconds, 2),
                        hostname=self.hostname,
                    )

            # Invalidate active session while user remains away
            self._session_start_time = None
            self._current_window = None
            logger.info(
                "AFK transition triggered (idle: %.1fs >= threshold: %.1fs). Subtracted %.1fs idle.",
                idle_seconds,
                self.afk_threshold,
                idle_seconds,
            )
            return [slice_result] if (return_as_list and slice_result) else ([] if return_as_list else slice_result)

        # -------------------------------------------------------------
        # Transition 2: AFK -> ACTIVE (User Returned)
        # -------------------------------------------------------------
        if self.status == AFKStatus.AFK and idle_seconds < self.afk_threshold:
            self.status = AFKStatus.ACTIVE
            self._current_window = window
            self._session_start_time = now
            self._last_active_time = now
            logger.info("User returned from AFK (idle: %.1fs). Resuming tracking.", idle_seconds)
            return [] if return_as_list else None

        # -------------------------------------------------------------
        # State: User remains AFK
        # -------------------------------------------------------------
        if self.status == AFKStatus.AFK:
            return [] if return_as_list else None

        # -------------------------------------------------------------
        # State: User remains ACTIVE (idle < threshold)
        # -------------------------------------------------------------
        self._last_active_time = now - idle_seconds

        # Check for window or document title change
        window_changed = False
        if window is not None:
            if self._current_window is None:
                window_changed = True
            else:
                window_changed = (
                    self._current_window.exe_name != window.exe_name
                    or self._current_window.title != window.title
                )

        if window_changed:
            if self._current_window and self._session_start_time is not None:
                duration = max(0.0, now - self._session_start_time)
                if duration > 0.0:
                    slice_result = TimeSlice(
                        app=self._current_window.exe_name,
                        title=self._current_window.title,
                        start_time=self._session_start_time,
                        end_time=now,
                        duration_sec=round(duration, 2),
                        idle_subtracted=0.0,
                        hostname=self.hostname,
                    )
            # Begin new window session
            self._current_window = window
            self._session_start_time = now
            return [slice_result] if (return_as_list and slice_result) else ([] if return_as_list else slice_result)

        return [] if return_as_list else None

    def flush(self, now: Optional[float] = None) -> Optional[TimeSlice]:
        """Flushes any currently open active session (e.g. on daemon shutdown)."""
        ts = now if now is not None else time.time()
        if (
            self.status == AFKStatus.ACTIVE
            and self._current_window is not None
            and self._session_start_time is not None
        ):
            duration = max(0.0, ts - self._session_start_time)
            if duration > 0.0:
                slice_to_emit = TimeSlice(
                    app=self._current_window.exe_name,
                    title=self._current_window.title,
                    start_time=self._session_start_time,
                    end_time=ts,
                    duration_sec=round(duration, 2),
                    idle_subtracted=0.0,
                    hostname=self.hostname,
                )
                self._session_start_time = None
                self._current_window = None
                return slice_to_emit
        return None
