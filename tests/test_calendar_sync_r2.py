"""
tests/test_calendar_sync_r2.py
Unit and integration tests for Milestone 2 (Requirement R2):
- Calendar ID extraction from Google .ics URLs (extract_calendar_id) and &src= parameter appending in generate_gcal_url.
- X-WR-CALNAME parsing from RFC 5545 iCal text and get_sync_status() reporting.
- Multi-feed .ics support (ics_urls, ics_url, ical_urls) in calendar_config.json and update_config().
- Cache-busting (?t=<timestamp>) query parameters and Cache-Control headers on remote fetch.
- Bidirectional reconciliation (reconcile_events): matching by title/UID, moved event dates/titles update, deletion propagation, zero duplicates.
- Event CRUD methods (add_event, edit_event, delete_event).
- CompanionRequestHandler HTTP routes: POST /api/calendar/action (sync_now, add, edit, move, delete) and GET /api/calendar.
- SystemStateMonitor.get_snapshot integration: _extract_todo_deadlines helper, calendar_sync_status, calendar_events, and todo_deadlines.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

import bridge.ai_monitor as am
from bridge.ai_monitor import (
    CalendarManager,
    extract_todo_deadlines,
    CompanionRequestHandler,
    parse_due_date_iso,
)


class TestCalendarIDExtraction(unittest.TestCase):
    """Verifica estrazione ID Google Calendar e inclusione parametro &src= nei link generati."""

    def test_extract_calendar_id_standard_google_url(self):
        url = (
            "https://calendar.google.com/calendar/ical/"
            "4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3%40group.calendar.google.com"
            "/private-2ad9e3e0308425580be8a6f3a931f414/basic.ics"
        )
        extracted = CalendarManager.extract_calendar_id(url)
        self.assertEqual(
            extracted,
            "4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3@group.calendar.google.com",
        )

    def test_extract_calendar_id_user_primary(self):
        url = "https://calendar.google.com/calendar/ical/user%40gmail.com/public/basic.ics"
        extracted = CalendarManager.extract_calendar_id(url)
        self.assertEqual(extracted, "user@gmail.com")

    def test_extract_calendar_id_invalid_or_none(self):
        self.assertIsNone(CalendarManager.extract_calendar_id(None))
        self.assertIsNone(CalendarManager.extract_calendar_id(""))
        self.assertIsNone(CalendarManager.extract_calendar_id("https://example.com/feed.ics"))

    def test_generate_gcal_url_includes_src(self):
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = os.path.join(temp_dir, "calendar_config.json")
            events_path = os.path.join(temp_dir, "events.json")
            with patch.object(am, "CALENDAR_CONFIG_FILE", cfg_path), patch.object(am, "EVENTS_FILE", events_path):
                cm = CalendarManager()
                cm.config["ics_urls"] = [
                    "https://calendar.google.com/calendar/ical/4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3%40group.calendar.google.com/private-token/basic.ics"
                ]
                s_dt = datetime(2026, 10, 27, 10, 0, 0)
                e_dt = datetime(2026, 10, 27, 11, 0, 0)
                url = cm.generate_gcal_url("Riunione Intermarine", s_dt, e_dt)

                self.assertIn("https://calendar.google.com/calendar/render?action=TEMPLATE", url)
                self.assertIn("text=Riunione%20Intermarine", url)
                self.assertIn("dates=20261027T100000/20261027T110000", url)
                self.assertIn("src=4167fd8b29d2bc84512cacb66fb2a1c9a8fd196098d82647883195f6933e45a3", url)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestIcsParsingAndSyncStatus(unittest.TestCase):
    """Verifica parsing X-WR-CALNAME, unfolding RFC 5545 ed esposizione di get_sync_status()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.temp_dir, "calendar_config.json")
        self.events_path = os.path.join(self.temp_dir, "events.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_x_wr_calname_and_vevent(self):
        ics_content = (
            "BEGIN:VCALENDAR\r\n"
            "PRODID:-//Google Inc//Google Calendar 70.9054//EN\r\n"
            "VERSION:2.0\r\n"
            "CALSCALE:GREGORIAN\r\n"
            "METHOD:PUBLISH\r\n"
            "X-WR-CALNAME:Neko\r\n"
            "X-WR-TIMEZONE:Europe/Rome\r\n"
            "BEGIN:VEVENT\r\n"
            "DTSTART:20261027T100000Z\r\n"
            "DTEND:20261027T110000Z\r\n"
            "UID:event_uid_999@google.com\r\n"
            "SUMMARY:Revisione modello Nave\r\n"
            "LOCATION:Ufficio Design\r\n"
            "LAST-MODIFIED:20261027T090000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            cal_name, events = cm._parse_ics_text(ics_content)

            self.assertEqual(cal_name, "Neko")
            self.assertEqual(len(events), 1)
            ev = events[0]
            self.assertEqual(ev["id"], "event_uid_999@google.com")
            self.assertEqual(ev["uid"], "event_uid_999@google.com")
            self.assertEqual(ev["title"], "Revisione modello Nave")
            self.assertEqual(ev["location"], "Ufficio Design")
            self.assertEqual(ev["calendar_name"], "Neko")
            self.assertEqual(ev["source"], "google_ics")
            self.assertEqual(ev["last_modified"], "20261027T090000Z")

    def test_get_sync_status_fields(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            cm.config["ics_urls"] = ["https://calendar.google.com/feed.ics"]
            cm.last_fetch_ts = time.time()
            cm.calendar_names = ["Neko", "Primario"]
            cm.remote_events = [{"id": "ev1", "title": "Test"}]
            cm.last_error = ""

            status = cm.get_sync_status()
            self.assertTrue(status["synced"])
            self.assertEqual(status["calendar_names"], ["Neko", "Primario"])
            self.assertEqual(status["remote_count"], 1)
            self.assertTrue(bool(status["last_sync"]))
            self.assertEqual(status["error"], "")
            self.assertTrue(status["configured"])


class TestMultiFeedSupport(unittest.TestCase):
    """Verifica supporto multi-feed .ics (ics_urls, ics_url, ical_urls) e persistenza."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.temp_dir, "calendar_config.json")
        self.events_path = os.path.join(self.temp_dir, "events.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_update_config_multi_feed_list(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            urls = [
                "https://calendar.google.com/ical/primary/basic.ics",
                "https://calendar.google.com/ical/secondary/basic.ics",
            ]
            cm.update_config(ics_urls=urls)
            cfg = cm.get_config()

            self.assertEqual(cfg["ics_urls"], urls)
            self.assertEqual(cfg["ical_urls"], urls)

            # Check file persistence
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["ics_urls"], urls)

    def test_update_config_single_ics_url(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            cm.update_config(ics_url="https://calendar.google.com/ical/single/basic.ics")
            cfg = cm.get_config()
            self.assertIn("https://calendar.google.com/ical/single/basic.ics", cfg["ics_urls"])


class TestCacheBustingAndPolling(unittest.TestCase):
    """Verifica aggiunta query param ?t= e header Cache-Control durante il download di feed .ics."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.temp_dir, "calendar_config.json")
        self.events_path = os.path.join(self.temp_dir, "events.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("urllib.request.urlopen")
    def test_cache_busting_param_and_headers(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:TestCal\r\nEND:VCALENDAR\r\n"
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            cm.config["ics_urls"] = ["https://calendar.google.com/calendar/ical/test/basic.ics"]
            cm.refresh_remote_events(force=True)

            self.assertTrue(mock_urlopen.called)
            req = mock_urlopen.call_args[0][0]

            # Verify query param ?t= exists in URL
            self.assertIn("t=", req.full_url)
            # Verify headers
            headers = req.headers
            self.assertIn("no-cache", headers.get("Cache-control", "").lower())
            self.assertIn("no-store", headers.get("Cache-control", "").lower())
            self.assertIn("no-cache", headers.get("Pragma", "").lower())


class TestBidirectionalReconciliation(unittest.TestCase):
    """Verifica riconciliazione bidirezionale: match UID/titolo, aggiornamento date spostate, rimozione eliminati, zero duplicati."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.temp_dir, "calendar_config.json")
        self.events_path = os.path.join(self.temp_dir, "events.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reconcile_moved_event_by_title_match(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            # 1. Add local event at 10:00
            local_ev = cm.add_event("Mandare report Intermarine", datetime(2026, 10, 27, 10, 0, 0), duration_minutes=60)
            self.assertEqual(len(cm.local_events), 1)
            self.assertEqual(cm.local_events[0]["start_iso"], "2026-10-27T10:00:00")

            # 2. Simulate remote .ics feed moving the event to 14:00 with UID: uid_report_1
            cm.remote_events = [
                {
                    "id": "uid_report_1",
                    "uid": "uid_report_1",
                    "title": "Mandare report Intermarine",
                    "start_iso": "2026-10-27T14:00:00",
                    "end_iso": "2026-10-27T15:00:00",
                    "all_day": False,
                    "location": "Online",
                    "source": "google_ics",
                    "calendar_name": "Neko",
                    "last_modified": "20261027T110000Z",
                }
            ]
            cm.last_fetch_successful = True
            cm.last_error = ""

            # 3. Trigger reconciliation
            cm.reconcile_events()

            # Verify local event was updated to 14:00 and linked
            self.assertEqual(len(cm.local_events), 1)
            self.assertEqual(cm.local_events[0]["start_iso"], "2026-10-27T14:00:00")
            self.assertEqual(cm.local_events[0]["remote_id"], "uid_report_1")
            self.assertTrue(cm.local_events[0]["synced_with_remote"])

            # Verify get_upcoming_events returns exactly 1 entry (0 duplicates)
            upcoming = cm.get_upcoming_events(days_ahead=7)
            intermarine_events = [e for e in upcoming if "intermarine" in e.get("title", "").lower()]
            self.assertEqual(len(intermarine_events), 1)
            self.assertEqual(intermarine_events[0]["start_iso"], "2026-10-27T14:00:00")

    def test_reconcile_deleted_remote_event_removes_synced_local_copy(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            # Local event already synced
            cm.local_events = [
                {
                    "id": "loc_123",
                    "remote_id": "uid_report_1",
                    "title": "Mandare report Intermarine",
                    "start_iso": "2026-10-27T14:00:00",
                    "end_iso": "2026-10-27T15:00:00",
                    "all_day": False,
                    "location": "",
                    "source": "local",
                    "synced_with_remote": True,
                }
            ]
            # Remote feed is now empty (event deleted on Google Calendar)
            cm.remote_events = []
            cm.last_fetch_successful = True
            cm.last_error = ""

            cm.reconcile_events()

            # Verify synced local event was removed
            self.assertEqual(len(cm.local_events), 0)


class TestEventCRUDMethods(unittest.TestCase):
    """Verifica metodi CRUD di CalendarManager (add_event, edit_event, delete_event)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.temp_dir, "calendar_config.json")
        self.events_path = os.path.join(self.temp_dir, "events.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_edit_delete_lifecycle(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            # 1. Add event
            ev = cm.add_event("Call Cliente", datetime(2026, 11, 2, 15, 0, 0), duration_minutes=45, location="Teams")
            self.assertIsNotNone(ev)
            ev_id = ev["id"]
            self.assertEqual(ev["title"], "Call Cliente")
            self.assertEqual(ev["location"], "Teams")
            self.assertEqual(ev["start_iso"], "2026-11-02T15:00:00")
            self.assertIn("&src=", ev["gcal_url"])

            # 2. Edit event (move date and rename)
            updated = cm.edit_event(
                ev_id,
                title="Call Cliente Intermarine",
                start_iso="2026-11-02T16:00:00",
                end_iso="2026-11-02T17:00:00",
                location="Google Meet",
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated["title"], "Call Cliente Intermarine")
            self.assertEqual(updated["start_iso"], "2026-11-02T16:00:00")
            self.assertEqual(updated["location"], "Google Meet")
            self.assertIn("Call%20Cliente%20Intermarine", updated["gcal_url"])

            # 3. Delete event
            deleted = cm.delete_event(ev_id)
            self.assertTrue(deleted)
            self.assertEqual(len(cm.local_events), 0)

    def test_delete_event_by_title_or_remote_id(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            cm = CalendarManager()
            cm.local_events = [
                {"id": "loc_1", "remote_id": "rem_1", "title": "Riunione Straordinaria", "start_iso": "2026-11-05T09:00:00"}
            ]
            cm.remote_events = [
                {"id": "rem_1", "title": "Riunione Straordinaria", "start_iso": "2026-11-05T09:00:00"}
            ]

            ok = cm.delete_event("Riunione Straordinaria")
            self.assertTrue(ok)
            self.assertEqual(len(cm.local_events), 0)
            self.assertEqual(len(cm.remote_events), 0)
            self.assertIn("rem_1", cm.deleted_remote_ids)


class TestCompanionRequestCalendarEndpoints(unittest.TestCase):
    """Verifica endpoint HTTP POST /api/calendar/action e GET /api/calendar."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.temp_dir, "calendar_config.json")
        self.events_path = os.path.join(self.temp_dir, "events.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _call_post_json(self, path, payload):
        handler = CompanionRequestHandler.__new__(CompanionRequestHandler)
        handler.path = path
        handler.headers = {"Content-Length": str(len(json.dumps(payload)))}
        handler.rfile = io.BytesIO(json.dumps(payload).encode("utf-8"))
        handler.wfile = io.BytesIO()

        responses = []
        def mock_send(data):
            responses.append(data)
            return True
        handler._send_json = mock_send

        handler.do_POST()
        self.assertTrue(len(responses) > 0, f"No response sent for POST {path}")
        return responses[0]

    def _call_get_json(self, path):
        handler = CompanionRequestHandler.__new__(CompanionRequestHandler)
        handler.path = path
        handler.headers = {}
        handler.wfile = io.BytesIO()

        responses = []
        def mock_send(data):
            responses.append(data)
            return True
        handler._send_json = mock_send

        handler.do_GET()
        self.assertTrue(len(responses) > 0, f"No response sent for GET {path}")
        return responses[0]

    def test_post_calendar_action_lifecycle(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            am.calendar_manager = CalendarManager()

            # 1. Action: add
            res_add = self._call_post_json(
                "/api/calendar/action",
                {
                    "action": "add",
                    "title": "Milestone Review",
                    "start": "2026-11-03T14:30:00",
                    "duration_minutes": 30,
                    "location": "Stanza 4",
                },
            )
            self.assertTrue(res_add.get("ok"))
            ev = res_add.get("event")
            self.assertIsNotNone(ev)
            self.assertEqual(ev["title"], "Milestone Review")
            ev_id = ev["id"]

            # 2. Action: edit / move
            res_edit = self._call_post_json(
                "/api/calendar/action",
                {
                    "action": "move",
                    "id": ev_id,
                    "title": "Milestone Review Spostata",
                    "start_iso": "2026-11-03T16:00:00",
                },
            )
            self.assertTrue(res_edit.get("ok"))
            self.assertEqual(res_edit["event"]["title"], "Milestone Review Spostata")
            self.assertEqual(res_edit["event"]["start_iso"], "2026-11-03T16:00:00")

            # 3. Action: sync_now
            with patch.object(am.calendar_manager, "refresh_remote_events") as mock_refresh:
                res_sync = self._call_post_json("/api/calendar/action", {"action": "sync_now"})
                self.assertTrue(res_sync.get("ok"))
                self.assertIn("calendar_sync_status", res_sync)
                self.assertTrue(mock_refresh.called)

            # 4. Action: delete
            res_del = self._call_post_json("/api/calendar/action", {"action": "delete", "id": ev_id})
            self.assertTrue(res_del.get("ok"))

    def test_get_calendar_endpoint(self):
        with patch.object(am, "CALENDAR_CONFIG_FILE", self.cfg_path), patch.object(am, "EVENTS_FILE", self.events_path):
            am.calendar_manager = CalendarManager()
            res_get = self._call_get_json("/api/calendar")

            self.assertTrue(res_get.get("ok"))
            self.assertIn("calendar", res_get)
            self.assertIn("calendar_sync_status", res_get)
            self.assertIn("calendar_events", res_get)
            self.assertIn("todo_deadlines", res_get)


class TestTodoDeadlinesAndSnapshot(unittest.TestCase):
    """Verifica estrazione scadenze To-Do e integrazione in SystemStateMonitor.get_snapshot."""

    def test_extract_todo_deadlines_from_structured_and_legacy_strings(self):
        todos = [
            # Structured To-Do
            {
                "id": "todo_1",
                "title": "Cleanup modello in Blender",
                "project": "Nave Intermarine",
                "due_date": "2026-11-02",
                "due_label": "lun 2/11",
                "status": "todo",
                "priority": "high",
            },
            # Legacy string with Italian parenthesis
            "Nave Intermarine – Export GLB ottimizzato con Draco (mar 3/11)",
            # Legacy string with range parenthesis
            "Nave Intermarine – Integrazione pipeline Rust + Three.js (mer-gio 4/5)",
            # Legacy string with Friday date
            "Nave Intermarine – Interattività + ottimizzazione realtime (ven 6/11)",
            # Task without deadline
            "Generico task senza scadenza",
        ]

        deadlines = extract_todo_deadlines(todos)
        self.assertEqual(len(deadlines), 4)

        # Verify parsed items
        d_map = {d["title"]: d for d in deadlines}
        self.assertIn("Cleanup modello in Blender", d_map)
        self.assertEqual(d_map["Cleanup modello in Blender"]["due_date"], "2026-11-02")
        self.assertEqual(d_map["Cleanup modello in Blender"]["project"], "Nave Intermarine")

        # Find Draco task
        draco = [d for d in deadlines if "Draco" in d["title"]][0]
        self.assertEqual(draco["due_date"], "2026-11-03")
        self.assertEqual(draco["due_label"], "mar 3/11")

        # Find Rust task
        rust = [d for d in deadlines if "Rust" in d["title"]][0]
        self.assertEqual(rust["due_date"], "2026-11-04")
        self.assertEqual(rust["due_label"], "mer-gio 4/5")

        # Find Realtime task
        rt = [d for d in deadlines if "realtime" in d["title"].lower()][0]
        self.assertEqual(rt["due_date"], "2026-11-06")
        self.assertEqual(rt["due_label"], "ven 6/11")

    def test_system_state_monitor_snapshot_contains_calendar_and_deadlines(self):
        temp_dir = tempfile.mkdtemp()
        try:
            cfg_path = os.path.join(temp_dir, "calendar_config.json")
            events_path = os.path.join(temp_dir, "events.json")
            with patch.object(am, "CALENDAR_CONFIG_FILE", cfg_path), patch.object(am, "EVENTS_FILE", events_path):
                cm = CalendarManager()
                cm.add_event("Meeting Direzione", datetime(2026, 10, 27, 10, 0, 0))

                # Mock components
                mock_audio = MagicMock()
                mock_audio.get_audio_state.return_value = {"audio_active": False}
                mock_ollama = MagicMock()
                mock_ollama.get_memories.return_value = []
                mock_ollama.load_todos.return_value = [
                    {"id": "t1", "title": "Task Alpha", "due_date": "2026-11-02", "due_label": "lun 2/11", "status": "todo"}
                ]
                mock_ollama.models = ["qwen2.5:7b"]
                mock_ollama.active_model = "qwen2.5:7b"
                mock_ollama.ollama_online = True
                mock_ollama.is_generating = False

                mock_mem_proj = MagicMock()
                mock_mem_proj.get_projects_state.return_value = {"projects": [], "archive": [], "stale_projects": []}
                mock_mem_proj.profile = {"traits": [], "answers": {}, "hotkey": "Alt+N"}

                mock_win_conn = MagicMock()
                mock_win_conn.ollama_pull_status = {"status": "idle"}
                mock_win_conn.window_mode = "dashboard"
                mock_win_conn.focus_input_seq = 0

                monitor = am.SystemStateMonitor(mock_audio, mock_ollama, cm, mock_mem_proj, mock_win_conn)
                snap = monitor.get_snapshot()

                self.assertIn("calendar_sync_status", snap)
                self.assertIn("calendar_events", snap)
                self.assertIn("todo_deadlines", snap)
                self.assertIn("calendar", snap)

                # Check todo_deadlines content
                self.assertEqual(len(snap["todo_deadlines"]), 1)
                self.assertEqual(snap["todo_deadlines"][0]["due_date"], "2026-11-02")
                # Check calendar_events content
                self.assertGreaterEqual(len(snap["calendar_events"]), 1)
                self.assertEqual(snap["calendar_events"][0]["title"], "Meeting Direzione")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
