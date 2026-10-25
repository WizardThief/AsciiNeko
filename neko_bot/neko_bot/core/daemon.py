"""
neko_bot.core.daemon - Background Daemon Lifecycle, IPC HTTP Server, and Status Caching.

Implements:
- Windows background process detachment (DETACHED_PROCESS | CREATE_NO_WINDOW)
- Thread-safe IPC HTTP server on 127.0.0.1:8766 (GET /health, GET /status, POST /command)
- Atomic .neko/status.json caching via temporary file and os.replace
- Robust PID tracking with stale PID auto-recovery
- In-process status fallback when daemon is stopped (zero crash / zero hang guarantee)
"""

from datetime import datetime, timezone
import http.server
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple
import urllib.error
import urllib.request

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.core.resource_monitor import ResourceMonitor

logger = logging.getLogger("neko_bot.daemon")


def atomic_write_json(file_path: Path, data: Dict[str, Any]) -> None:
    """Convenience alias for atomic_write_json_file."""
    atomic_write_json_file(file_path, data)


class DaemonStatusManager:
    """Thread-safe status container managing runtime snapshot and atomic caching."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._status: Dict[str, Any] = {
            "daemon_health": "stopped",
            "daemon_pid": None,
            "resource_mode": "normal",
            "active_application": None,
            "active_app": None,
            "active_project": None,
            "today_tracked_seconds": 0,
            "tracked_time_today_sec": 0,
            "is_afk": False,
            "idle_seconds": 0,
            "top_task": None,
            "notification_queue": [],
            "memory": {
                "total_facts": 0,
                "active_projects": 0,
                "recent_episodes": 0,
                "upcoming_deadlines": [],
            },
            "skills": {
                "total_skills": 0,
                "active_skills": [],
                "last_executed_skill": None,
            },
            "cloud_sync": {
                "status": "offline",
                "connected": False,
                "last_sync_time": None,
                "last_sync": None,
                "pending_mutations": 0,
                "pending": 0,
                "cloud_server_url": getattr(self.config, "cloud_url", "http://127.0.0.1:8767"),
                "server": getattr(self.config, "cloud_url", "http://127.0.0.1:8767"),
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def set_running(self, pid: int) -> None:
        """Marks daemon as running with the given PID and flushes to disk."""
        with self._lock:
            self._status["daemon_health"] = "running"
            self._status["daemon_pid"] = int(pid)
            self._status["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.flush()

    def set_stopped(self) -> None:
        """Marks daemon as stopped and flushes to disk."""
        with self._lock:
            self._status["daemon_health"] = "stopped"
            self._status["daemon_pid"] = None
            self._status["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.flush()

    def update(self, **kwargs: Any) -> None:
        """Updates arbitrary fields in the status dictionary, syncing alias keys."""
        with self._lock:
            for k, v in kwargs.items():
                self._status[k] = v
                if k == "active_application":
                    self._status["active_app"] = v
                elif k == "active_app":
                    self._status["active_application"] = v
                elif k == "today_tracked_seconds":
                    self._status["tracked_time_today_sec"] = v
                elif k == "tracked_time_today_sec":
                    self._status["today_tracked_seconds"] = v
            self._status["updated_at"] = datetime.now(timezone.utc).isoformat()

    def get_status(self) -> Dict[str, Any]:
        """Returns a copy of the current status dictionary."""
        with self._lock:
            return dict(self._status)

    def get_uptime(self) -> float:
        """Returns uptime in seconds."""
        return time.time() - self._start_time

    def flush(self) -> None:
        """Flushes the current status snapshot to .neko/status.json atomically."""
        atomic_write_json_file(self.config.status_file, self.get_status())


class DaemonRequestHandler(http.server.BaseHTTPRequestHandler):
    """Local IPC HTTP Request Handler for 127.0.0.1:8766."""

    server: "DaemonHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path == "/health":
            body = {
                "status": "ok",
                "pid": os.getpid(),
                "uptime_seconds": round(self.server.daemon_server.status_manager.get_uptime(), 2),
            }
            self._send_json(200, body)
            return

        if clean_path == "/status":
            self._send_json(200, self.server.daemon_server.status_manager.get_status())
            return

        self._send_json(404, {"error": "Not Found"})

    def do_POST(self) -> None:
        clean_path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body: Dict[str, Any] = {}
        if length > 0:
            try:
                raw_data = self.rfile.read(length).decode("utf-8")
                body = json.loads(raw_data)
            except Exception:
                self._send_json(400, {"error": "Invalid JSON body"})
                return

        if clean_path == "/command":
            action = body.get("action", "") or body.get("command", "")
            if action == "stop":
                self._send_json(200, {"success": True, "message": "Daemon shutting down"})
                threading.Thread(
                    target=self.server.daemon_server.trigger_stop,
                    daemon=True,
                ).start()
                return

            if action == "ping":
                self._send_json(200, {"success": True, "message": "pong"})
                return

            if action in ("route_prompt", "do"):
                prompt = body.get("prompt", "")
                result = self.server.daemon_server.handle_route_prompt(prompt)
                self._send_json(200, {"success": True, "result": result, "response": result.get("data")})
                return

            if action in ("task_add", "add_task"):
                text = body.get("text", "") or body.get("prompt", "")
                from neko_bot.task.parser import parse_task_input
                from neko_bot.task.store import TaskStore
                cfg = self.server.daemon_server.config
                parsed = parse_task_input(text, config=cfg)
                store = TaskStore(cfg.tasks_file, cfg.tasks_md_file, config=cfg)
                task = store.add_task(
                    title=parsed.title,
                    project=parsed.project,
                    priority=parsed.priority,
                    due_date=parsed.due_date,
                    tags=parsed.tags,
                    metadata={"source": "ipc_quick_bar", "raw_prompt": text},
                )
                try:
                    from neko_bot.task.ranking import get_top_task_from_store
                    top = get_top_task_from_store(cfg.workspace_dir)
                    self.server.daemon_server.status_manager.update(top_task=top)
                    self.server.daemon_server.status_manager.flush()
                except Exception:
                    pass
                self._send_json(200, {
                    "success": True,
                    "action": "task_add",
                    "task": task.to_dict(),
                    "message": f"Task added: {task.title}",
                })
                return

            if action in ("briefing", "generate_briefing"):
                force_det = body.get("deterministic", False)
                lang = body.get("language", "it")
                is_eve = body.get("evening", False)
                gen = self.server.daemon_server.briefing_generator
                if is_eve:
                    res = gen.generate_evening_wrapup(force_deterministic=force_det, language=lang)
                else:
                    res = gen.generate_morning_briefing(force_deterministic=force_det, language=lang)
                self._send_json(200, {
                    "success": True,
                    "action": "briefing",
                    "briefing": res.to_dict(),
                    "text": res.text,
                })
                return

            if action == "sync":
                force = bool(body.get("force", False))
                res = self.server.daemon_server.sync_engine.sync(force_push=force)
                self._send_json(200, {"success": res.success, "result": res.to_dict()})
                return

            if action in ("cloud_sync", "sync_cloud"):
                if getattr(self.server.daemon_server, "cloud_sync_manager", None):
                    res = self.server.daemon_server.cloud_sync_manager.reconcile()
                    self._send_json(200, {"success": res.get("status") == "synced", "result": res})
                else:
                    self._send_json(503, {"success": False, "error": "CloudSyncManager not initialized"})
                return

            if action in ("cloud_status", "status_cloud"):
                c_status = self.server.daemon_server.status_manager.get_status().get("cloud_sync", {})
                self._send_json(200, {"success": True, "cloud_sync": c_status})
                return

            if action == "memory_stats":
                stats = self.server.daemon_server.memory_store.get_stats()
                self._send_json(200, {"success": True, "stats": stats})
                return

            if action == "memory_add_fact":
                content = body.get("content", "")
                cat = body.get("category", "fact")
                tags = body.get("tags")
                imp = body.get("importance", 3)
                conf = body.get("confidence", 1.0)
                fact = self.server.daemon_server.memory_store.add_fact(
                    content=content, category=cat, tags=tags, importance=imp, confidence=conf
                )
                self._send_json(200, {"success": True, "fact": fact})
                return

            if action == "memory_add_project":
                pname = body.get("project_name", "")
                milestone = body.get("milestone", "")
                due = body.get("deadline_utc")
                risk = body.get("risk_level", "medium")
                deps = body.get("dependencies")
                proj = self.server.daemon_server.memory_store.add_project(
                    project_name=pname, milestone=milestone, deadline_utc=due,
                    risk_level=risk, dependencies=deps
                )
                self._send_json(200, {"success": True, "project": proj})
                return

            if action == "memory_search":
                query = body.get("query", "")
                level = body.get("level", "all")
                limit = body.get("limit", 5)
                res = self.server.daemon_server.memory_store.search(query, level=level, limit=limit)
                self._send_json(200, {"success": True, "results": res})
                return

            if action == "memory_list":
                level = body.get("level", "semantic")
                project = body.get("project")
                res = self.server.daemon_server.memory_store.list_entries(level=level, project=project)
                self._send_json(200, {"success": True, "entries": res})
                return

            if action == "memory_get":
                eid = body.get("entry_id", "")
                entry = self.server.daemon_server.memory_store.get_entry(eid)
                if entry:
                    self._send_json(200, {"success": True, "entry": entry})
                else:
                    self._send_json(404, {"error": f"Entry not found: {eid}"})
                return

            if action == "memory_delete":
                eid = body.get("entry_id", "")
                hard = bool(body.get("hard", False))
                deleted = self.server.daemon_server.memory_store.delete_entry(eid, hard=hard)
                self._send_json(200, {"success": deleted, "deleted": deleted})
                return

            if action == "skill_list":
                inc_del = bool(body.get("include_deleted", False))
                skills = self.server.daemon_server.skill_registry.list_skills(include_deleted=inc_del)
                self._send_json(200, {"success": True, "skills": skills})
                return

            if action == "skill_get":
                sid = body.get("skill_id", "")
                skill = self.server.daemon_server.skill_registry.get_skill(sid)
                if skill:
                    self._send_json(200, {"success": True, "skill": skill})
                else:
                    self._send_json(404, {"error": f"Skill not found: {sid}"})
                return

            if action == "skill_run":
                sid = body.get("skill_id", "")
                inp = body.get("inputs", {})
                ctx = body.get("context")
                from neko_bot.skills.executor import SkillExecutor
                executor = SkillExecutor(
                    self.server.daemon_server.skill_registry,
                    self.server.daemon_server.skill_sandbox,
                    memory_store=self.server.daemon_server.memory_store,
                    config=self.server.daemon_server.config,
                )
                res = executor.execute(sid, inputs=inp, context=ctx, session_id="ipc_skill_run")
                self._send_json(200, res)
                return

            if action == "skill_test":
                sid = body.get("skill_id", "")
                skill = self.server.daemon_server.skill_registry.get_skill(sid)
                if not skill:
                    self._send_json(404, {"error": f"Skill not found: {sid}"})
                    return
                ok, msg = self.server.daemon_server.skill_sandbox.verify_skill(skill)
                self._send_json(200, {"success": ok, "message": msg, "skill_id": sid})
                return

            if action == "skill_delete":
                sid = body.get("skill_id", "")
                hard = bool(body.get("hard", False))
                deleted = self.server.daemon_server.skill_registry.delete_skill(sid, hard=hard)
                self._send_json(200, {"success": deleted, "deleted": deleted, "skill_id": sid})
                return

            self._send_json(400, {"error": f"Unknown action: {action}"})
            return

        self._send_json(404, {"error": "Not Found"})

    def _send_json(self, code: int, data: Dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class DaemonHTTPServer(http.server.ThreadingHTTPServer):
    """Threading HTTP Server with address reuse and daemon threads."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: Tuple[str, int], RequestHandlerClass: Any, daemon_server: "DaemonServer") -> None:
        super().__init__(server_address, RequestHandlerClass)
        self.daemon_server = daemon_server


class DaemonServer:
    """Daemon Runtime Server running the main loop and managing subsystems."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.status_manager = DaemonStatusManager(config)
        self.resource_monitor = ResourceMonitor()
        self.stop_event = threading.Event()
        self.http_server: Optional[DaemonHTTPServer] = None
        self.http_thread: Optional[threading.Thread] = None

        # Time tracker subsystem
        from neko_bot.tracker.rules import RulesEngine
        from neko_bot.tracker.logger import TimeLogger
        from neko_bot.tracker.afk import AFKManager
        from neko_bot.tracker.win32 import get_win32_provider

        rules_path = self.config.workspace_dir / "tracking_rules.json"
        self.rules_engine = RulesEngine(rules_path)
        self.time_logger = TimeLogger(logs_dir=self.config.time_logs_dir)
        self.afk_manager = AFKManager(afk_threshold=self.config.afk_threshold)
        self.win32_provider = get_win32_provider()

        # Notification & Briefing subsystems (Milestone 4)
        from neko_bot.notifications.queue import NotificationQueue
        from neko_bot.notifications.toast import get_toast_dispatcher
        from neko_bot.briefing.generator import BriefingGenerator, BriefingTriggerManager
        from neko_bot.briefing.timers import ContinuousWorkMonitor

        self.notification_queue = NotificationQueue(
            file_path=self.config.notifications_file,
            status_file=self.config.status_file,
        )
        self.toast_dispatcher = get_toast_dispatcher()
        self.briefing_generator = BriefingGenerator(self.config)
        self.briefing_trigger = BriefingTriggerManager(config=self.config)
        self.work_monitor = ContinuousWorkMonitor(
            break_interval=self.config.break_interval,
            break_duration=self.config.break_duration,
        )

        # Multi-PC Conflict-Free Git Auto-Sync subsystem (Milestone 5)
        from neko_bot.sync.engine import GitSyncEngine
        self.sync_engine = GitSyncEngine(config=self.config)
        self._last_sync_time = 0.0
        self._sync_in_progress = False

        # Multi-Level Persistent Memory Subsystem (Milestone 2)
        from neko_bot.memory.store import MemoryStore
        self.memory_store = MemoryStore(self.config.workspace_dir, config=self.config)

        # Task Management Subsystem (Milestone 1)
        from neko_bot.task.store import TaskStore
        self.task_store = TaskStore(self.config.tasks_file, self.config.tasks_md_file, config=self.config)

        # Autonomous Hybrid Skill Subsystem (Milestone 3)
        from neko_bot.skills.sandbox import SkillSandbox
        from neko_bot.skills.registry import SkillRegistry
        self.skill_sandbox = SkillSandbox()
        self.skill_registry = SkillRegistry(self.config.workspace_dir, self.skill_sandbox, config=self.config)

        # Cloud-First Offline Sync Subsystem (Milestone 4)
        try:
            from neko_bot.cloud.sync_manager import CloudSyncManager
            dev_id = getattr(self.config, "cloud_device_id", None) or os.environ.get("NEKO_DEVICE_ID", "default_device")
            tok = getattr(self.config, "cloud_auth_token", None) or os.environ.get("NEKO_CLOUD_TOKEN", "")
            self.cloud_sync_manager = CloudSyncManager(
                self.config.workspace_dir,
                server_url=getattr(self.config, "cloud_url", "http://127.0.0.1:8767"),
                device_id=dev_id,
                token=tok,
                task_store=self.task_store,
                memory_store=self.memory_store,
                skill_registry=self.skill_registry,
                config=self.config,
            )
        except Exception as e:
            logger.debug("CloudSyncManager initialization skipped: %s", e)
            self.cloud_sync_manager = None

        self._last_cloud_sync_time = 0.0
        self._cloud_sync_in_progress = False
        self._last_successful_sync_time: Optional[str] = None

    def trigger_stop(self) -> None:
        """Sets stop event with slight delay allowing in-flight HTTP responses to finish."""
        time.sleep(0.05)
        self.stop_event.set()

    def handle_route_prompt(self, prompt: str) -> Dict[str, Any]:
        """Routes a natural language prompt via Skill Subsystem or OllamaRouter."""
        try:
            # 1. Match existing installed skill
            matched = self.skill_registry.match_skill(prompt)
            if matched:
                from neko_bot.skills.executor import SkillExecutor
                executor = SkillExecutor(
                    self.skill_registry,
                    self.skill_sandbox,
                    memory_store=self.memory_store,
                    config=self.config,
                )
                res = executor.execute(
                    matched["id"],
                    inputs={"text": prompt, "prompt": prompt},
                    session_id="daemon_do",
                    log_episode=True,
                    event_type="skill_reused",
                )
                return {"success": True, "source": "skill_reused", "data": res.get("result"), "skill": matched}

            # 2. Autonomous discovery & synthesis
            from neko_bot.skills.synthesizer import SkillSynthesizer
            synthesizer = SkillSynthesizer(self.skill_sandbox, self.skill_registry, self.config)
            ok, new_skill, diag = synthesizer.synthesize_and_register(prompt)
            if ok and new_skill:
                from neko_bot.skills.executor import SkillExecutor
                executor = SkillExecutor(
                    self.skill_registry,
                    self.skill_sandbox,
                    memory_store=self.memory_store,
                    config=self.config,
                )
                res = executor.execute(
                    new_skill["id"],
                    inputs={"text": prompt, "prompt": prompt},
                    session_id="daemon_do",
                    log_episode=True,
                    event_type="skill_discovered_and_executed",
                )
                return {"success": True, "source": "skill_synthesized", "data": res.get("result"), "skill": new_skill}

            from neko_bot.core.router import get_default_router
            router = get_default_router(self.config)
            return router.route_prompt(prompt)
        except Exception as e:
            logger.error("Failed to route prompt: %s", e)
            return {"error": str(e), "success": False, "data": prompt}

    def run(self) -> None:
        """Main daemon entry point: writes PID, starts HTTP server, runs tick loop."""
        self.config.ensure_dirs()
        pid = os.getpid()

        with open(self.config.pid_file, "w", encoding="utf-8") as f:
            f.write(str(pid))

        self.status_manager.set_running(pid)
        logger.info("neko_bot daemon started with PID %d on port %d", pid, self.config.port)

        try:
            self.http_server = DaemonHTTPServer(
                (self.config.host, self.config.port),
                DaemonRequestHandler,
                self,
            )
            self.http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
            self.http_thread.start()
            logger.info("IPC HTTP Server listening on %s:%d", self.config.host, self.config.port)
        except Exception as e:
            logger.error("Failed to bind IPC HTTP Server: %s", e)
            self.status_manager.set_stopped()
            if self.config.pid_file.exists():
                try:
                    self.config.pid_file.unlink()
                except Exception:
                    pass
            raise

        # Proactive Morning Briefing on first startup of the day
        try:
            if self.briefing_trigger.should_trigger():
                briefing = self.briefing_generator.generate_morning_briefing()
                self.toast_dispatcher.show_async(
                    title=briefing.title,
                    message=briefing.short_body,
                    category="briefing",
                )
                self.notification_queue.enqueue(
                    title=briefing.title,
                    message=briefing.text,
                    category="briefing",
                    level="info",
                )
                self.briefing_trigger.mark_triggered()
                self.notification_queue.sync_to_status(self.config.status_file)
        except Exception as e:
            logger.debug("Startup briefing delivery failed: %s", e)

        # Non-blocking Git startup auto-sync (Milestone 5)
        threading.Thread(target=self._run_background_sync, daemon=True).start()

        try:
            while not self.stop_event.is_set():
                self._tick()
                self.stop_event.wait(timeout=self.config.tracking_interval)
        finally:
            self._cleanup()

    def _tick(self) -> None:
        """Periodic background evaluation of resource mode, time tracking, and status persistence."""
        try:
            res_mode = self.resource_monitor.get_resource_mode()
            top_task = None
            if self.config.tasks_file.exists():
                try:
                    from neko_bot.task.ranking import get_top_task_from_store
                    top_task = get_top_task_from_store(self.config.workspace_dir)
                except Exception:
                    pass

            # 1. Poll Win32 window and idle state
            now = time.time()
            win_state = self.win32_provider.get_foreground_window_state()
            idle_sec = self.win32_provider.get_idle_seconds()

            # 2. Update AFKManager
            time_slice_or_slices = self.afk_manager.update(now=now, window=win_state, idle_seconds=idle_sec)
            slices = time_slice_or_slices if isinstance(time_slice_or_slices, list) else ([time_slice_or_slices] if time_slice_or_slices else [])
            for ts in slices:
                if ts and ts.duration_sec > 0:
                    classification = self.rules_engine.classify(ts.app, ts.title)
                    if not classification.is_ignored:
                        self.time_logger.log_record(
                            start_time=ts.start_iso,
                            end_time=ts.end_iso,
                            app=ts.app,
                            title=ts.title,
                            project=classification.project,
                            duration_sec=ts.duration_sec,
                            category=classification.category,
                            idle_subtracted=ts.idle_subtracted,
                        )

            # 3. Classify current active window for status display
            active_app = win_state.exe_name if win_state else None
            active_proj = None
            if win_state and win_state.exe_name:
                curr_class = self.rules_engine.classify(win_state.exe_name, win_state.title)
                active_proj = curr_class.project

            # 4. Aggregated today tracked seconds
            from neko_bot.tracker.aggregator import get_today_tracked_seconds
            today_sec = get_today_tracked_seconds(self.config.time_logs_dir)

            # 5. Continuous Work & Focus Timer Evaluation
            break_alert = self.work_monitor.tick(
                is_afk=self.afk_manager.is_afk,
                idle_seconds=idle_sec,
            )
            if break_alert:
                self.toast_dispatcher.show_async(
                    title=break_alert.title,
                    message=break_alert.message,
                    category="timer",
                )
                self.notification_queue.enqueue(
                    title=break_alert.title,
                    message=break_alert.message,
                    category="timer",
                    level="alert",
                )
                self.notification_queue.sync_to_status(self.config.status_file)

            active_notifs = [item.to_dict() for item in self.notification_queue.peek(unread_only=True)]

            # Query memory store statistics
            mem_stats = {
                "total_facts": 0,
                "active_projects": 0,
                "recent_episodes": 0,
                "upcoming_deadlines": [],
            }
            if hasattr(self, "memory_store") and self.memory_store:
                try:
                    mem_stats = self.memory_store.get_stats()
                except Exception:
                    pass

            # Query skill registry statistics
            skills_stats = {
                "total_skills": 0,
                "active_skills": [],
                "last_executed_skill": None,
            }
            if hasattr(self, "skill_registry") and self.skill_registry:
                try:
                    skills = self.skill_registry.list_skills()
                    skills_stats["total_skills"] = len(skills)
                    skills_stats["active_skills"] = [s.get("name") or s.get("id") for s in skills]
                    executed = [s for s in skills if s.get("last_executed_at")]
                    if executed:
                        executed.sort(key=lambda s: s.get("last_executed_at", ""), reverse=True)
                        skills_stats["last_executed_skill"] = executed[0].get("id") or executed[0].get("name")
                except Exception:
                    pass

            self.status_manager.update(
                resource_mode=res_mode,
                top_task=top_task,
                active_application=active_app,
                active_app=active_app,
                active_project=active_proj,
                today_tracked_seconds=today_sec,
                tracked_time_today_sec=today_sec,
                is_afk=self.afk_manager.is_afk,
                idle_seconds=int(round(idle_sec)),
                notification_queue=active_notifs,
                memory=mem_stats,
                skills=skills_stats,
            )
            self.status_manager.flush()

            # 6. Periodic Background Git Auto-Sync
            now_sync = time.time()
            sync_interval = getattr(self.config, "git_sync_interval", 300)
            if (now_sync - self._last_sync_time) >= sync_interval:
                self._last_sync_time = now_sync
                if not self._sync_in_progress:
                    threading.Thread(target=self._run_background_sync, daemon=True).start()

            # 7. Periodic Background Cloud Auto-Sync (Milestone 4)
            now_cloud = time.time()
            cloud_sync_interval = getattr(self.config, "cloud_sync_interval", 15)
            if (now_cloud - self._last_cloud_sync_time) >= cloud_sync_interval:
                self._last_cloud_sync_time = now_cloud
                if not self._cloud_sync_in_progress and getattr(self, "cloud_sync_manager", None) is not None:
                    threading.Thread(target=self._run_background_cloud_sync, daemon=True).start()

        except Exception as e:
            logger.debug("Daemon tick error: %s", e)

    def _run_background_sync(self, force: bool = False) -> None:
        """Executes git sync in background thread without stalling daemon tick loop."""
        if self._sync_in_progress:
            return
        self._sync_in_progress = True
        try:
            res = self.sync_engine.sync(force_push=force)
            self.status_manager.update(
                git_sync_status=res.status.value,
                git_last_sync=res.timestamp,
                git_sync_message=res.message,
            )
            self.status_manager.flush()
        except Exception as e:
            logger.error("Background git sync failed: %s", e)
        finally:
            self._sync_in_progress = False

    def _run_background_cloud_sync(self) -> None:
        """Executes cloud sync in background thread without stalling daemon tick loop."""
        if self._cloud_sync_in_progress or not getattr(self, "cloud_sync_manager", None):
            return
        self._cloud_sync_in_progress = True
        try:
            res = self.cloud_sync_manager.reconcile()
            connected = (res.get("status") == "synced")
            now_iso = datetime.now(timezone.utc).isoformat()
            if connected:
                self._last_successful_sync_time = now_iso

            pending_count = res.get("pending_mutations", res.get("pending", 0))
            cloud_payload = {
                "status": res.get("status", "synced" if connected else "offline"),
                "connected": connected,
                "last_sync_time": self._last_successful_sync_time,
                "last_sync": self._last_successful_sync_time,
                "pending_mutations": pending_count,
                "pending": pending_count,
                "cloud_server_url": self.cloud_sync_manager.server_url,
                "server": self.cloud_sync_manager.server_url,
            }
            self.status_manager.update(cloud_sync=cloud_payload)
            self.status_manager.flush()
        except Exception as e:
            logger.debug("Background cloud sync error: %s", e)
            q_count = 0
            try:
                q_count = self.cloud_sync_manager.get_queue_stats().get("pending_mutations", 0)
            except Exception:
                pass
            cloud_payload = {
                "status": "offline",
                "connected": False,
                "last_sync_time": self._last_successful_sync_time,
                "last_sync": self._last_successful_sync_time,
                "pending_mutations": q_count,
                "pending": q_count,
                "cloud_server_url": getattr(self.config, "cloud_url", "http://127.0.0.1:8767"),
                "server": getattr(self.config, "cloud_url", "http://127.0.0.1:8767"),
            }
            self.status_manager.update(cloud_sync=cloud_payload)
            self.status_manager.flush()
        finally:
            self._cloud_sync_in_progress = False

    def _cleanup(self) -> None:
        """Performs clean shutdown: flushes active tracking session, stops HTTP server, unlinks PID file."""
        logger.info("neko_bot daemon stopping...")
        if hasattr(self, "afk_manager") and hasattr(self, "time_logger") and hasattr(self, "rules_engine"):
            try:
                final_slice = self.afk_manager.flush()
                if final_slice and final_slice.duration_sec > 0:
                    classification = self.rules_engine.classify(final_slice.app, final_slice.title)
                    if not classification.is_ignored:
                        self.time_logger.log_record(
                            start_time=final_slice.start_iso,
                            end_time=final_slice.end_iso,
                            app=final_slice.app,
                            title=final_slice.title,
                            project=classification.project,
                            duration_sec=final_slice.duration_sec,
                            category=classification.category,
                            idle_subtracted=final_slice.idle_subtracted,
                        )
            except Exception as e:
                logger.debug("Failed to flush active session on daemon cleanup: %s", e)

        # Flush final git sync on shutdown
        if hasattr(self, "sync_engine"):
            try:
                self.sync_engine.sync()
            except Exception as e:
                logger.debug("Final git sync on daemon cleanup skipped: %s", e)

        if self.http_server:
            try:
                self.http_server.shutdown()
                self.http_server.server_close()
            except Exception:
                pass
        self.status_manager.set_stopped()
        if self.config.pid_file.exists():
            try:
                self.config.pid_file.unlink()
            except Exception:
                pass
        logger.info("neko_bot daemon stopped cleanly")


class DaemonController:
    """CLI-facing management controller (start, stop, restart, status)."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def is_running(self) -> Tuple[bool, Optional[int]]:
        """
        Determines if the daemon is genuinely active.
        Verifies PID file existence, process existence, and probes HTTP /health.
        Automatically cleans stale PID files if dead.
        """
        if not self.config.pid_file.exists():
            return False, None

        try:
            with open(self.config.pid_file, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
        except Exception:
            self._clean_pid_file()
            return False, None

        # Check process liveness via psutil if available
        if HAS_PSUTIL:
            if not psutil.pid_exists(pid):
                self._clean_pid_file()
                return False, None
            try:
                p = psutil.Process(pid)
                if "python" not in p.name().lower():
                    self._clean_pid_file()
                    return False, None
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._clean_pid_file()
                return False, None

        # Active HTTP loopback probe
        try:
            req = urllib.request.Request(f"http://{self.config.host}:{self.config.port}/health")
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("pid") == pid:
                        return True, pid
        except Exception:
            pass

        # If HTTP probe failed, daemon is not running or hung
        return False, pid

    def start(self, foreground: bool = False) -> Tuple[bool, str]:
        """Starts the daemon process in foreground or detached background."""
        running, pid = self.is_running()
        if running:
            return True, f"Daemon is already running with PID {pid}"

        self.config.ensure_dirs()
        if foreground:
            server = DaemonServer(self.config)
            server.run()
            return True, "Daemon terminated"

        # Windows background detachment flags
        flags = 0
        if sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            flags = DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP

        log_out = open(self.config.log_file, "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "neko_bot",
                    "--workspace",
                    str(self.config.project_root),
                    "daemon",
                    "run",
                    "--port",
                    str(self.config.port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                close_fds=True,
            )
        finally:
            log_out.close()

        # Poll health for up to 3.0 seconds
        deadline = time.time() + 3.0
        while time.time() < deadline:
            time.sleep(0.1)
            running, confirmed_pid = self.is_running()
            if running:
                return True, f"Daemon started successfully with PID {confirmed_pid}"
            if proc.poll() is not None:
                return False, f"Daemon process exited prematurely with code {proc.returncode}. See {self.config.log_file}"

        return False, f"Daemon started (PID {proc.pid}) but failed health check within 3s. See {self.config.log_file}"

    def stop(self) -> Tuple[bool, str]:
        """Stops the daemon gracefully via HTTP, falling back to process termination."""
        running, pid = self.is_running()
        if not running:
            if pid and HAS_PSUTIL and psutil.pid_exists(pid):
                try:
                    p = psutil.Process(pid)
                    if "python" in p.name().lower():
                        p.terminate()
                        try:
                            p.wait(timeout=1.0)
                        except psutil.TimeoutExpired:
                            p.kill()
                except Exception:
                    pass
            elif pid and not HAS_PSUTIL:
                try:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            self._clean_pid_file()
            self._write_offline_status()
            return True, "Daemon stopped (cleaned unresponsive process)" if pid else "Daemon was not running"

        # Stage 1: Graceful HTTP stop command
        try:
            req = urllib.request.Request(
                f"http://{self.config.host}:{self.config.port}/command",
                data=json.dumps({"action": "stop"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                pass
        except Exception:
            pass

        # Wait up to 2.5 seconds
        deadline = time.time() + 2.5
        while time.time() < deadline:
            time.sleep(0.1)
            if pid and HAS_PSUTIL:
                if not psutil.pid_exists(pid):
                    self._clean_pid_file()
                    self._write_offline_status()
                    return True, "Daemon stopped gracefully"

        # Stage 2: Force termination if still active
        if pid and HAS_PSUTIL:
            try:
                p = psutil.Process(pid)
                p.terminate()
                p.wait(timeout=1.5)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                try:
                    psutil.Process(pid).kill()
                except Exception:
                    pass

        self._clean_pid_file()
        self._write_offline_status()
        return True, "Daemon stopped"

    def restart(self) -> Tuple[bool, str]:
        """Restarts the daemon."""
        self.stop()
        time.sleep(0.2)
        return self.start()

    def get_status_payload(self) -> Dict[str, Any]:
        """
        Retrieves status snapshot:
        1. Queries live daemon HTTP endpoint (/status) if active.
        2. Falls back to in-process synthesis in < 30ms if daemon is stopped.
        Never raises uncaught exceptions.
        """
        # Step 1: Probe live daemon HTTP endpoint
        try:
            req = urllib.request.Request(f"http://{self.config.host}:{self.config.port}/status")
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            pass

        # Step 2: In-process fallback
        return self._build_in_process_status()

    def _build_in_process_status(self) -> Dict[str, Any]:
        """Synthesizes complete AC1 status payload directly from local resources and status cache."""
        cached: Dict[str, Any] = {}
        if self.config.status_file.exists():
            try:
                with open(self.config.status_file, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                    if isinstance(cdata, dict):
                        cached = cdata
            except Exception:
                cached = {}

        resource_mode = cached.get("resource_mode", "normal")
        try:
            monitor = ResourceMonitor()
            resource_mode = monitor.get_resource_mode()
        except Exception:
            pass

        top_task = None
        if self.config.tasks_file.exists():
            try:
                from neko_bot.task.ranking import get_top_task_from_store
                top_task = get_top_task_from_store(self.config.workspace_dir)
            except Exception as e:
                logger.debug("Failed to get top task from ranking: %s", e)
        else:
            top_task = cached.get("top_task")

        tracked_seconds = cached.get("today_tracked_seconds", 0)
        if tracked_seconds == 0 and self.config.time_logs_dir.exists():
            try:
                from neko_bot.tracker.aggregator import get_today_tracked_seconds
                tracked_seconds = get_today_tracked_seconds(self.config.time_logs_dir)
            except Exception as e:
                logger.debug("Failed to calculate today tracked seconds: %s", e)

        notifications = cached.get("notification_queue", [])
        if not notifications and self.config.notifications_file.exists():
            try:
                with open(self.config.notifications_file, "r", encoding="utf-8") as f:
                    ndata = json.load(f)
                    notifications = ndata.get("items", []) if isinstance(ndata, dict) else []
            except Exception:
                pass

        mem_stats = cached.get("memory")
        if not mem_stats:
            try:
                from neko_bot.memory.store import MemoryStore
                mstore = MemoryStore(self.config.workspace_dir, config=self.config)
                stats = mstore.get_stats()
                if stats["total_facts"] == 0 and stats["active_projects"] == 0:
                    root_mem = self.config.project_root / "memory"
                    if root_mem.exists():
                        mstore_root = MemoryStore(root_mem, config=self.config)
                        stats = mstore_root.get_stats()
                mem_stats = stats
            except Exception:
                mem_stats = {
                    "total_facts": 0,
                    "active_projects": 0,
                    "recent_episodes": 0,
                    "upcoming_deadlines": [],
                }

        skills_stats = cached.get("skills")
        if not skills_stats:
            try:
                from neko_bot.skills.registry import SkillRegistry
                sreg = SkillRegistry(self.config.workspace_dir, config=self.config)
                skills = sreg.list_skills()
                executed = [s for s in skills if s.get("last_executed_at")]
                last_exec = None
                if executed:
                    executed.sort(key=lambda s: s.get("last_executed_at", ""), reverse=True)
                    last_exec = executed[0].get("id") or executed[0].get("name")
                skills_stats = {
                    "total_skills": len(skills),
                    "active_skills": [s.get("name") or s.get("id") for s in skills],
                    "last_executed_skill": last_exec,
                }
            except Exception:
                skills_stats = {
                    "total_skills": 0,
                    "active_skills": [],
                    "last_executed_skill": None,
                }

        cloud_sync = cached.get("cloud_sync")
        if not cloud_sync:
            q_file = getattr(self.config, "cloud_mutations_file", self.config.neko_dir / "cloud_mutations.json")
            pending_count = 0
            if q_file.exists():
                try:
                    with open(q_file, "r", encoding="utf-8") as f:
                        q_data = json.load(f)
                        if isinstance(q_data, list):
                            pending_count = len(q_data)
                except Exception:
                    pass
            cloud_url = getattr(self.config, "cloud_url", "http://127.0.0.1:8767")
            cloud_sync = {
                "status": "offline",
                "connected": False,
                "last_sync_time": None,
                "last_sync": None,
                "pending_mutations": pending_count,
                "pending": pending_count,
                "cloud_server_url": cloud_url,
                "server": cloud_url,
            }

        active_app = cached.get("active_application", None)
        return {
            "daemon_health": "stopped",
            "daemon_pid": None,
            "resource_mode": resource_mode,
            "active_application": active_app,
            "active_app": active_app,
            "active_project": cached.get("active_project", None),
            "today_tracked_seconds": int(tracked_seconds),
            "tracked_time_today_sec": int(tracked_seconds),
            "is_afk": bool(cached.get("is_afk", False)),
            "idle_seconds": int(cached.get("idle_seconds", 0)),
            "top_task": top_task,
            "notification_queue": notifications if isinstance(notifications, list) else [],
            "memory": mem_stats,
            "skills": skills_stats,
            "cloud_sync": cloud_sync,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _clean_pid_file(self) -> None:
        if self.config.pid_file.exists():
            try:
                self.config.pid_file.unlink()
            except Exception:
                pass

    def _write_offline_status(self) -> None:
        try:
            payload = self._build_in_process_status()
            atomic_write_json_file(self.config.status_file, payload)
        except Exception:
            pass


def run_daemon(port: int = 8766, root_dir: Optional[Path] = None) -> None:
    """Entry point to run daemon in the current console."""
    cfg = Config.load(root_dir=root_dir)
    cfg.port = port
    server = DaemonServer(cfg)
    server.run()
