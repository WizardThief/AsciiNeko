"""
tests/test_notifications_stress.py - Adversarial Stress & Edge-Case Testing for Notification Pipeline (F20, F21, AC4).

Author: challenger_m4_1 (Empirical Challenger)
Test Dimensions:
1. Concurrency & Queue Capacity Stress (150 parallel enqueues from 10 threads, peeking/reading/pruning, 50-item cap, eviction order).
2. Hostile String & Injection Resilience (XML tags, quotes, emojis, accents, 10k chars, PowerShell injection).
3. Latency & Non-Blocking SLA (< 2ms dispatch latency benchmark).
4. TTL Edge Cases (0s TTL, negative TTL, future timestamps, expired timestamps, corrupt timestamps).
5. Dual-Key Schema Integrity (notifications == items, legacy items-only load, status.json sync).
6. Dispatcher Fallback & History Buffer Stress.
"""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import random
import re
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import uuid

from neko_bot.core.config import atomic_write_json_file
from neko_bot.notifications.queue import (
    NotificationCategory,
    NotificationItem,
    NotificationLevel,
    NotificationQueue,
    now_utc_iso,
)
from neko_bot.notifications.toast import (
    MockToastDispatcher,
    NativeToastDispatcher,
    ToastRecord,
    escape_powershell_xml_text,
)


class TestConcurrencyAndQueueCapacityStress(unittest.TestCase):
    """Stress tests high concurrency and FIFO capacity enforcement on NotificationQueue."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_stress_queue_")
        self.queue_file = Path(self.temp_dir) / "notifications.json"
        self.status_file = Path(self.temp_dir) / "status.json"
        self.queue = NotificationQueue(
            file_path=self.queue_file,
            status_file=self.status_file,
            max_capacity=50,
            auto_save=True,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_concurrent_enqueue_150_items_from_10_threads(self) -> None:
        """
        Stress Test: 10 worker threads concurrently enqueue 15 items each (150 total),
        while 2 reader threads peek, 1 thread marks as read, and 1 thread prunes.
        Verifies:
        1. Strict 50-item FIFO cap is enforced without exceeding capacity.
        2. Zero file corruption or JSON decoding errors on disk.
        3. No deadlock or uncaught thread exceptions.
        """
        num_threads = 10
        items_per_thread = 15
        stop_event = threading.Event()
        errors = []

        def enqueue_worker(thread_idx: int) -> None:
            try:
                for i in range(items_per_thread):
                    self.queue.enqueue(
                        title=f"T{thread_idx}-Item-{i}",
                        message=f"Stress message from thread {thread_idx} seq {i}",
                        category=random.choice(["system", "briefing", "timer", "task"]),
                        level=random.choice(["info", "warning", "alert"]),
                        ttl_seconds=3600,
                    )
                    time.sleep(0.001)
            except Exception as e:
                errors.append(f"Enqueue thread {thread_idx} failed: {e}")

        def reader_worker() -> None:
            try:
                while not stop_event.is_set():
                    items = self.queue.peek(limit=10)
                    self.assertLessEqual(len(items), 50)
                    time.sleep(0.002)
            except Exception as e:
                errors.append(f"Reader worker failed: {e}")

        def marker_worker() -> None:
            try:
                while not stop_event.is_set():
                    items = self.queue.peek(unread_only=True, limit=5)
                    for item in items:
                        self.queue.mark_read(item.id)
                    time.sleep(0.003)
            except Exception as e:
                errors.append(f"Marker worker failed: {e}")

        def pruner_worker() -> None:
            try:
                while not stop_event.is_set():
                    self.queue.prune()
                    time.sleep(0.005)
            except Exception as e:
                errors.append(f"Pruner worker failed: {e}")

        # Start background helper threads
        readers = [threading.Thread(target=reader_worker) for _ in range(2)]
        marker = threading.Thread(target=marker_worker)
        pruner = threading.Thread(target=pruner_worker)

        for r in readers:
            r.daemon = True
            r.start()
        marker.daemon = True
        marker.start()
        pruner.daemon = True
        pruner.start()

        # Run 10 parallel enqueue threads
        threads = [
            threading.Thread(target=enqueue_worker, args=(t,))
            for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Allow helpers to finish
        stop_event.set()
        for r in readers:
            r.join(timeout=1.0)
        marker.join(timeout=1.0)
        pruner.join(timeout=1.0)

        # Assert no exceptions occurred in any thread
        self.assertEqual(len(errors), 0, f"Thread errors encountered: {errors}")

        # Verify in-memory capacity cap
        final_items = self.queue.list_notifications()
        self.assertLessEqual(
            len(final_items),
            50,
            f"Queue capacity exceeded: {len(final_items)} > 50",
        )

        # Verify on-disk file integrity
        self.assertTrue(self.queue_file.exists())
        with open(self.queue_file, "r", encoding="utf-8") as f:
            disk_data = json.load(f)

        self.assertIn("notifications", disk_data)
        self.assertIn("items", disk_data)
        self.assertLessEqual(len(disk_data["notifications"]), 50)
        self.assertEqual(len(disk_data["notifications"]), len(disk_data["items"]))
        self.assertEqual(disk_data["total"], len(final_items))

    def test_eviction_priority_read_items_evicted_before_unread(self) -> None:
        """
        Adversarial Test: When queue capacity (e.g. 5) overflows,
        read items MUST be evicted before unread items, even if read items are newer than unread items.
        """
        small_queue = NotificationQueue(
            file_path=self.queue_file,
            max_capacity=5,
            auto_save=True,
        )

        # Enqueue 4 items: U0, U1, U2, U3 (all unread)
        item0 = small_queue.enqueue("U0", "Unread 0")
        item1 = small_queue.enqueue("U1", "Unread 1")
        item2 = small_queue.enqueue("U2", "Unread 2")
        item3 = small_queue.enqueue("U3", "Unread 3")

        # Mark U1 as read
        small_queue.mark_read(item1.id)

        # Enqueue item 5: R4, and mark it as read
        item4 = small_queue.enqueue("R4", "Read 4")
        small_queue.mark_read(item4.id)

        # Current state: [U0(unread), U1(read), U2(unread), U3(unread), R4(read)] (total 5)
        self.assertEqual(len(small_queue.list_notifications()), 5)

        # Now enqueue 6th item: U5 (unread).
        # Expected: Capacity 5 exceeded. U1 is the oldest read item. U1 must be evicted!
        item5 = small_queue.enqueue("U5", "Unread 5")

        remaining = {i.id: i for i in small_queue.list_notifications()}
        self.assertEqual(len(remaining), 5)
        self.assertNotIn(item1.id, remaining, "Oldest read item U1 should have been evicted first!")
        self.assertIn(item0.id, remaining, "Oldest unread item U0 should NOT have been evicted!")
        self.assertIn(item2.id, remaining)
        self.assertIn(item3.id, remaining)
        self.assertIn(item4.id, remaining)
        self.assertIn(item5.id, remaining)

        # Now enqueue 7th item: U6 (unread).
        # Expected: R4 is now the only read item. R4 must be evicted before any unread item!
        item6 = small_queue.enqueue("U6", "Unread 6")
        remaining2 = {i.id: i for i in small_queue.list_notifications()}
        self.assertEqual(len(remaining2), 5)
        self.assertNotIn(item4.id, remaining2, "Remaining read item R4 should have been evicted!")
        self.assertIn(item0.id, remaining2, "Unread item U0 should still be preserved!")

        # Now enqueue 8th item: U7 (unread).
        # All items are unread [U0, U2, U3, U5, U6].
        # Now FIFO eviction must take oldest unread item: U0!
        item7 = small_queue.enqueue("U7", "Unread 7")
        remaining3 = {i.id: i for i in small_queue.list_notifications()}
        self.assertEqual(len(remaining3), 5)
        self.assertNotIn(item0.id, remaining3, "FIFO eviction should now evict oldest item U0!")
        self.assertIn(item2.id, remaining3)
        self.assertIn(item7.id, remaining3)


class TestHostileStringAndInjectionResilience(unittest.TestCase):
    """Stress tests hostile payloads, XML injections, PowerShell syntax, and special characters."""

    def setUp(self) -> None:
        self.dispatcher = NativeToastDispatcher()
        self.temp_dir = tempfile.mkdtemp(prefix="neko_stress_strings_")
        self.queue_file = Path(self.temp_dir) / "notifications.json"
        self.queue = NotificationQueue(file_path=self.queue_file)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hostile_xml_and_script_injection_payloads(self) -> None:
        """
        Adversarial payloads:
        - XML breakout tags: <script>, </toast>, <visual>, <text>
        - XML entities: &amp;, &lt;, &gt;, &quot;, &apos;
        - Quotes: double quotes, single quotes, triple quotes, backticks
        - Emojis and multi-byte UTF-8: 🐱, 🔥, 🚀, 💻, 🌸
        - Accented characters: è, à, ù, ì, ò, é, Ç
        - Shell injection: $PID, $env:COMPUTERNAME, $(Stop-Process), `r`n
        """
        hostile_payloads = [
            ('<script>alert("xss")</script>', 'Injection <toast><visual>crash</visual></toast>'),
            ('Alert: "Double" and \'Single\' Quotes', 'Quotes: """nested""" and \'\'\'raw\'\'\''),
            ('&amp; &lt; &gt; &quot; &apos;', 'Entities: &amp;amp; &euro; &copy;'),
            ('🐱 Neko Status: 🔥 Burning GPU', 'Emojis in body: 🚀 ✨ 💻 🎉 💤 🐾'),
            ('Attività di oggi: Caffè & Città', 'È arrivata l\'ora di fare una pausa! Però sì.'),
            ('$PID $env:USERNAME $(Get-Process)', 'PowerShell vars `$Title `n `r `0'),
            ('A' * 5000, 'B' * 10000),  # Extreme length strings (10,000 chars)
            ('', ''),  # Empty strings
            ('   \t\n\r   ', '   \n\t   '),  # Whitespace and newlines
        ]

        for idx, (title, message) in enumerate(hostile_payloads):
            with self.subTest(payload_idx=idx):
                # 1. Test PowerShell script generation
                script = self.dispatcher.build_powershell_script(title, message)
                self.assertIsInstance(script, str)
                self.assertIn("Windows.UI.Notifications.ToastNotificationManager", script)

                # Script generation should never raise an exception and should produce valid lines
                lines = script.strip().split("\n")
                self.assertGreaterEqual(len(lines), 5)

                # 2. Test Queue persistence with hostile strings
                item = self.queue.enqueue(title=title, message=message)
                self.queue.save()

                # Reload and verify round-trip persistence
                reloaded_queue = NotificationQueue(file_path=self.queue_file)
                reloaded_item = reloaded_queue.get(item.id)
                self.assertIsNotNone(reloaded_item)
                self.assertEqual(reloaded_item.title, title.strip())
                self.assertEqual(reloaded_item.message, message.strip())

    def test_powershell_xml_escaping_behavior(self) -> None:
        """
        Verifies entity escaping in escape_powershell_xml_text.
        Identifies potential double-escaping considerations with WinRT CreateTextNode.
        """
        raw = '<toast title="alert" & \'test\'>'
        escaped = escape_powershell_xml_text(raw)
        self.assertNotIn("<", escaped)
        self.assertNotIn(">", escaped)
        self.assertNotIn('"', escaped)
        self.assertNotIn("'", escaped)
        self.assertIn("&lt;", escaped)
        self.assertIn("&gt;", escaped)
        self.assertIn("&quot;", escaped)
        self.assertIn("&apos;", escaped)
        self.assertIn("&amp;", escaped)

    def test_winrt_text_node_raw_entity_preservation(self) -> None:
        """
        Verifies that WinRT CreateTextNode parameters receive raw, unescaped literal text
        via Base64 decoding, preventing double-escaping of quotes, ampersands, accents, and emojis.
        """
        test_cases = [
            # Quotes
            ('Alert: "Double" and \'Single\' Quotes', 'Quotes: """nested""" and \'\'\'raw\'\'\''),
            # Ampersands & XML entities
            ('&amp; &lt; &gt; &quot; &apos;', 'Entities: &amp;amp; &euro; &copy;'),
            # XML breakout tags
            ('<script>alert("xss")</script>', '<toast><visual>crash</visual></toast>'),
            # Italian Accents
            ('Attività di oggi: Caffè & Città', 'È arrivata l\'ora di fare una pausa! Però sì.'),
            # Emojis & multi-byte UTF-8
            ('🐱 Neko Status: 🔥 Burning GPU', 'Emojis in body: 🚀 ✨ 💻 🎉 💤 🐾'),
            # Shell syntax injection
            ('$PID $env:USERNAME $(Get-Process)', 'PowerShell vars `$Title `n `r `0'),
        ]

        for title, message in test_cases:
            script = self.dispatcher.build_powershell_script(title, message)
            # 1. Script must not contain double-escaped entities
            self.assertNotIn("&amp;quot;", script)
            self.assertNotIn("&amp;amp;", script)
            self.assertNotIn("&amp;lt;", script)
            self.assertNotIn("&quot;", script)
            self.assertNotIn("&apos;", script)

            # 2. Base64 payload must decode to the EXACT original string
            title_b64_match = re.search(r'\$Title\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]+)"\)\)', script)
            self.assertIsNotNone(title_b64_match)
            decoded_title = base64.b64decode(title_b64_match.group(1)).decode("utf-8")
            self.assertEqual(decoded_title, title)

            msg_b64_match = re.search(r'\$Message\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]+)"\)\)', script)
            self.assertIsNotNone(msg_b64_match)
            decoded_msg = base64.b64decode(msg_b64_match.group(1)).decode("utf-8")
            self.assertEqual(decoded_msg, message)

    def test_build_powershell_command_adversarial_hostile_strings(self) -> None:
        """
        Adversarial Test: Verifies that build_powershell_command() produces a strictly
        7-element command array with -EncodedCommand and ZERO trailing arguments
        for all extreme hostile strings, and that the UTF-16LE payload cleanly decodes
        and restores 100% of the input text via UTF-8 Base64 decoding with zero injection.
        """
        adversarial_cases = [
            # 1. Quotes and nested quoting
            ('Alert: "Double" and \'Single\' Quotes', 'Quotes: """nested""" and \'\'\'raw\'\'\' and `backticks`'),
            # 2. XML tags, breakouts, and XML entities
            ('<script>alert("xss")</script>', '<toast><visual><binding template="ToastGeneric"><text>crash</text></binding></visual></toast>'),
            ('&amp; &lt; &gt; &quot; &apos;', 'Entities: &amp;amp; &euro; &copy; &apos;&apos;'),
            # 3. Accented characters (Italian)
            ('Attività di oggi: Caffè & Città — Però è così!', 'Tè caldo, pietà, virtù, longevità: à è é ì ò ù À È É Ì Ò Ù'),
            # 4. Emojis and multi-byte UTF-8
            ('🐱 Neko Status: 🔥 Burning GPU', 'Emojis in body: 🚀 ✨ 💻 🎉 💤 🐾 🐱‍👤 🛡️ ⚔️'),
            # 5. Shell injection and subexpressions
            ('$PID $env:USERNAME $(Get-Process)', 'PowerShell vars `$Title `n `r `0; Stop-Process -Force; Remove-Item C:\\*'),
            # 6. Newlines and whitespace
            ("Multi-line\r\nTitle\nWith\tTabs", "Message\r\nLine 1\r\nLine 2\nLine 3\t\tEnd"),
            # 7. 10,000-character extreme length strings
            ('T' * 1000, 'M' * 10000),
            # 8. Empty strings
            ('', ''),
        ]

        for title, message in adversarial_cases:
            cmd = self.dispatcher.build_powershell_command(title, message)

            # Verification 1: Command array length and structure
            self.assertEqual(len(cmd), 7, f"cmd must have exactly 7 elements, got {len(cmd)}")
            self.assertEqual(cmd[0], "powershell.exe")
            self.assertEqual(cmd[1], "-NoProfile")
            self.assertEqual(cmd[2], "-NonInteractive")
            self.assertEqual(cmd[3], "-ExecutionPolicy")
            self.assertEqual(cmd[4], "Bypass")
            self.assertEqual(cmd[5], "-EncodedCommand")

            # Verification 2: Strictly NO trailing arguments
            trailing = cmd[cmd.index("-EncodedCommand") + 2:]
            self.assertEqual(len(trailing), 0, f"Found trailing arguments after -EncodedCommand: {trailing}")
            self.assertNotIn("-Title", cmd)
            self.assertNotIn("-Message", cmd)
            self.assertNotIn("-AppId", cmd)

            # Verification 3: UTF-16LE decoding of the payload
            encoded_arg = cmd[6]
            raw_script_bytes = base64.b64decode(encoded_arg)
            script = raw_script_bytes.decode("utf-16le")

            # Verification 4: Script contains WinRT API and no HTML entity double-escaping
            self.assertIn("Windows.UI.Notifications.ToastNotificationManager", script)
            self.assertIn("CreateTextNode($Title)", script)
            self.assertIn("CreateTextNode($Message)", script)
            self.assertNotIn("&quot;", script)
            self.assertNotIn("&amp;quot;", script)
            self.assertNotIn("&apos;", script)
            self.assertNotIn("&lt;", script)
            self.assertNotIn("&gt;", script)

            # Verification 5: 100% roundtrip text recovery via Base64 UTF-8
            title_match = re.search(
                r'\$Title\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]*)"\)\)',
                script,
            )
            msg_match = re.search(
                r'\$Message\s*=\s*\[System\.Text\.Encoding\]::UTF8\.GetString\(\[System\.Convert\]::FromBase64String\("([^"]*)"\)\)',
                script,
            )
            self.assertIsNotNone(title_match, "Failed to match $Title Base64 decode line")
            self.assertIsNotNone(msg_match, "Failed to match $Message Base64 decode line")

            decoded_title = base64.b64decode(title_match.group(1)).decode("utf-8")
            decoded_msg = base64.b64decode(msg_match.group(1)).decode("utf-8")
            self.assertEqual(decoded_title, title, f"Title mismatch: {decoded_title!r} != {title!r}")
            self.assertEqual(decoded_msg, message, f"Message mismatch: {decoded_msg!r} != {message!r}")


class TestLatencyAndNonBlockingSLA(unittest.TestCase):
    """Benchmarks dispatch latency to verify non-blocking < 2ms execution SLA."""

    def setUp(self) -> None:
        self.dispatcher = NativeToastDispatcher()

    def test_show_async_dispatch_latency_sla(self) -> None:
        """
        Benchmark: Calling show_async() 100 times must complete in < 2ms on average,
        guaranteeing the caller is never delayed by toast background processing.
        """
        latencies = []
        # Warm up executor
        with patch.object(self.dispatcher, "_execute_winrt_powershell", return_value=True):
            self.dispatcher.show_async("Warmup", "Warmup").result()

            for i in range(100):
                t0 = time.perf_counter()
                future = self.dispatcher.show_async(f"Title {i}", f"Message {i}")
                t1 = time.perf_counter()
                latencies.append((t1 - t0) * 1000.0)  # Convert to ms

        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)]

        # SLA checks
        self.assertLess(
            avg_latency,
            2.0,
            f"Average dispatch latency ({avg_latency:.3f}ms) exceeded 2ms SLA",
        )
        self.assertLess(
            p95_latency,
            5.0,
            f"P95 dispatch latency ({p95_latency:.3f}ms) exceeded 5ms limit",
        )

    def test_send_toast_async_dispatch_returns_instantly(self) -> None:
        """Verifies send_toast(async_dispatch=True) completes in < 2ms without blocking."""
        with patch.object(self.dispatcher, "_execute_winrt_powershell", return_value=True):
            t0 = time.perf_counter()
            res = self.dispatcher.send_toast("Title", "Body", async_dispatch=True)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertTrue(res)
            self.assertLess(elapsed_ms, 2.0, f"send_toast latency was {elapsed_ms:.3f}ms (>= 2ms)")


class TestTTLEdgeCases(unittest.TestCase):
    """Tests TTL expiration edge cases (0s, negative, future timestamps, corrupt)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_stress_ttl_")
        self.queue_file = Path(self.temp_dir) / "notifications.json"
        self.queue = NotificationQueue(file_path=self.queue_file)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ttl_zero_semantics(self) -> None:
        """
        Examines TTL=0 behavior:
        In queue.py: `if self.ttl_seconds <= 0: return False`.
        Verifies that TTL=0 treats the notification as non-expiring (persistent).
        """
        item = self.queue.enqueue("TTL Zero", "Should not expire", ttl_seconds=0)
        # Set timestamp to 30 days ago
        old_time = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        item.timestamp = old_time
        self.queue.save()

        pruned = self.queue.prune()
        self.assertEqual(pruned, 0, "TTL <= 0 items should not be pruned under current implementation")
        self.assertIsNotNone(self.queue.get(item.id))

    def test_ttl_negative_semantics(self) -> None:
        """Tests negative TTL (ttl_seconds < 0)."""
        item = self.queue.enqueue("Negative TTL", "Body", ttl_seconds=-60)
        self.assertFalse(item.is_expired())
        pruned = self.queue.prune()
        self.assertEqual(pruned, 0)
        self.assertIsNotNone(self.queue.get(item.id))

    def test_future_timestamp_not_expired(self) -> None:
        """Tests notification with timestamp set in the future."""
        item = self.queue.enqueue("Future Item", "Body", ttl_seconds=60)
        future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        item.timestamp = future_time
        self.queue.save()

        self.assertFalse(item.is_expired())
        pruned = self.queue.prune()
        self.assertEqual(pruned, 0)
        self.assertIsNotNone(self.queue.get(item.id))

    def test_expired_timestamp_pruned(self) -> None:
        """Tests notification that has exceeded its TTL is cleanly pruned."""
        item = self.queue.enqueue("Expiring Item", "Body", ttl_seconds=10)
        past_time = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat().replace("+00:00", "Z")
        item.timestamp = past_time
        self.queue.save()

        self.assertTrue(item.is_expired())
        pruned = self.queue.prune()
        self.assertEqual(pruned, 1)
        self.assertIsNone(self.queue.get(item.id))

    def test_malformed_timestamp_handled_gracefully(self) -> None:
        """Tests that a malformed/corrupt timestamp string does not crash is_expired()."""
        item = self.queue.enqueue("Bad Timestamp", "Body")
        item.timestamp = "INVALID_TIMESTAMP_STRING"
        self.assertFalse(item.is_expired())  # Caught by try-except


class TestDualKeySchemaIntegrity(unittest.TestCase):
    """Verifies dual-key compatibility ('notifications' and 'items') across all operations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_stress_schema_")
        self.queue_file = Path(self.temp_dir) / "notifications.json"
        self.status_file = Path(self.temp_dir) / "status.json"
        self.queue = NotificationQueue(
            file_path=self.queue_file,
            status_file=self.status_file,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_notifications_and_items_identical_and_synchronized(self) -> None:
        """Verifies both keys contain identical serialized items at all times."""
        for i in range(10):
            self.queue.enqueue(f"Item {i}", f"Message {i}", category="briefing", level="info")

        with open(self.queue_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("notifications", data)
        self.assertIn("items", data)
        self.assertEqual(data["notifications"], data["items"])
        self.assertEqual(len(data["notifications"]), 10)
        self.assertEqual(data["total"], 10)
        self.assertEqual(data["unread"], 10)

        # Mark some items read
        first_id = data["notifications"][0]["id"]
        self.queue.mark_read(first_id)

        with open(self.queue_file, "r", encoding="utf-8") as f:
            data2 = json.load(f)

        self.assertEqual(data2["notifications"], data2["items"])
        self.assertTrue(data2["notifications"][0]["read"])
        self.assertTrue(data2["items"][0]["read"])
        self.assertEqual(data2["unread"], 9)

    def test_backward_compatibility_load_items_only(self) -> None:
        """Verifies loading a legacy file containing only 'items' (without 'notifications') works seamlessly."""
        legacy_payload = {
            "items": [
                {
                    "id": "legacy_01",
                    "title": "Legacy Item",
                    "message": "Created by old daemon",
                    "timestamp": now_utc_iso(),
                    "category": "system",
                    "level": "info",
                    "read": False,
                    "ttl_seconds": 86400,
                }
            ],
            "total": 1,
            "unread": 1,
        }
        with open(self.queue_file, "w", encoding="utf-8") as f:
            json.dump(legacy_payload, f)

        # Load queue
        q = NotificationQueue(file_path=self.queue_file)
        items = q.list_notifications()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "legacy_01")
        self.assertEqual(items[0].title, "Legacy Item")

        # Save and verify dual keys are now written
        q.save()
        with open(self.queue_file, "r", encoding="utf-8") as f:
            upgraded = json.load(f)
        self.assertIn("notifications", upgraded)
        self.assertIn("items", upgraded)
        self.assertEqual(upgraded["notifications"][0]["id"], "legacy_01")

    def test_status_json_sync_integrity(self) -> None:
        """Verifies sync_to_status() updates status.json with the full active queue."""
        initial_status = {
            "daemon_health": "running",
            "active_app": "Blender",
            "notification_queue": [],
        }
        atomic_write_json_file(self.status_file, initial_status)

        self.queue.enqueue("Sync Test", "Body for status sync")
        self.queue.sync_to_status()

        with open(self.status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)

        self.assertEqual(status_data["daemon_health"], "running")
        self.assertIn("notification_queue", status_data)
        self.assertEqual(len(status_data["notification_queue"]), 1)
        self.assertEqual(status_data["notification_queue"][0]["title"], "Sync Test")


class TestDispatcherFallbackAndBufferStress(unittest.TestCase):
    """Stress tests NativeToastDispatcher fallbacks, history bounds, and mock dispatcher."""

    def test_fallback_transitions_on_repeated_failures(self) -> None:
        """Verifies clean fallback transitions without memory leaks or uncaught exceptions."""
        dispatcher = NativeToastDispatcher(max_history=10)

        with patch.object(dispatcher, "_execute_winrt_powershell", return_value=False):
            for i in range(25):
                ok = dispatcher.show(f"F{i}", f"Message {i}", timeout=1.0)
                self.assertTrue(ok)
                self.assertEqual(dispatcher.last_fallback_level, "logger")

            # Check history limit
            history = dispatcher.get_history()
            self.assertEqual(len(history), 10)
            self.assertEqual(history[-1].title, "F24")
            self.assertEqual(history[-1].status, "delivered_fallback")

    def test_mock_dispatcher_thread_safety(self) -> None:
        """Verifies MockToastDispatcher handles high concurrent dispatches safely."""
        mock = MockToastDispatcher()
        num_threads = 8
        items_per_thread = 20

        def worker(tid: int) -> None:
            for i in range(items_per_thread):
                mock.show(f"Mock-{tid}-{i}", f"Msg {i}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(mock.notifications), num_threads * items_per_thread)
        self.assertEqual(len(mock.calls), num_threads * items_per_thread)
        self.assertEqual(len(mock.get_history()), num_threads * items_per_thread)


if __name__ == "__main__":
    unittest.main()
