"""
tests/test_m4_adversarial.py - Empirical Adversarial Verification Suite for Milestone 4.

Covers:
1. Continuous Work & AFK Boundary Dynamics (3300 ticks with micro-pauses 30s, 60s, 180s, 290s; 5m AFK strict reset; Pomodoro edge cases).
2. Multi-Machine Hours Aggregation & Zero-VRAM Bypass (PC-WORK + PC-HOME across yesterday and today; IT & EN templates; creative_detected and low_vram < 2ms zero-VRAM guarantee).
3. Quick-Bar Two-Tier Submission & Offline Resilience (IPC online submission; offline fallback persistence to tasks.json + TASKS.md mirror; zero data loss).
4. Tier 4 Scenario 4 (Morning Routine to Evening Wrap-up E2E lifecycle integration).
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from neko_bot.briefing.generator import BriefingData, BriefingGenerator, BriefingTriggerManager
from neko_bot.briefing.timers import BreakAlert, ContinuousWorkMonitor, PomodoroSession
from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.core.daemon import DaemonServer
from neko_bot.core.resource_monitor import ResourceMonitor
from neko_bot.notifications.queue import NotificationItem, NotificationQueue
from neko_bot.notifications.toast import ToastDispatcher
from neko_bot.task.models import TaskPriority
from neko_bot.task.store import TaskStore
from neko_bot.tracker.aggregator import TimeAggregator
from neko_bot.ui.quick_bar import QuickBar, QuickBarResult, submit_command
from tests.conftest import get_free_port


# ==============================================================================
# Challenge 1: Continuous Work & AFK Boundary Dynamics
# ==============================================================================
class TestContinuousWorkAndAFKBoundaries(unittest.TestCase):
    """Stress tests continuous work accumulation, micro-pause invariance, and AFK reset boundaries."""

    def setUp(self) -> None:
        self.timer = ContinuousWorkMonitor(break_threshold_minutes=55, afk_reset_minutes=5, language="it")

    def test_55m_active_with_varying_micro_pauses(self) -> None:
        """
        Simulates 55 minutes (3300 ticks) of active work interleaved with micro-pauses:
        - 10 min active (600s)
        - 30s micro-pause (is_afk=True) -> verify counter paused, not reset
        - 15 min active (900s)
        - 60s micro-pause (is_afk=True) -> verify counter paused, not reset
        - 15 min active (900s)
        - 180s micro-pause (is_afk=True) -> verify counter paused, not reset
        - 10 min active (600s)
        - 290s micro-pause (is_afk=True) -> verify counter paused, not reset (290s < 300s)
        - 5 min active (300s) -> reaching exactly 3300s active work -> verify alert triggers!
        """
        sim_time = 100000.0

        # Segment 1: 600 ticks active
        for _ in range(600):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=False, idle_seconds=0, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 600.0)

        # Micro-pause 1: 30s AFK
        for _ in range(30):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=True, idle_seconds=30, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 600.0, "30s pause must NOT reset active counter")

        # Segment 2: 900 ticks active
        for _ in range(900):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=False, idle_seconds=0, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 1500.0)

        # Micro-pause 2: 60s AFK
        for _ in range(60):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=True, idle_seconds=60, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 1500.0, "60s pause must NOT reset active counter")

        # Segment 3: 900 ticks active
        for _ in range(900):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=False, idle_seconds=0, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 2400.0)

        # Micro-pause 3: 180s AFK
        for _ in range(180):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=True, idle_seconds=180, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 2400.0, "180s pause must NOT reset active counter")

        # Segment 4: 600 ticks active
        for _ in range(600):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=False, idle_seconds=0, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 3000.0)

        # Micro-pause 4: 290s AFK (just below 300s threshold)
        for _ in range(290):
            sim_time += 1.0
            alert = self.timer.tick(is_afk=True, idle_seconds=290, delta_seconds=1.0, now=sim_time)
            self.assertIsNone(alert)
        self.assertEqual(self.timer.continuous_active_seconds, 3000.0, "290s pause must NOT reset active counter")

        # Final segment: 300 ticks active (reaching exactly 3300s = 55m)
        alert_fired = None
        for i in range(1, 301):
            sim_time += 1.0
            res = self.timer.tick(is_afk=False, idle_seconds=0, delta_seconds=1.0, now=sim_time)
            if i < 300:
                self.assertIsNone(res, f"Alert must not fire at active second {3000 + i}")
            else:
                alert_fired = res

        # Verify alert triggered at exactly 3300 active seconds
        self.assertIsNotNone(alert_fired, "Break alert MUST trigger at exactly 55m (3300s) active work")
        self.assertEqual(self.timer.continuous_active_seconds, 3300.0)
        self.assertTrue(self.timer.alert_fired)
        self.assertIn("55 minuti", alert_fired.message)
        self.assertIn("Pausa Caffè", alert_fired.title)

        # Verify subsequent ticks do NOT re-fire duplicate alert
        sim_time += 1.0
        extra_alert = self.timer.tick(is_afk=False, idle_seconds=0, delta_seconds=1.0, now=sim_time)
        self.assertIsNone(extra_alert, "Continuous work timer must not spam alerts every tick")

    def test_afk_break_5m_strict_reset(self) -> None:
        """Verifies active counter resets strictly to 0.0 only when AFK reaches 300s."""
        sim_time = 200000.0
        # Work for 40 minutes (2400s)
        self.timer.tick(is_afk=False, idle_seconds=0, delta_seconds=2400.0, now=sim_time)
        self.assertEqual(self.timer.continuous_active_seconds, 2400.0)

        # 299 seconds of AFK: counter must remain 2400.0
        for _ in range(299):
            sim_time += 1.0
            self.timer.tick(is_afk=True, idle_seconds=299, delta_seconds=1.0, now=sim_time)
        self.assertEqual(self.timer.continuous_active_seconds, 2400.0, "299s of AFK must NOT reset counter")

        # Tick 300 of AFK: counter must reset strictly to 0.0
        sim_time += 1.0
        self.timer.tick(is_afk=True, idle_seconds=300, delta_seconds=1.0, now=sim_time)
        self.assertEqual(self.timer.continuous_active_seconds, 0.0, "300s (5m) of AFK MUST strictly reset counter to 0.0")
        self.assertFalse(self.timer.alert_fired)

    def test_pomodoro_transitions_and_edge_cases(self) -> None:
        """Stress-tests PomodoroSession state machine and edge conditions."""
        # 25/5 session
        session = self.timer.create_pomodoro_session(work_minutes=25, break_minutes=5)
        self.assertEqual(session.state, "work")
        self.assertEqual(session.remaining_seconds, 1500.0)

        # Tick 1499s: still work
        ev = session.tick(seconds=1499.0)
        self.assertIsNone(ev)
        self.assertEqual(session.state, "work")
        self.assertEqual(session.remaining_seconds, 1.0)

        # Tick 1s: transition to break
        ev = session.tick(seconds=1.0)
        self.assertEqual(ev, "work_completed")
        self.assertEqual(session.state, "break")
        self.assertEqual(session.remaining_seconds, 300.0)

        # Tick 300s: transition to completed
        ev = session.tick(seconds=300.0)
        self.assertEqual(ev, "break_completed")
        self.assertEqual(session.state, "completed")
        self.assertEqual(session.remaining_seconds, 0)

        # Extra ticks after completion: must be safe and return None
        ev = session.tick(seconds=10.0)
        self.assertIsNone(ev)
        self.assertEqual(session.state, "completed")

        # Custom 50/10 session
        s50 = self.timer.create_pomodoro_session(work_minutes=50, break_minutes=10)
        self.assertEqual(s50.remaining_seconds, 3000.0)
        s50.tick(seconds=3000.0)
        self.assertEqual(s50.state, "break")
        self.assertEqual(s50.remaining_seconds, 600.0)


# ==============================================================================
# Challenge 2: Multi-Machine Hours Aggregation & Zero-VRAM Bypass
# ==============================================================================
class TestMultiMachineHoursAggregationAndZeroVRAM(unittest.TestCase):
    """Verifies multi-machine hours aggregation, IT/EN templates, and zero-VRAM bypass under load."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_m4_agg_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.ensure_dirs()
        self.generator = BriefingGenerator(self.config)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_multi_machine_partitioned_logs_aggregation(self) -> None:
        """
        Creates mock partitioned logs for PC-WORK and PC-HOME across yesterday and today.
        Verifies exact aggregation across machines and projects.
        """
        fixed_now = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)
        today_str = "2026-10-23"
        yesterday_str = "2026-10-22"

        # Yesterday logs:
        # PC-WORK: 2h in CyberpunkCity (Unreal), 1h in ArchViz (3dsmax) -> 3h (10800s)
        log_work_yest = self.config.time_logs_dir / f"PC-WORK_{yesterday_str}.jsonl"
        with open(log_work_yest, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "UnrealEditor.exe", "project": "CyberpunkCity", "duration_sec": 7200}) + "\n")
            f.write(json.dumps({"app": "3dsmax.exe", "project": "ArchViz", "duration_sec": 3600}) + "\n")

        # PC-HOME: 1h in CyberpunkCity (Blender), 30m in CharacterRig (Blender) -> 1.5h (5400s)
        log_home_yest = self.config.time_logs_dir / f"PC-HOME_{yesterday_str}.jsonl"
        with open(log_home_yest, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "blender.exe", "project": "CyberpunkCity", "duration_sec": 3600}) + "\n")
            f.write(json.dumps({"app": "blender.exe", "project": "CharacterRig", "duration_sec": 1800}) + "\n")

        # Today logs:
        # PC-WORK: 2h 15m in ArchViz (3dsmax) -> 8100s
        log_work_today = self.config.time_logs_dir / f"PC-WORK_{today_str}.jsonl"
        with open(log_work_today, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "3dsmax.exe", "project": "ArchViz", "duration_sec": 8100}) + "\n")

        # Collect data
        data = self.generator.collect_data(reference_time=fixed_now)

        # Total yesterday: 10800 + 5400 = 16200s (4h 30m)
        self.assertEqual(data.yesterday_total_seconds, 16200.0)
        self.assertEqual(data.yesterday_total_human, "4h 30m")
        # Project rollup for yesterday: CyberpunkCity = 7200 + 3600 = 10800s (3h 00m)
        self.assertEqual(data.yesterday_by_project.get("CyberpunkCity"), "3h 00m")
        self.assertEqual(data.yesterday_by_project.get("ArchViz"), "1h 00m")
        self.assertEqual(data.yesterday_by_project.get("CharacterRig"), "0h 30m")

        # Total today: 8100s (2h 15m)
        self.assertEqual(data.today_total_seconds, 8100.0)
        self.assertEqual(data.today_total_human, "2h 15m")
        self.assertEqual(data.today_by_project.get("ArchViz"), "2h 15m")

        # Machines list contains both hosts
        self.assertEqual(data.machines, ["PC-HOME", "PC-WORK"])

    def test_italian_and_english_briefing_templates(self) -> None:
        """Verifies deterministic template generation in both Italian and English."""
        fixed_now = datetime(2026, 10, 23, 9, 30, 0, tzinfo=timezone.utc)
        yesterday_str = "2026-10-22"
        log = self.config.time_logs_dir / f"PC-WORK_{yesterday_str}.jsonl"
        with open(log, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "Photoshop.exe", "project": "TexturePack", "duration_sec": 7200}) + "\n")

        # Add tasks
        store = TaskStore(self.config.tasks_file, self.config.tasks_md_file, config=self.config)
        store.add_task(title="Refactor lighting", project="TexturePack", priority=TaskPriority.HIGH)

        # 1. Italian Morning Briefing
        res_it = self.generator.generate_morning_briefing(force_deterministic=True, language="it", reference_time=fixed_now)
        self.assertTrue(res_it.text.startswith("Buongiorno!"))
        self.assertIn("Ieri hai lavorato 2h 00m (#TexturePack: 2h 00m).", res_it.text)
        self.assertIn("Refactor lighting", res_it.text)
        self.assertIn("#TexturePack", res_it.text)
        self.assertLess(len(res_it.short_body), 120)

        # 2. English Morning Briefing
        res_en = self.generator.generate_morning_briefing(force_deterministic=True, language="en", reference_time=fixed_now)
        self.assertTrue(res_en.text.startswith("Good morning!"))
        self.assertIn("Yesterday you worked 2h 00m (#TexturePack: 2h 00m).", res_en.text)
        self.assertIn("Refactor lighting", res_en.text)

        # 3. Italian Evening Wrap-up
        res_eve_it = self.generator.generate_evening_wrapup(force_deterministic=True, language="it", reference_time=fixed_now)
        self.assertTrue(res_eve_it.text.startswith("Buonasera!"))
        self.assertIn("Task attivi rimanenti: 1.", res_eve_it.text)

        # 4. English Evening Wrap-up
        res_eve_en = self.generator.generate_evening_wrapup(force_deterministic=True, language="en", reference_time=fixed_now)
        self.assertTrue(res_eve_en.text.startswith("Good evening!"))
        self.assertIn("Remaining active tasks: 1.", res_eve_en.text)

    def test_zero_vram_bypass_modes(self) -> None:
        """
        Tests creative_detected and low_vram modes:
        Verifies that Ollama is strictly bypassed and response is generated with zero_vram_preserved = True.
        """
        for mode in ("creative_detected", "low_vram"):
            with patch.object(ResourceMonitor, "get_resource_mode", return_value=mode):
                with patch("neko_bot.core.router.OllamaRouter.route_request") as mock_route:
                    start_ts = time.perf_counter()
                    res = self.generator.generate(language="it", force_tier1=False)
                    elapsed_ms = (time.perf_counter() - start_ts) * 1000

                    self.assertFalse(mock_route.called, f"Ollama must NOT be invoked in {mode} mode")
                    self.assertTrue(res.zero_vram_preserved, f"zero_vram_preserved must be True in {mode}")
                    self.assertEqual(res.generator_mode, "deterministic_creative_bypass")
                    self.assertEqual(res.tier, "tier1_deterministic")
                    self.assertEqual(res.resource_mode, mode)
                    self.assertLess(elapsed_ms, 20.0, f"Deterministic response must complete in < 20ms (was {elapsed_ms:.2f}ms)")


# ==============================================================================
# Challenge 3: Quick-Bar Two-Tier Submission & Offline Resilience
# ==============================================================================
class TestQuickBarTwoTierSubmissionAndOfflineResilience(unittest.TestCase):
    """Stress tests Quick-Bar launcher IPC submission, offline store fallback, and TASKS.md mirroring."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_quickbar_adv_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_task_submission_when_daemon_is_running(self) -> None:
        """Simulates task submission when daemon is running (IPC HTTP)."""
        server = DaemonServer(self.config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        time.sleep(0.25)

        try:
            prompt = "Preparare mockup interfaccia #archviz !alta entro le 18:00"
            result = submit_command(prompt, config=self.config, port=self.config.port)

            self.assertTrue(result.success)
            self.assertEqual(result.mode, "ipc")
            self.assertIsNotNone(result.task)
            self.assertEqual(result.task.get("project"), "archviz")
            self.assertEqual(result.task.get("priority"), "high")

            # Check persistence in tasks.json
            store = TaskStore(self.config.tasks_file, self.config.tasks_md_file, config=self.config)
            tasks = store.list_tasks()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].project, "archviz")

            # Check TASKS.md mirror
            self.assertTrue(self.config.tasks_md_file.exists())
            with open(self.config.tasks_md_file, "r", encoding="utf-8") as f:
                md_content = f.read()
            self.assertIn("Preparare mockup interfaccia", md_content)
            self.assertIn("archviz", md_content)
        finally:
            server.trigger_stop()
            time.sleep(0.1)

    def test_task_submission_when_daemon_is_dead_offline_fallback(self) -> None:
        """
        Simulates task submission when daemon is dead / unreachable.
        Verifies task is persisted to tasks.json and mirrored to TASKS.md with zero data loss.
        """
        dead_port = self.config.port  # Daemon is NOT started on this port

        prompt = "Bake lightmaps per CyberpunkCity #unreal !media"
        result = submit_command(prompt, config=self.config, port=dead_port)

        self.assertTrue(result.success)
        self.assertEqual(result.mode, "fallback", "Must use fallback when daemon is unreachable")
        self.assertIsNotNone(result.task)
        self.assertEqual(result.task.get("title"), "Bake lightmaps per CyberpunkCity")
        self.assertEqual(result.task.get("project"), "unreal")

        # Verify tasks.json
        self.assertTrue(self.config.tasks_file.exists())
        store = TaskStore(self.config.tasks_file, self.config.tasks_md_file, config=self.config)
        tasks = store.list_tasks()
        self.assertEqual(len(tasks), 1)
        persisted = tasks[0]
        self.assertEqual(persisted.title, "Bake lightmaps per CyberpunkCity")
        self.assertEqual(persisted.project, "unreal")
        self.assertTrue(persisted.metadata.get("offline_fallback"))

        # Verify TASKS.md mirror
        self.assertTrue(self.config.tasks_md_file.exists())
        with open(self.config.tasks_md_file, "r", encoding="utf-8") as f:
            md_content = f.read()
        self.assertIn("Bake lightmaps per CyberpunkCity", md_content)
        self.assertIn("unreal", md_content)

    def test_quickbar_input_edge_cases(self) -> None:
        """Verifies quick-bar resilience on empty strings, quotes, and Italian accents."""
        # Whitespace
        res_empty = submit_command("   \t  \n  ", config=self.config)
        self.assertFalse(res_empty.success)
        self.assertEqual(res_empty.mode, "none")

        # Special characters & quotes
        prompt = 'Aggiornare texture "Legno_Rovere" e testare l\'illuminazione #design !bassa'
        res = submit_command(prompt, config=self.config)
        self.assertTrue(res.success)
        self.assertIn("Legno_Rovere", res.task.get("title", ""))


# ==============================================================================
# Challenge 4: Tier 4 Scenario 4 (Morning Routine to Evening Wrap-up)
# ==============================================================================
class TestScenario4FullLifecycleAdversarial(unittest.TestCase):
    """End-to-end full lifecycle simulation of Scenario 4."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_scenario4_adv_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scenario4_complete_lifecycle(self) -> None:
        """
        Executes Scenario 4 end-to-end:
        1. Daemon startup: Morning routine fires -> generates briefing -> Toast & JSON queue.
        2. AsciiNeko polls status & notifications queue -> marks read.
        3. User opens Quick-Bar launcher -> submits task in Italian -> task added to store.
        4. User works for 55m active minutes in 3ds Max -> continuous break alert triggers.
        5. User takes 5m AFK break -> continuous work counter resets.
        6. Evening wrap-up triggers -> daily hours rollup & summary delivered.
        """
        queue = NotificationQueue(self.config.notifications_file, self.config.status_file)
        toast = ToastDispatcher(mock_mode=True)
        briefing_gen = BriefingGenerator(self.config)
        trigger_mgr = BriefingTriggerManager(config=self.config)
        work_timer = ContinuousWorkMonitor(break_threshold_minutes=55, afk_reset_minutes=5)

        # ----------------------------------------------------------------------
        # Phase 1: Daemon Startup & Morning Routine
        # ----------------------------------------------------------------------
        self.assertTrue(trigger_mgr.should_trigger())
        briefing = briefing_gen.generate_morning_briefing(force_deterministic=True)
        self.assertTrue(briefing.text.startswith("Buongiorno!"))

        # Deliver to Toast and JSON Queue
        toast.send_toast(briefing.title, briefing.short_body, category="briefing")
        q_item = queue.enqueue(briefing.title, briefing.text, category="briefing")
        trigger_mgr.mark_triggered()

        self.assertEqual(len(toast.get_mock_dispatched()), 1)
        self.assertEqual(len(queue.list_notifications(unread_only=True)), 1)
        self.assertFalse(trigger_mgr.should_trigger(), "Should not trigger briefing again today")

        # ----------------------------------------------------------------------
        # Phase 2: AsciiNeko Polling & Acknowledgment
        # ----------------------------------------------------------------------
        unread = queue.list_notifications(unread_only=True)
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0].title, briefing.title)
        queue.mark_as_read(unread[0].id)
        self.assertEqual(len(queue.list_notifications(unread_only=True)), 0)

        # ----------------------------------------------------------------------
        # Phase 3: Quick Launcher Task Ingestion
        # ----------------------------------------------------------------------
        sub_res = submit_command(
            "Finire modello sedia Mies van der Rohe #archviz !alta entro le 17:00",
            config=self.config,
        )
        self.assertTrue(sub_res.success)
        self.assertEqual(sub_res.task.get("project"), "archviz")

        # Check task persistence
        store = TaskStore(self.config.tasks_file, self.config.tasks_md_file, config=self.config)
        tasks = store.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].priority.value, "high")

        # ----------------------------------------------------------------------
        # Phase 4: Continuous Work Session (55 minutes active work)
        # ----------------------------------------------------------------------
        now = time.time()
        # 55m active work
        alert = work_timer.tick(is_afk=False, idle_seconds=0, delta_seconds=3300, now=now + 3300)
        self.assertIsNotNone(alert, "55m continuous work must trigger alert")

        # Deliver break alert
        toast.send_toast(alert.title, alert.message, category="timer")
        queue.enqueue(alert.title, alert.message, category="timer", level="alert")

        self.assertEqual(len(toast.get_mock_dispatched()), 2)
        self.assertEqual(len(queue.list_notifications(unread_only=True)), 1)

        # ----------------------------------------------------------------------
        # Phase 5: AFK Break & Counter Reset
        # ----------------------------------------------------------------------
        work_timer.tick(is_afk=True, idle_seconds=300, delta_seconds=300, now=now + 3600)
        self.assertEqual(work_timer.continuous_active_seconds, 0.0, "5m AFK must reset continuous counter to 0")

        # ----------------------------------------------------------------------
        # Phase 6: Evening Wrap-up
        # ----------------------------------------------------------------------
        evening = briefing_gen.generate_evening_wrapup(force_deterministic=True)
        self.assertTrue(evening.text.startswith("Buonasera!"))
        toast.send_toast(evening.title, evening.short_body, category="briefing")
        queue.enqueue(evening.title, evening.text, category="briefing")

        self.assertEqual(len(toast.get_mock_dispatched()), 3)
        self.assertEqual(len(queue.list_notifications()), 3)


if __name__ == "__main__":
    unittest.main()
