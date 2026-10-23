"""
tests/test_notifications_briefing.py - Acceptance Criteria AC4 & Milestone 4 Test Harness.

Comprehensive test suite verifying:
1. Feature F20: PowerShell WinRT Toast Notification Dispatcher & Fallback Hierarchy.
2. Feature F21: Local JSON Notification Queue (.neko/notifications.json) Persistence, Pruning, & Status Sync.
3. Feature F18: Proactive Startup & Daily Briefings (Deterministic Template + Zero-VRAM Bypass under Load).
4. Feature F19: Continuous Work Timers (55m Alert Trigger, 5m AFK Reset, Sub-threshold Pause).
5. Feature F22: Quick-Bar Launcher Command Dispatch (Headless, IPC Online, Fallback Offline).
6. Tier 4 Scenario 4: Morning Routine to Evening Wrap-up (Full Lifecycle E2E).
"""

import base64
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.request

from neko_bot.cli import main
from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.core.daemon import DaemonServer, DaemonStatusManager
from neko_bot.core.resource_monitor import ResourceMonitor
from neko_bot.notifications.queue import NotificationItem, NotificationQueue
from neko_bot.notifications.toast import ToastDispatcher, get_toast_dispatcher
from neko_bot.briefing.generator import BriefingGenerator
from neko_bot.briefing.timers import ContinuousWorkTimer
from neko_bot.ui.quick_bar import QuickBar, QuickBarResult, submit_command
from tests.conftest import get_free_port, temporary_workspace


# ==============================================================================
# Suite 1: Toast Notification Dispatcher (F20)
# ==============================================================================
class TestToastNotificationDispatcher(unittest.TestCase):
    """Verifies WinRT PowerShell toast script generation, parameter safety, non-blocking async execution, and fallbacks."""

    def setUp(self) -> None:
        self.dispatcher = ToastDispatcher()

    def test_powershell_winrt_toast_script_generation(self) -> None:
        """Verifies build_powershell_script generates Base64 UTF-8 decoded script syntax."""
        script = self.dispatcher.build_powershell_script(
            title="Neko Alert",
            message="Test message",
            app_id="neko_bot",
        )
        self.assertIn("Windows.UI.Notifications.ToastNotificationManager", script)
        self.assertIn("GetTemplateContent", script)
        self.assertIn("CreateTextNode($Title)", script)
        self.assertIn("CreateTextNode($Message)", script)
        self.assertIn("[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(", script)

        # Embedded Base64 strings decode to original values
        title_b64 = base64.b64encode("Neko Alert".encode("utf-8")).decode("ascii")
        msg_b64 = base64.b64encode("Test message".encode("utf-8")).decode("ascii")
        self.assertIn(f'FromBase64String("{title_b64}")', script)
        self.assertIn(f'FromBase64String("{msg_b64}")', script)

    def test_toast_xml_escaping(self) -> None:
        """Verifies special characters and quotes are safely handled without XML double-escaping (&quot;)."""
        raw_title = 'Alert: "Bake" & Render <Test>'
        raw_msg = "O'Reilly's render is 100% complete! 🐱"
        script = self.dispatcher.build_powershell_script(
            title=raw_title,
            message=raw_msg,
        )
        self.assertIn("Windows.UI.Notifications.ToastNotificationManager", script)
        self.assertIn("CreateTextNode($Title)", script)
        self.assertIn("CreateTextNode($Message)", script)

        # Ensure no XML double-escaping entities in the script
        self.assertNotIn("&quot;", script, "Double quotes must not be escaped to &quot;")
        self.assertNotIn("&apos;", script, "Single quotes must not be escaped to &apos;")
        self.assertNotIn("&amp;quot;", script, "Double-escaping &amp;quot; must not appear")
        self.assertNotIn("&lt;", script, "Angle brackets must not be pre-escaped to &lt;")
        self.assertNotIn("&gt;", script, "Angle brackets must not be pre-escaped to &gt;")

        # Extract and verify exact character recovery from Base64 decoded payloads
        match_title = re.search(
            r'\$Title\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]+)"\)\)',
            script,
        )
        match_msg = re.search(
            r'\$Message\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]+)"\)\)',
            script,
        )
        self.assertIsNotNone(match_title)
        self.assertIsNotNone(match_msg)
        decoded_title = base64.b64decode(match_title.group(1)).decode("utf-8")
        decoded_msg = base64.b64decode(match_msg.group(1)).decode("utf-8")
        self.assertEqual(decoded_title, raw_title)
        self.assertEqual(decoded_msg, raw_msg)

    @patch("subprocess.run")
    def test_powershell_command_no_trailing_parameters(self, mock_run: MagicMock) -> None:
        """
        Verifies the command line passed to subprocess.run has -EncodedCommand <base64>
        with NO trailing parameters whatsoever (-Title, -Message, -AppId, etc.).

        Prevents regression of PowerShell error:
        'A parameter cannot be found that matches parameter name Title'.
        """
        mock_run.return_value = MagicMock(returncode=0)

        with patch("sys.platform", "win32"):
            success = self.dispatcher._execute_winrt_powershell(
                title='Test "Title" & Accent è',
                message="Test Message with Emojis 🐱🔥",
                timeout=3.0,
            )

        self.assertTrue(success)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]

        # powershell.exe invocation and flags
        self.assertEqual(cmd[0], "powershell.exe")
        self.assertIn("-NoProfile", cmd)
        self.assertIn("-NonInteractive", cmd)
        self.assertIn("-ExecutionPolicy", cmd)
        self.assertIn("Bypass", cmd)

        # -EncodedCommand must be present
        self.assertIn("-EncodedCommand", cmd)
        encoded_idx = cmd.index("-EncodedCommand")

        # Argument immediately after -EncodedCommand must be the base64 string
        self.assertLess(encoded_idx + 1, len(cmd), "-EncodedCommand must have a base64 argument")
        encoded_arg = cmd[encoded_idx + 1]

        # Ensure encoded_arg is valid base64
        decoded_bytes = base64.b64decode(encoded_arg)
        self.assertGreater(len(decoded_bytes), 0)

        # Strictly NO trailing parameters after the encoded command argument
        trailing = cmd[encoded_idx + 2:]
        self.assertEqual(
            len(trailing),
            0,
            f"PowerShell does not accept script parameters after -EncodedCommand. Found trailing: {trailing}",
        )

        # Specifically verify neither -Title, -Message, nor -AppId appear anywhere in cmd
        self.assertNotIn("-Title", cmd)
        self.assertNotIn("-Message", cmd)
        self.assertNotIn("-AppId", cmd)

        # Verify execution flags (windowless CREATE_NO_WINDOW 0x08000000 on Windows)
        kwargs = mock_run.call_args[1]
        self.assertEqual(kwargs.get("creationflags"), 0x08000000)
        self.assertEqual(kwargs.get("timeout"), 3.0)

    @patch("subprocess.run")
    def test_decoded_powershell_script_valid_base64_decoding(self, mock_run: MagicMock) -> None:
        """
        Verifies the decoded PowerShell script contains valid Base64 decoding expressions
        for Title, Message, and AppId, and routes them directly to WinRT CreateTextNode.
        """
        mock_run.return_value = MagicMock(returncode=0)

        test_title = "Riepilogo Mattutino"
        test_message = "Hai lavorato 4 ore su Unreal Engine & ArchViz."
        expected_app_id = self.dispatcher.app_id

        with patch("sys.platform", "win32"):
            self.dispatcher._execute_winrt_powershell(title=test_title, message=test_message)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        encoded_arg = cmd[cmd.index("-EncodedCommand") + 1]

        # Decode UTF-16LE powershell script
        raw_bytes = base64.b64decode(encoded_arg)
        script = raw_bytes.decode("utf-16le")

        # WinRT API and Toast template syntax
        self.assertIn("Windows.UI.Notifications.ToastNotificationManager", script)
        self.assertIn("ToastTemplateType]::ToastText02", script)
        self.assertIn("CreateToastNotifier", script)
        self.assertIn("CreateTextNode", script)

        # Base64 UTF-8 decoding expressions
        self.assertIn("[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String(", script)

        # Verify extracted embedded Base64 payloads decode to original values
        title_b64 = base64.b64encode(test_title.encode("utf-8")).decode("ascii")
        msg_b64 = base64.b64encode(test_message.encode("utf-8")).decode("ascii")
        app_b64 = base64.b64encode(expected_app_id.encode("utf-8")).decode("ascii")

        self.assertIn(f'FromBase64String("{title_b64}")', script)
        self.assertIn(f'FromBase64String("{msg_b64}")', script)
        self.assertIn(f'FromBase64String("{app_b64}")', script)

        # Verify CreateTextNode attaches Title and Message variables
        self.assertIn("CreateTextNode($Title)", script)
        self.assertIn("CreateTextNode($Message)", script)

    @patch("subprocess.run")
    def test_character_preservation_quotes_ampersands_accents_emojis(self, mock_run: MagicMock) -> None:
        """
        Verifies quotes, ampersands, Italian accents, and emojis are transmitted
        with 100% fidelity without &quot;, &amp;, &lt;, &gt;, or &apos; entity corruption.
        """
        mock_run.return_value = MagicMock(returncode=0)

        complex_title = 'Notifica: "Bake" & <Render> d\'Autunno — Città 🐱'
        complex_message = 'È arrivato l\'alert per il caffè & tè! Priorità: alta (100% complete) 🔥🚀'

        with patch("sys.platform", "win32"):
            self.dispatcher._execute_winrt_powershell(
                title=complex_title,
                message=complex_message,
            )

        cmd = mock_run.call_args[0][0]
        encoded_arg = cmd[cmd.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded_arg).decode("utf-16le")

        # 1. No HTML/XML entity literals in the script
        self.assertNotIn("&quot;", script, "Quotes must not be double-escaped as &quot;")
        self.assertNotIn("&amp;", script, "Ampersands must not be double-escaped as &amp;")
        self.assertNotIn("&lt;", script, "Angle brackets must not be pre-escaped as &lt;")
        self.assertNotIn("&gt;", script, "Angle brackets must not be pre-escaped as &gt;")
        self.assertNotIn("&apos;", script, "Apostrophes must not be pre-escaped as &apos;")

        # 2. Extract Base64 payloads and verify exact character recovery
        match_title = re.search(
            r'\$Title\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]+)"\)\)',
            script,
        )
        match_msg = re.search(
            r'\$Message\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]+)"\)\)',
            script,
        )

        self.assertIsNotNone(match_title, "Script must assign $Title from Base64 string")
        self.assertIsNotNone(match_msg, "Script must assign $Message from Base64 string")

        extracted_title_b64 = match_title.group(1)
        extracted_msg_b64 = match_msg.group(1)

        decoded_title = base64.b64decode(extracted_title_b64).decode("utf-8")
        decoded_msg = base64.b64decode(extracted_msg_b64).decode("utf-8")

        self.assertEqual(decoded_title, complex_title, "Title with quotes, accents, ampersands, and emojis was corrupted")
        self.assertEqual(decoded_msg, complex_message, "Message with quotes, accents, ampersands, and emojis was corrupted")

    def test_toast_dispatch_non_blocking_async(self) -> None:
        """Verifies that send_toast() returns immediately (< 50ms) without blocking."""
        start = time.perf_counter()
        with patch.object(self.dispatcher, "_execute_winrt_powershell", return_value=True):
            res = self.dispatcher.send_toast("Title", "Body", async_dispatch=True)
            elapsed = time.perf_counter() - start
            self.assertLess(elapsed, 0.05, "Toast dispatch must be non-blocking and return in < 50ms")
            self.assertTrue(res)

    def test_toast_fallback_hierarchy(self) -> None:
        """Verifies zero crash when PowerShell raises an error; falls back gracefully to logger/in-memory."""
        with patch.object(self.dispatcher, "_execute_winrt_powershell", return_value=False):
            res = self.dispatcher.send_toast("Title", "Fallback test", async_dispatch=False)
            self.assertTrue(res, "Fallback level must handle missing PowerShell without crashing")
            self.assertEqual(self.dispatcher.last_fallback_level, "logger")

    def test_mock_toast_provider(self) -> None:
        """Verifies inspection of dispatched toasts via mock provider."""
        self.dispatcher.enable_mock_mode(True)
        self.dispatcher.send_toast("Mock Title", "Mock Body", category="briefing")
        dispatched = self.dispatcher.get_mock_dispatched()
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["title"], "Mock Title")
        self.assertEqual(dispatched[0]["category"], "briefing")


# ==============================================================================
# Suite 2: Local JSON Notification Queue (F21)
# ==============================================================================
class TestNotificationQueue(unittest.TestCase):
    """Verifies .neko/notifications.json schema, persistence, atomic writes, pruning, and status sync."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_queue_"))
        self.queue_file = self.temp_dir / "notifications.json"
        self.status_file = self.temp_dir / "status.json"
        self.queue = NotificationQueue(file_path=self.queue_file, status_file=self.status_file)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_queue_persistence_and_schema(self) -> None:
        """Verifies enqueued items conform to AC4 schema and persist on disk."""
        item = self.queue.enqueue(
            title="Morning Briefing",
            message="Yesterday you worked 5h 30m.",
            category="briefing",
            level="info",
        )
        self.assertTrue(self.queue_file.exists())
        with open(self.queue_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("notifications", data)
        self.assertEqual(len(data["notifications"]), 1)
        stored = data["notifications"][0]
        self.assertEqual(stored["id"], item.id)
        self.assertEqual(stored["title"], "Morning Briefing")
        self.assertEqual(stored["category"], "briefing")
        self.assertEqual(stored["level"], "info")
        self.assertFalse(stored["read"])

    def test_atomic_write_durability(self) -> None:
        """Verifies atomic writes use os.replace and leave no stray temp files."""
        self.queue.enqueue("Test", "Message")
        temp_files = list(self.temp_dir.glob("*.tmp*"))
        self.assertEqual(len(temp_files), 0, "No leftover .tmp files after atomic write")

    def test_enqueue_and_list_unread(self) -> None:
        """Verifies filtering unread items."""
        i1 = self.queue.enqueue("Item 1", "Msg 1")
        i2 = self.queue.enqueue("Item 2", "Msg 2")
        self.assertEqual(len(self.queue.list_notifications(unread_only=True)), 2)
        self.queue.mark_as_read(i1.id)
        unread = self.queue.list_notifications(unread_only=True)
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0].id, i2.id)

    def test_mark_as_read(self) -> None:
        """Verifies mark_as_read updates read flag and persists."""
        item = self.queue.enqueue("Read Test", "Body")
        ok = self.queue.mark_as_read(item.id)
        self.assertTrue(ok)
        reloaded = self.queue.get(item.id)
        self.assertIsNotNone(reloaded)
        self.assertTrue(reloaded.read)

    def test_ttl_expiration_pruning(self) -> None:
        """Verifies pruning removes expired notifications."""
        item = self.queue.enqueue("Short Lived", "Expiring", ttl_seconds=1)
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        item.timestamp = old_time
        self.queue.save()

        pruned_count = self.queue.prune_expired()
        self.assertGreaterEqual(pruned_count, 1)
        self.assertIsNone(self.queue.get(item.id))

    def test_fifo_max_capacity_cap(self) -> None:
        """Verifies max capacity (50 items) is strictly enforced via FIFO."""
        for i in range(60):
            self.queue.enqueue(f"Notification {i}", f"Message {i}")
        all_items = self.queue.list_notifications()
        self.assertLessEqual(len(all_items), 50)
        self.assertEqual(all_items[-1].title, "Notification 59")

    def test_status_json_queue_synchronization(self) -> None:
        """Verifies .neko/status.json notification_queue array is kept synchronized."""
        atomic_write_json_file(self.status_file, {"daemon_health": "running", "notification_queue": []})
        self.queue.enqueue("Status Sync", "Checking status sync")
        self.queue.sync_to_status()
        with open(self.status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)
        self.assertEqual(len(status_data["notification_queue"]), 1)
        self.assertEqual(status_data["notification_queue"][0]["title"], "Status Sync")


# ==============================================================================
# Suite 3: Proactive Briefings & Generator (F18)
# ==============================================================================
class TestBriefingGenerator(unittest.TestCase):
    """Verifies deterministic Italian briefings, multi-machine rollups, and zero-VRAM bypass under load."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_briefing_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.ensure_dirs()
        self.generator = BriefingGenerator(self.config)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_morning_briefing_deterministic_italian_template(self) -> None:
        """Verifies instant (< 2ms) deterministic Italian morning briefing template."""
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        log_work = self.config.time_logs_dir / f"PC-WORK_{yesterday_str}.jsonl"
        with open(log_work, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "UnrealEditor.exe", "project": "CyberpunkCity", "duration_sec": 7200}) + "\n")

        start = time.perf_counter()
        briefing = self.generator.generate_morning_briefing(force_deterministic=True)
        elapsed = (time.perf_counter() - start) * 1000
        self.assertLess(elapsed, 15.0, "Deterministic briefing must generate in < 15ms")
        self.assertTrue(briefing.text.startswith(("Buongiorno!", "Buon pomeriggio!", "Buonasera!")))
        self.assertIn("Ieri hai lavorato", briefing.text)

    def test_morning_briefing_hours_rollup_multi_machine(self) -> None:
        """Verifies hours rollup aggregates across partitioned host files for yesterday."""
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        log_work = self.config.time_logs_dir / f"PC-WORK_{yesterday_str}.jsonl"
        log_home = self.config.time_logs_dir / f"PC-HOME_{yesterday_str}.jsonl"

        with open(log_work, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "UnrealEditor.exe", "project": "CyberpunkCity", "duration_sec": 7200}) + "\n")
        with open(log_home, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "blender.exe", "project": "MechaModel", "duration_sec": 3600}) + "\n")

        briefing = self.generator.generate_morning_briefing(force_deterministic=True)
        self.assertIn("3h 00m", briefing.text)

    def test_morning_briefing_top_task_inclusion(self) -> None:
        """Verifies top pending task is included in morning briefing text."""
        from neko_bot.task.models import TaskPriority
        from neko_bot.task.store import TaskStore
        store = TaskStore(self.config.tasks_file, self.config.tasks_md_file, config=self.config)
        store.add_task(title="Bake lightmaps", project="ArchViz", priority=TaskPriority.HIGH)

        briefing = self.generator.generate_morning_briefing(force_deterministic=True)
        self.assertIn("Bake lightmaps", briefing.text)
        self.assertIn("ArchViz", briefing.text)

    def test_zero_vram_bypass_under_creative_load(self) -> None:
        """Verifies that when creative mode is active, Ollama is strictly bypassed."""
        with patch.object(ResourceMonitor, "get_resource_mode", return_value="creative_detected"):
            with patch("neko_bot.core.router.OllamaRouter.route_prompt") as mock_route:
                briefing = self.generator.generate_morning_briefing(force_deterministic=False)
                self.assertFalse(mock_route.called, "Ollama must never be called when creative apps are active")
                self.assertEqual(briefing.generator_mode, "deterministic_creative_bypass")

    def test_evening_wrapup_briefing(self) -> None:
        """Verifies evening wrap-up summarizing today's active work time and tasks."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        log_file = self.config.time_logs_dir / f"PC-WORK_{today_str}.jsonl"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"app": "3dsmax.exe", "project": "ArchViz", "duration_sec": 7200}) + "\n")

        briefing = self.generator.generate_evening_wrapup(force_deterministic=True)
        self.assertIn("Buonasera!", briefing.text)
        self.assertIn("Oggi hai lavorato", briefing.text)


# ==============================================================================
# Suite 4: Continuous Work Timers (F19)
# ==============================================================================
class TestContinuousWorkTimers(unittest.TestCase):
    """Verifies 55m continuous work break reminder, 5m AFK counter reset, and sub-threshold pauses."""

    def setUp(self) -> None:
        self.timer = ContinuousWorkTimer(break_threshold_minutes=55, afk_reset_minutes=5)

    def test_continuous_work_55m_alert_trigger(self) -> None:
        """Verifies break alert triggers when continuous active time reaches 55 minutes (3300s)."""
        now = time.time()
        # 54 minutes active
        should_alert = self.timer.update(now=now, is_afk=False, idle_seconds=0, delta_seconds=3240)
        self.assertFalse(should_alert)

        # 55 minutes active
        should_alert = self.timer.update(now=now + 60, is_afk=False, idle_seconds=0, delta_seconds=60)
        self.assertTrue(should_alert, "Continuous work timer must trigger alert at 55 minutes")

    def test_sub_threshold_work_no_alert(self) -> None:
        """Verifies no break alert at 30 minutes."""
        now = time.time()
        should_alert = self.timer.update(now=now, is_afk=False, idle_seconds=0, delta_seconds=1800)
        self.assertFalse(should_alert)

    def test_afk_5m_resets_continuous_counter(self) -> None:
        """Verifies being AFK for >= 5 minutes (300s) resets continuous active work counter to 0."""
        now = time.time()
        # Work for 45 minutes
        self.timer.update(now=now, is_afk=False, idle_seconds=0, delta_seconds=2700)
        self.assertEqual(self.timer.continuous_active_seconds, 2700)

        # Go AFK for 5 minutes (300s)
        self.timer.update(now=now + 300, is_afk=True, idle_seconds=300, delta_seconds=300)
        self.assertEqual(self.timer.continuous_active_seconds, 0, "5m AFK must reset continuous work counter to 0")

    def test_afk_short_pause_preserves_counter(self) -> None:
        """Verifies short AFK pause (< 5m) preserves continuous work counter."""
        now = time.time()
        # Work for 30 minutes (1800s)
        self.timer.update(now=now, is_afk=False, idle_seconds=0, delta_seconds=1800)

        # Quick 1 minute (60s) water break
        self.timer.update(now=now + 60, is_afk=True, idle_seconds=60, delta_seconds=60)
        self.assertEqual(self.timer.continuous_active_seconds, 1800, "Short pause must not reset continuous counter")

    def test_pomodoro_focus_timer_intervals(self) -> None:
        """Verifies Pomodoro 25m work / 5m break interval transitions."""
        pomo = self.timer.create_pomodoro_session(work_minutes=25, break_minutes=5)
        self.assertEqual(pomo.state, "work")
        pomo.tick(seconds=1500)
        self.assertEqual(pomo.state, "break")


# ==============================================================================
# Suite 5: Quick-Bar Launcher Command Dispatch (F22)
# ==============================================================================
class TestQuickBarLauncher(unittest.TestCase):
    """Verifies headless quick-bar initialization, IPC dispatch, and offline store fallback."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_quickbar_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_quick_bar_headless_init_and_properties(self) -> None:
        """Verifies QuickBar can be instantiated in headless mode without display errors."""
        app = QuickBar(config=self.config, headless=True)
        self.assertTrue(app.headless)
        self.assertIsNone(app.root)

    def test_submit_command_ipc_online(self) -> None:
        """Verifies submit_command sends task via IPC POST /command when daemon is online."""
        server = DaemonServer(self.config)
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        time.sleep(0.2)  # Allow bind and start

        try:
            res = submit_command(
                "Refactor shader cache #unreal !alta domani",
                config=self.config,
                port=self.config.port,
            )
            self.assertTrue(res.success)
            self.assertEqual(res.mode, "ipc")
            self.assertIsNotNone(res.task)
            self.assertEqual(res.task.get("project"), "unreal")
        finally:
            server.trigger_stop()
            time.sleep(0.1)

    def test_submit_command_fallback_offline(self) -> None:
        """Verifies submit_command falls back to in-process TaskStore when daemon is offline."""
        res = submit_command(
            "Design HUD layout #cyberpunk !media",
            config=self.config,
            port=self.config.port,  # Offline port
        )
        self.assertTrue(res.success)
        self.assertEqual(res.mode, "fallback")
        self.assertIsNotNone(res.task)
        self.assertEqual(res.task.get("title"), "Design HUD layout")

        # Verify persisted on disk
        from neko_bot.task.store import TaskStore
        store = TaskStore(self.config.tasks_file, self.config.tasks_md_file, config=self.config)
        tasks = store.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Design HUD layout")

    def test_submit_command_empty_ignored(self) -> None:
        """Verifies empty or whitespace-only inputs are ignored without error."""
        res = submit_command("   ", config=self.config)
        self.assertFalse(res.success)
        self.assertEqual(res.mode, "none")

    def test_cli_quick_command_integration(self) -> None:
        """Verifies neko quick command parses correctly."""
        with patch("neko_bot.ui.quick_bar.QuickBar.run", return_value=0) as mock_run:
            ret = main(["quick", "--workspace", str(self.temp_dir)])
            self.assertEqual(ret, 0)
            self.assertTrue(mock_run.called)


# ==============================================================================
# Suite 6: Tier 4 Scenario 4: Morning Routine to Evening Wrap-up
# ==============================================================================
class TestScenario4MorningToEvening(unittest.TestCase):
    """Tier 4 Real-World Application Scenario 4: Full Day Routine to Evening Wrap-up."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="neko_test_scenario4_"))
        self.config = Config.load(root_dir=self.temp_dir)
        self.config.port = get_free_port()
        self.config.ensure_dirs()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_day_scenario_4_flow(self) -> None:
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
        timer = ContinuousWorkTimer(break_threshold_minutes=55, afk_reset_minutes=5)

        # ----------------------------------------------------------------------
        # Phase 1: Daemon Startup & Morning Routine
        # ----------------------------------------------------------------------
        briefing = briefing_gen.generate_morning_briefing(force_deterministic=True)
        self.assertTrue(briefing.text.startswith(("Buongiorno!", "Buon pomeriggio!", "Buonasera!")))

        # Deliver via dual notification pipeline
        toast.send_toast("Neko Morning Briefing", briefing.text, category="briefing")
        q_item = queue.enqueue("Morning Briefing", briefing.text, category="briefing")

        self.assertEqual(len(toast.get_mock_dispatched()), 1)
        self.assertEqual(len(queue.list_notifications(unread_only=True)), 1)

        # ----------------------------------------------------------------------
        # Phase 2: AsciiNeko Companion Polling & Acknowledgment
        # ----------------------------------------------------------------------
        unread_notifs = queue.list_notifications(unread_only=True)
        self.assertEqual(len(unread_notifs), 1)
        queue.mark_as_read(unread_notifs[0].id)
        self.assertEqual(len(queue.list_notifications(unread_only=True)), 0)

        # ----------------------------------------------------------------------
        # Phase 3: Quick Launcher Task Ingestion
        # ----------------------------------------------------------------------
        sub_res = submit_command(
            "Finire illuminazione scena #archviz !alta entro le 18:00",
            config=self.config,
            port=self.config.port,
        )
        self.assertTrue(sub_res.success)
        self.assertEqual(sub_res.task.get("project"), "archviz")

        # ----------------------------------------------------------------------
        # Phase 4: Continuous Work Session (55 minutes in 3ds Max)
        # ----------------------------------------------------------------------
        now = time.time()
        alert_needed = timer.update(now=now + 3300, is_afk=False, idle_seconds=0, delta_seconds=3300)
        self.assertTrue(alert_needed, "55 minutes of continuous work must trigger break alert")

        # Dispatch break alert
        break_msg = "Hai lavorato ininterrottamente per 55 minuti. Fai una pausa di 5 minuti!"
        toast.send_toast("Pausa Consigliata", break_msg, category="timer")
        queue.enqueue("Pausa Consigliata", break_msg, category="timer")

        self.assertEqual(len(toast.get_mock_dispatched()), 2)
        self.assertEqual(len(queue.list_notifications(unread_only=True)), 1)

        # ----------------------------------------------------------------------
        # Phase 5: AFK Break & Counter Reset
        # ----------------------------------------------------------------------
        timer.update(now=now + 3600, is_afk=True, idle_seconds=300, delta_seconds=300)
        self.assertEqual(timer.continuous_active_seconds, 0, "5m AFK must reset continuous work timer to 0")

        # ----------------------------------------------------------------------
        # Phase 6: Evening Wrap-up
        # ----------------------------------------------------------------------
        evening = briefing_gen.generate_evening_wrapup(force_deterministic=True)
        self.assertTrue(evening.text.startswith("Buonasera!"))
        toast.send_toast("Neko Evening Wrap-up", evening.text, category="briefing")
        queue.enqueue("Evening Wrap-up", evening.text, category="briefing")

        self.assertEqual(len(toast.get_mock_dispatched()), 3)
        self.assertEqual(len(queue.list_notifications()), 3)


if __name__ == "__main__":
    unittest.main()
