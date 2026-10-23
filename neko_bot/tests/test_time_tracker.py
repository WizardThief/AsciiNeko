"""
tests/test_time_tracker.py - Milestone 3 & Acceptance Criteria AC3 Test Harness.

Comprehensive test suite verifying:
- Feature F12: Win32 Foreground Window & Process Tracking (with mocks for non-Windows / CI).
- Feature F13: Win32 AFK Detector & Retroactive Idle Subtraction.
- Feature F14: Universal Window-Title Regex Heuristics across Creative & Dev Apps.
- Feature F15: Customizable tracking_rules.json Loading & Overriding.
- Feature F16: Machine-Partitioned Logging to time_logs/<HOSTNAME>_<DATE>.jsonl.
- Feature F17: Time Aggregation Engine across Multi-Machine Logs.
- CLI Time Commands (today, week, project, report) & neko status --json Integration.
- Tier 4 Scenario 3: Real-World Multiverse Creative Workflow & AFK Filtering.
"""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from neko_bot.cli import build_parser, main
from neko_bot.core.config import Config
from neko_bot.tracker.aggregator import AggregatedReport, TimeAggregator, format_duration, get_today_tracked_seconds
from neko_bot.tracker.logger import TimeLogger, sanitize_hostname, sanitize_title
from neko_bot.tracker.rules import RulesEngine, ClassificationResult
from neko_bot.tracker.afk import AFKManager, AFKStatus, TimeSlice
from neko_bot.tracker.win32 import (
    MockWin32Provider,
    WindowState,
    calculate_idle_seconds,
    mock_win32_scope,
    get_win32_provider,
    set_win32_provider,
)


# ==============================================================================
# Suite 1: Win32 Wrappers & Mock Strategies (F12)
# ==============================================================================
class TestWin32WrappersAndMocks(unittest.TestCase):
    """Verifies Win32 ctypes wrappers, wide string buffers, and non-Windows mocks."""

    def test_mock_foreground_window_polling(self) -> None:
        """Verifies foreground window retrieval via MockWin32Provider."""
        provider = MockWin32Provider()
        provider.set_foreground_window(
            hwnd=12345,
            title="CyberpunkCity - Unreal Editor",
            exe_name="UnrealEditor.exe",
            exe_path="C:\\UE5\\UnrealEditor.exe",
            idle_seconds=5.0,
        )
        state = provider.get_foreground_window_state()
        self.assertIsNotNone(state)
        self.assertEqual(state.hwnd, 12345)
        self.assertEqual(state.title, "CyberpunkCity - Unreal Editor")
        self.assertEqual(state.exe_name, "UnrealEditor.exe")
        self.assertEqual(state.idle_seconds, 5.0)

    def test_idle_time_calculation_and_dword_rollover(self) -> None:
        """Verifies GetLastInputInfo tick subtraction with DWORD 49.7 day rollover safety."""
        # Normal condition: tick=100_000, last_input=80_000 -> 20.0s
        self.assertAlmostEqual(calculate_idle_seconds(100000, 80000), 20.0)

        # DWORD rollover condition: tick=5000 (after wrap), last_input=0xFFFFFF00
        # (5000 - 0xFFFFFF00) & 0xFFFFFFFF = 5256 ticks -> 5.256s
        idle = calculate_idle_seconds(5000, 0xFFFFFF00)
        self.assertGreater(idle, 5.0)
        self.assertLess(idle, 6.0)

    def test_utf16_wide_string_character_support(self) -> None:
        """Verifies Unicode wide strings (accents, Japanese, emoticons) in window titles."""
        provider = MockWin32Provider()
        unicode_title = "Progetto Città Scura — 3ds Max 2024 [日本語] ★"
        provider.set_foreground_window(101, unicode_title, "3dsmax.exe")
        state = provider.get_foreground_window_state()
        self.assertIsNotNone(state)
        self.assertEqual(state.title, unicode_title)

    def test_64bit_handle_safety(self) -> None:
        """Verifies 64-bit window handle values (> 2^31 - 1) operate without truncation."""
        provider = MockWin32Provider()
        large_hwnd = 0xFFFF800012345678
        provider.set_foreground_window(hwnd=large_hwnd, title="Test", exe_name="test.exe")
        state = provider.get_foreground_window_state()
        self.assertIsNotNone(state)
        self.assertEqual(state.hwnd, large_hwnd)

    def test_mock_win32_scope_context_manager(self) -> None:
        """Verifies mock_win32_scope replaces and restores global provider cleanly."""
        mock_p = MockWin32Provider()
        mock_p.set_foreground_window(999, "Scoped Window", "scoped.exe", idle_seconds=42.0)
        with mock_win32_scope(mock_p) as p:
            current = get_win32_provider()
            self.assertEqual(current.get_foreground_window(), 999)
            self.assertEqual(current.get_idle_seconds(), 42.0)


# ==============================================================================
# Suite 2: AFK Detector & Retroactive Idle Subtraction (F13)
# ==============================================================================
class TestAfkDetectorAndIdleSubtraction(unittest.TestCase):
    """Verifies AFK detection state transitions and retroactive idle subtraction."""

    def test_active_state_below_threshold(self) -> None:
        """Verifies that idle time below threshold (180s) maintains ACTIVE state."""
        mgr = AFKManager(afk_threshold_sec=180.0)
        self.assertFalse(mgr.is_afk)
        mgr.update(idle_seconds=30.0)
        self.assertFalse(mgr.is_afk)
        mgr.update(idle_seconds=179.0)
        self.assertFalse(mgr.is_afk)

    def test_transition_to_afk_and_retroactive_subtraction(self) -> None:
        """
        Verifies that when idle reaches threshold (e.g., 200s), AFK is triggered
        and the full 200s idle duration is retroactively subtracted from active work.
        """
        mgr = AFKManager(afk_threshold_sec=180.0)
        mgr.start_session("UnrealEditor.exe", "CyberpunkCity - Unreal Editor")
        time_slices = mgr.update(idle_seconds=10.0, elapsed_session_sec=600.0)
        self.assertEqual(len(time_slices), 0)

        # User walks away, idle reaches 200s at elapsed 800s
        time_slices = mgr.update(idle_seconds=200.0, elapsed_session_sec=800.0)
        self.assertTrue(mgr.is_afk)
        self.assertEqual(len(time_slices), 1)

        slice_record = time_slices[0]
        # Active duration must be 800 - 200 = 600s; idle_subtracted must be 200s
        self.assertAlmostEqual(slice_record.duration_sec, 600.0, delta=1.0)
        self.assertAlmostEqual(slice_record.idle_subtracted, 200.0, delta=1.0)
        self.assertEqual(slice_record.app, "UnrealEditor.exe")

    def test_resume_from_afk(self) -> None:
        """Verifies state returns to ACTIVE when input resumes (idle drops to 0)."""
        mgr = AFKManager(afk_threshold_sec=180.0)
        mgr.update(idle_seconds=200.0)
        self.assertTrue(mgr.is_afk)

        # Mouse moved -> idle = 0.5s
        mgr.update(idle_seconds=0.5)
        self.assertFalse(mgr.is_afk)

    def test_window_switch_emits_slice(self) -> None:
        """Verifies window switch emits slice for previous window and begins new session."""
        mgr = AFKManager(afk_threshold=180.0)
        w1 = WindowState(1, "CyberpunkCity - Unreal Editor", 100, "UnrealEditor.exe", "")
        w2 = WindowState(2, "hero.blend - Blender", 200, "blender.exe", "")

        # Tick 1: start at t=1000
        slice1 = mgr.update(now=1000.0, window=w1, idle_seconds=0.0)
        self.assertIsNone(slice1)

        # Tick 2: switch to w2 at t=1300
        slice2 = mgr.update(now=1300.0, window=w2, idle_seconds=0.0)
        self.assertIsNotNone(slice2)
        self.assertEqual(slice2.app, "UnrealEditor.exe")
        self.assertEqual(slice2.duration_sec, 300.0)

        # Flush at t=1500
        final_slice = mgr.flush(now=1500.0)
        self.assertIsNotNone(final_slice)
        self.assertEqual(final_slice.app, "blender.exe")
        self.assertEqual(final_slice.duration_sec, 200.0)


# ==============================================================================
# Suite 3: Window-Title Regex Heuristics (F14)
# ==============================================================================
class TestWindowRegexHeuristics(unittest.TestCase):
    """Verifies regex classification across creative, developer, and general applications."""

    def setUp(self) -> None:
        self.engine = RulesEngine(tracking_rules_path=None)  # Use built-in defaults

    def test_unreal_engine_heuristics(self) -> None:
        res = self.engine.classify("UnrealEditor.exe", "CyberpunkCity - Unreal Editor")
        self.assertEqual(res.project, "CyberpunkCity")
        self.assertEqual(res.category, "creative")

        res2 = self.engine.classify("UnrealEditor.exe", "SpaceCombat (64-bit Development) - Unreal Editor")
        self.assertEqual(res2.project, "SpaceCombat")
        self.assertEqual(res2.category, "creative")

    def test_blender_heuristics(self) -> None:
        res = self.engine.classify("blender.exe", "C:\\Work\\SciFi\\mech_rig.blend - Blender 4.2")
        self.assertEqual(res.project, "mech_rig")
        self.assertEqual(res.category, "creative")

        res2 = self.engine.classify("blender.exe", "[character_sculpt.blend] - Blender")
        self.assertEqual(res2.project, "character_sculpt")
        self.assertEqual(res2.category, "creative")

    def test_photoshop_heuristics(self) -> None:
        res = self.engine.classify("Photoshop.exe", "concept_art_v03.psd @ 66.7% (RGB/8#) *")
        self.assertEqual(res.project, "concept_art_v03")
        self.assertEqual(res.category, "creative")

        res2 = self.engine.classify("Photoshop.exe", "banner_matte.psb @ 100% (Layer 1, RGB/16)")
        self.assertEqual(res2.project, "banner_matte")
        self.assertEqual(res2.category, "creative")

    def test_3dsmax_heuristics(self) -> None:
        res = self.engine.classify("3dsmax.exe", "Corridor_Lighting.max - Autodesk 3ds Max 2024")
        self.assertEqual(res.project, "Corridor_Lighting")
        self.assertEqual(res.category, "creative")

    def test_premiere_and_after_effects(self) -> None:
        res = self.engine.classify("Adobe Premiere Pro.exe", "Adobe Premiere Pro 2024 - D:\\Edits\\Trailer_Cut.prproj")
        self.assertEqual(res.project, "Trailer_Cut")
        self.assertEqual(res.category, "creative")

        res2 = self.engine.classify("AfterFX.exe", "Adobe After Effects 2024 - D:\\VFX\\Explosion_Comp.aep")
        self.assertEqual(res2.project, "Explosion_Comp")
        self.assertEqual(res2.category, "creative")

    def test_vscode_and_visual_studio(self) -> None:
        res = self.engine.classify("Code.exe", "aggregator.py - neko_bot - Visual Studio Code")
        self.assertEqual(res.project, "neko_bot")
        self.assertEqual(res.category, "dev")

        res2 = self.engine.classify("devenv.exe", "NekoEngine - Microsoft Visual Studio (Administrator)")
        self.assertEqual(res2.project, "NekoEngine")
        self.assertEqual(res2.category, "dev")

    def test_browser_heuristics(self) -> None:
        res = self.engine.classify("chrome.exe", "Pull Request #42 · org/repo · GitHub - Google Chrome")
        self.assertEqual(res.project, "repo")
        self.assertEqual(res.category, "dev")

        res_yt = self.engine.classify("brave.exe", "Cyberpunk 2077 Soundtrack - YouTube - Brave")
        self.assertEqual(res_yt.category, "browsing")

    def test_ignored_windows(self) -> None:
        res = self.engine.classify("LockApp.exe", "Windows Default Lock Screen")
        self.assertTrue(res.is_ignored)

        res_blank = self.engine.classify("explorer.exe", "")
        self.assertTrue(res_blank.is_ignored)


# ==============================================================================
# Suite 4: Customizable tracking_rules.json (F15)
# ==============================================================================
class TestTrackingRulesCustomization(unittest.TestCase):
    """Verifies user custom rules overriding, ignored windows, and corrupt JSON recovery."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_rules_"))
        self.rules_file = self.temp_dir / "tracking_rules.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_custom_project_pattern(self) -> None:
        custom_config = {
            "version": 1,
            "custom_projects": [
                {
                    "name": "CustomClientProject",
                    "patterns": [r"ClientX_.*"],
                    "apps": ["blender.exe"],
                    "category": "creative",
                }
            ],
        }
        with open(self.rules_file, "w", encoding="utf-8") as f:
            json.dump(custom_config, f)

        engine = RulesEngine(tracking_rules_path=self.rules_file)
        res = engine.classify("blender.exe", "ClientX_Model_v01.blend - Blender")
        self.assertEqual(res.project, "CustomClientProject")
        self.assertEqual(res.category, "creative")

    def test_corrupt_tracking_rules_recovers_to_defaults(self) -> None:
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write("{invalid json syntax,}")

        engine = RulesEngine(tracking_rules_path=self.rules_file)
        # Built-in rules should still work without crash
        res = engine.classify("UnrealEditor.exe", "CyberpunkCity - Unreal Editor")
        self.assertEqual(res.project, "CyberpunkCity")

    def test_starter_rules_file_creation(self) -> None:
        new_path = self.temp_dir / "subdir" / "tracking_rules.json"
        engine = RulesEngine(tracking_rules_path=new_path)
        self.assertTrue(new_path.exists())
        with open(new_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("version"), 1)


# ==============================================================================
# Suite 5: Partitioned Logging (F16)
# ==============================================================================
class TestPartitionedLogging(unittest.TestCase):
    """Verifies machine-partitioned append-only JSONL logging and schema validation."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_log_"))
        self.logs_dir = self.temp_dir / "time_logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_partitioned_filename_per_host(self) -> None:
        logger_work = TimeLogger(logs_dir=self.logs_dir, hostname="PC-WORK")
        path_work = logger_work.log_record(
            start_time="2026-10-23T10:00:00Z",
            end_time="2026-10-23T10:30:00Z",
            app="UnrealEditor.exe",
            title="CyberpunkCity - Unreal Editor",
            project="CyberpunkCity",
            duration_sec=1800.0,
            category="creative",
        )
        self.assertEqual(path_work.name, "PC-WORK_2026-10-23.jsonl")

        logger_home = TimeLogger(logs_dir=self.logs_dir, hostname="PC-HOME")
        path_home = logger_home.log_record(
            start_time="2026-10-23T11:00:00Z",
            end_time="2026-10-23T11:15:00Z",
            app="blender.exe",
            title="hero.blend - Blender",
            project="hero",
            duration_sec=900.0,
            category="creative",
        )
        self.assertEqual(path_home.name, "PC-HOME_2026-10-23.jsonl")

        # Confirm two separate disjoint files exist
        self.assertTrue((self.logs_dir / "PC-WORK_2026-10-23.jsonl").exists())
        self.assertTrue((self.logs_dir / "PC-HOME_2026-10-23.jsonl").exists())

    def test_single_line_newline_sanitization(self) -> None:
        logger_work = TimeLogger(logs_dir=self.logs_dir, hostname="PC-WORK")
        path = logger_work.log_record(
            start_time="2026-10-23T10:00:00Z",
            end_time="2026-10-23T10:30:00Z",
            app="test.exe",
            title="Line 1\nLine 2\r\nLine 3",
            project="TestProject",
            duration_sec=100.0,
            category="general",
        )
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertNotIn("\n", record["title"])


# ==============================================================================
# Suite 6: Time Aggregation Engine across Multi-Machine Logs (F17)
# ==============================================================================
class TestTimeAggregatorEngine(unittest.TestCase):
    """Verifies multi-machine rollups, date ranges, and breakdown calculations."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_agg_"))
        self.logs_dir = self.temp_dir / "time_logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_multi_machine_same_day_aggregation(self) -> None:
        today_str = datetime.now().strftime("%Y-%m-%d")

        # Machine A logs: 3600s on CyberpunkCity, 1800s on neko_bot
        log_a = self.logs_dir / f"PC-WORK_{today_str}.jsonl"
        with open(log_a, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": f"{today_str}T10:00:00Z",
                "start_time": f"{today_str}T09:00:00Z",
                "end_time": f"{today_str}T10:00:00Z",
                "app": "UnrealEditor.exe",
                "title": "CyberpunkCity - Unreal Editor",
                "project": "CyberpunkCity",
                "duration_sec": 3600.0,
                "category": "creative",
                "hostname": "PC-WORK",
                "idle_subtracted": 0.0,
            }) + "\n")
            f.write(json.dumps({
                "timestamp": f"{today_str}T10:30:00Z",
                "start_time": f"{today_str}T10:00:00Z",
                "end_time": f"{today_str}T10:30:00Z",
                "app": "Code.exe",
                "title": "main.py - neko_bot - Visual Studio Code",
                "project": "neko_bot",
                "duration_sec": 1800.0,
                "category": "dev",
                "hostname": "PC-WORK",
                "idle_subtracted": 0.0,
            }) + "\n")

        # Machine B logs: 5400s on CyberpunkCity
        log_b = self.logs_dir / f"PC-HOME_{today_str}.jsonl"
        with open(log_b, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": f"{today_str}T18:00:00Z",
                "start_time": f"{today_str}T16:30:00Z",
                "end_time": f"{today_str}T18:00:00Z",
                "app": "Photoshop.exe",
                "title": "concept.psd @ 100% *",
                "project": "CyberpunkCity",
                "duration_sec": 5400.0,
                "category": "creative",
                "hostname": "PC-HOME",
                "idle_subtracted": 0.0,
            }) + "\n")

        agg = TimeAggregator(self.logs_dir)
        report = agg.aggregate_today()

        self.assertAlmostEqual(report.total_seconds, 10800.0)  # 3 hours total
        self.assertEqual(report.total_human, "3h 00m")
        self.assertAlmostEqual(report.by_project["CyberpunkCity"], 9000.0)  # 2.5 hours
        self.assertAlmostEqual(report.by_project["neko_bot"], 1800.0)
        self.assertIn("PC-WORK", report.machines)
        self.assertIn("PC-HOME", report.machines)

    def test_duration_formatting(self) -> None:
        self.assertEqual(format_duration(0), "0h 00m")
        self.assertEqual(format_duration(3600), "1h 00m")
        self.assertEqual(format_duration(8100), "2h 15m")
        self.assertEqual(format_duration(8130, include_seconds=True), "2h 15m 30s")


# ==============================================================================
# Suite 7: CLI Time Commands & Status Integration
# ==============================================================================
class TestCliTimeCommandsAndStatus(unittest.TestCase):
    """Verifies CLI time commands and neko status --json output payload."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_cli_time_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.ensure_dirs()

        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = self.config.time_logs_dir / f"PC-WORK_{today_str}.jsonl"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": f"{today_str}T10:00:00Z",
                "start_time": f"{today_str}T08:00:00Z",
                "end_time": f"{today_str}T10:00:00Z",
                "app": "UnrealEditor.exe",
                "title": "CyberpunkCity - Unreal Editor",
                "project": "CyberpunkCity",
                "duration_sec": 7200.0,
                "category": "creative",
                "hostname": "PC-WORK",
                "idle_subtracted": 0.0,
            }) + "\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_time_today_table_and_json(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["time", "today", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 0)
        out = buf.getvalue()
        self.assertIn("NEKO TIME TODAY", out)
        self.assertIn("CyberpunkCity", out)
        self.assertIn("2h 00m", out)

        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            ret_json = main(["time", "today", "--json", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret_json, 0)
        data = json.loads(buf_json.getvalue().strip())
        self.assertEqual(data["total_seconds"], 7200.0)
        self.assertEqual(data["by_project"]["CyberpunkCity"], 7200.0)

    def test_cli_time_week_and_project(self) -> None:
        buf_week = io.StringIO()
        with redirect_stdout(buf_week):
            ret = main(["time", "week", "--json", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 0)
        week_data = json.loads(buf_week.getvalue().strip())
        self.assertEqual(week_data["total_seconds"], 7200.0)

        buf_proj = io.StringIO()
        with redirect_stdout(buf_proj):
            ret_proj = main(["time", "project", "CyberpunkCity", "--json", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret_proj, 0)
        proj_data = json.loads(buf_proj.getvalue().strip())
        self.assertEqual(proj_data["project"], "CyberpunkCity")
        self.assertEqual(proj_data["total_seconds"], 7200.0)

    def test_status_json_integration(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["status", "--json", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 0)
        status_data = json.loads(buf.getvalue().strip())

        # Validate both status aliases are populated with exact 7200s
        self.assertEqual(status_data["today_tracked_seconds"], 7200)
        self.assertEqual(status_data["tracked_time_today_sec"], 7200)


# ==============================================================================
# Suite 8: Tier 4 Scenario 3 (Multiverse Creative Workflow & AFK Detection)
# ==============================================================================
class TestTier4Scenario3CreativeWorkflow(unittest.TestCase):
    """
    Tier 4 Real-World Scenario 3:
    Simulates a complex day of switching across Unreal, Photoshop, Blender, 3ds Max,
    VS Code, and Chrome, followed by a 20-minute AFK lunch break.
    Verifies that active seconds are accurately attributed and AFK idle is excluded.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_t4_s3_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_scenario_3_multiverse_tracking_and_afk_exclusion(self) -> None:
        today_str = datetime.now().strftime("%Y-%m-%d")
        logger = TimeLogger(logs_dir=self.config.time_logs_dir, hostname="PC-WORK")

        # 1. Unreal Engine: 30 minutes (1800s) on CyberpunkCity
        logger.log_record(
            start_time=f"{today_str}T09:00:00Z",
            end_time=f"{today_str}T09:30:00Z",
            app="UnrealEditor.exe",
            title="CyberpunkCity - Unreal Editor",
            project="CyberpunkCity",
            duration_sec=1800.0,
            category="creative",
            idle_subtracted=0.0,
        )

        # 2. Photoshop: 20 minutes (1200s) on concept_art
        logger.log_record(
            start_time=f"{today_str}T09:30:00Z",
            end_time=f"{today_str}T09:50:00Z",
            app="Photoshop.exe",
            title="concept_art.psd @ 100% (RGB/8) *",
            project="concept_art",
            duration_sec=1200.0,
            category="creative",
            idle_subtracted=0.0,
        )

        # 3. Blender: 45 minutes (2700s) on hero_asset
        logger.log_record(
            start_time=f"{today_str}T09:50:00Z",
            end_time=f"{today_str}T10:35:00Z",
            app="blender.exe",
            title="hero_asset.blend - Blender 4.2",
            project="hero_asset",
            duration_sec=2700.0,
            category="creative",
            idle_subtracted=0.0,
        )

        # 4. 3ds Max: 30 minutes (1800s) on mech_corridor
        logger.log_record(
            start_time=f"{today_str}T10:35:00Z",
            end_time=f"{today_str}T11:05:00Z",
            app="3dsmax.exe",
            title="mech_corridor.max - Autodesk 3ds Max 2024",
            project="mech_corridor",
            duration_sec=1800.0,
            category="creative",
            idle_subtracted=0.0,
        )

        # 5. VS Code: 40 minutes (2400s) on neko_bot
        logger.log_record(
            start_time=f"{today_str}T11:05:00Z",
            end_time=f"{today_str}T11:45:00Z",
            app="Code.exe",
            title="aggregator.py - neko_bot - Visual Studio Code",
            project="neko_bot",
            duration_sec=2400.0,
            category="dev",
            idle_subtracted=0.0,
        )

        # 6. Chrome: 15 minutes (900s) on repo
        logger.log_record(
            start_time=f"{today_str}T11:45:00Z",
            end_time=f"{today_str}T12:00:00Z",
            app="chrome.exe",
            title="Pull Request #42 · org/repo · GitHub - Google Chrome",
            project="repo",
            duration_sec=900.0,
            category="dev",
            idle_subtracted=0.0,
        )

        # 7. AFK LUNCH BREAK: 20 minutes (1200s idle).
        # In a real session, this slice resulted in 0 active seconds and 1200 idle subtracted.
        logger.log_record(
            start_time=f"{today_str}T12:00:00Z",
            end_time=f"{today_str}T12:20:00Z",
            app="UnrealEditor.exe",
            title="CyberpunkCity - Unreal Editor",
            project="CyberpunkCity",
            duration_sec=0.0,
            category="creative",
            idle_subtracted=1200.0,
        )

        # 8. Resume Unreal Engine: 25 minutes (1500s) on CyberpunkCity
        logger.log_record(
            start_time=f"{today_str}T12:20:00Z",
            end_time=f"{today_str}T12:45:00Z",
            app="UnrealEditor.exe",
            title="CyberpunkCity - Unreal Editor",
            project="CyberpunkCity",
            duration_sec=1500.0,
            category="creative",
            idle_subtracted=0.0,
        )

        # Run aggregation
        agg = TimeAggregator(self.config.time_logs_dir)
        report = agg.aggregate_today()

        # Total expected: 1800 + 1200 + 2700 + 1800 + 2400 + 900 + 0 + 1500 = 12300 seconds (3h 25m)
        self.assertAlmostEqual(report.total_seconds, 12300.0)
        self.assertEqual(report.total_human, "3h 25m")
        self.assertAlmostEqual(report.idle_subtracted_total, 1200.0)

        # Project breakdown
        self.assertAlmostEqual(report.by_project["CyberpunkCity"], 3300.0)  # 1800 + 1500 = 55m
        self.assertAlmostEqual(report.by_project["hero_asset"], 2700.0)
        self.assertAlmostEqual(report.by_project["neko_bot"], 2400.0)
        self.assertAlmostEqual(report.by_project["mech_corridor"], 1800.0)
        self.assertAlmostEqual(report.by_project["concept_art"], 1200.0)
        self.assertAlmostEqual(report.by_project["repo"], 900.0)

        # Category breakdown
        self.assertAlmostEqual(report.by_category["creative"], 9000.0)  # 3300 + 2700 + 1800 + 1200 = 2h 30m
        self.assertAlmostEqual(report.by_category["dev"], 3300.0)       # 2400 + 900 = 55m

        # Verify CLI output reflects exact metrics
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["time", "today", "--json", "--workspace", str(self.temp_dir)])
        self.assertEqual(ret, 0)
        cli_data = json.loads(buf.getvalue().strip())
        self.assertEqual(cli_data["total_seconds"], 12300.0)
        self.assertEqual(cli_data["total_human"], "3h 25m")


if __name__ == "__main__":
    unittest.main()
