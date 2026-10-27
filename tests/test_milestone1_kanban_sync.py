"""
tests/test_milestone1_kanban_sync.py
Unit and integration tests for Milestone 1 (R1):
- StorageManager transaction fix on insert_memory & migrate_legacy_data robust parsing
- Dual-sync between memory.json and neko_storage.db
- Startup migration of todos, projects, traits deduplication, and skills_learned
- Kanban To-Dos CRUD, auto-project detection, backward compatibility
- Skills learned CRUD and LLM action tags
- Unified prompt assembly in handle_chat_or_command
- CompanionRequestHandler API endpoints for skills and todos
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

from bridge.storage_manager import StorageManager
from bridge.ai_monitor import (
    MemoryAndProjectsManager,
    OllamaLocalBridge,
    parse_due_date_iso,
    _migrate_todos_and_projects_on_startup,
    CompanionRequestHandler,
)


class TestStorageManagerFixes(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_storage.db")
        self.storage = StorageManager(self.db_path, self.temp_dir)

    def tearDown(self):
        try:
            self.storage.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_memory_with_project_and_pinned(self):
        # Test insert_memory with project_id and pinned=1
        # This specifically validates that conn.execute is within transaction context
        res = self.storage.insert_memory(
            text="Alpha rule: always test thoroughly",
            pinned=1,
            project_id="proj_alpha"
        )
        self.assertIsNotNone(res)
        mem_id = res["id"]
        
        # Verify persistence and query
        mems = self.storage.get_memories(project_id="proj_alpha")
        alpha_mems = [m for m in mems if m["id"] == mem_id]
        self.assertEqual(len(alpha_mems), 1)
        self.assertEqual(alpha_mems[0]["pinned"], 1)
        self.assertEqual(alpha_mems[0]["project_id"], "proj_alpha")
        self.assertIn("Alpha rule", alpha_mems[0]["text"])

    def test_migrate_legacy_data_with_string_priority_and_iso_created_at(self):
        # Create legacy todo.json with string priority and iso created_at
        todo_path = os.path.join(self.temp_dir, "todo.json")
        legacy_todos = [
            {
                "id": "task_1",
                "title": "Legacy High Priority Task",
                "priority": "high",
                "created_at": "2026-10-27T10:00:00Z",
                "status": "todo"
            },
            {
                "id": "task_2",
                "title": "Legacy Low Priority Task",
                "priority": "low",
                "created_at": "2026-10-27T11:00:00.123456",
                "status": "doing"
            }
        ]
        with open(todo_path, "w", encoding="utf-8") as f:
            json.dump(legacy_todos, f)

        # Run migrate_legacy_data
        result = self.storage.migrate_legacy_data()
        self.assertIn("tasks", result)
        self.assertGreaterEqual(result["tasks"], 2)


class TestDueDateParser(unittest.TestCase):
    def test_parse_due_date_iso(self):
        # Italian abbreviations
        self.assertEqual(parse_due_date_iso("lun 2/11"), "2026-11-02")
        self.assertEqual(parse_due_date_iso("mar 3/11"), "2026-11-03")
        self.assertEqual(parse_due_date_iso("mer-gio 4/5 nov"), "2026-11-04")
        self.assertEqual(parse_due_date_iso("ven 6/11"), "2026-11-06")

        # ISO date passthrough
        self.assertEqual(parse_due_date_iso("2026-12-25"), "2026-12-25")


class TestStartupMigrationAndMemorySync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "neko_storage.db")

        # Setup initial profile with duplicated traits
        profile_path = os.path.join(self.temp_dir, "profile.json")
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump({
                "username": "TestUser",
                "traits": ["Creative", "Creative", "Fast", "Creative", "Fast"],
                "custom_rules": []
            }, f)

        # Setup legacy string todos
        todo_path = os.path.join(self.temp_dir, "todo.json")
        with open(todo_path, "w", encoding="utf-8") as f:
            json.dump([
                "Intermarine – Raccolta reference & CAD scafo (lun 2/11)",
                "Intermarine – Modellazione 3D low/mid-poly scafo (mar 3/11)",
                "Intermarine – Dettagli coperta e sovrastruttura (mer-gio 4/5 nov)",
                "Intermarine – Setup materiali PBR e review (ven 6/11)"
            ], f)

        # Setup memory.json with Intermarine rule
        mem_path = os.path.join(self.temp_dir, "memory.json")
        with open(mem_path, "w", encoding="utf-8") as f:
            json.dump({
                "pinned": [
                    "Intermarine vuole sempre il colore blu per il suo Logo",
                    "Regola generale: usare sempre Blender per export GLB"
                ],
                "recent": []
            }, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_startup_migration(self):
        _migrate_todos_and_projects_on_startup(self.temp_dir)

        # 1. Profile traits deduplication
        with open(os.path.join(self.temp_dir, "profile.json"), "r", encoding="utf-8") as f:
            prof = json.load(f)
        self.assertEqual(sorted(prof["traits"]), ["Creative", "Fast"])
        self.assertIn("skills_learned", prof)
        skill_names = [s["name"] for s in prof["skills_learned"]]
        self.assertIn("Blender", skill_names)
        self.assertIn("Rust", skill_names)

        # 2. Projects created
        with open(os.path.join(self.temp_dir, "projects.json"), "r", encoding="utf-8") as f:
            projs = json.load(f)
        intermarine_proj = next((p for p in projs if "Intermarine" in p.get("name", "")), None)
        self.assertIsNotNone(intermarine_proj)
        self.assertEqual(intermarine_proj["client"], "Intermarine")

        # 3. Todos converted to structured objects
        with open(os.path.join(self.temp_dir, "todo.json"), "r", encoding="utf-8") as f:
            todos = json.load(f)
        self.assertEqual(len(todos), 4)
        for t in todos:
            self.assertIsInstance(t, dict)
            self.assertEqual(t["project"], "Nave Intermarine")
            self.assertEqual(t["status"], "todo")
            self.assertTrue(t["due_date"].startswith("2026-11-"))

    def test_dual_sync_and_memory_crud(self):
        _migrate_todos_and_projects_on_startup(self.temp_dir)
        mgr = MemoryAndProjectsManager(data_dir=self.temp_dir)
        bridge = OllamaLocalBridge(model="test_model", memory_manager=mgr, data_dir=self.temp_dir)

        # Check SQLite memories table was populated by sync
        storage = bridge.storage
        self.assertIsNotNone(storage)
        mems = storage.get_memories()
        self.assertGreaterEqual(len(mems), 2)

        # Test adding a memory via bridge (dual write)
        bridge.add_memory("Nuova specifica cliente: formato preferito GLTF", pinned=True)
        
        # Verify in memory.json
        disk_mems = bridge.get_memories()
        found_disk = any("formato preferito GLTF" in m.get("text", "") for m in disk_mems)
        self.assertTrue(found_disk)
        pinned_item = next(m for m in disk_mems if "formato preferito GLTF" in m.get("text", ""))
        self.assertTrue(pinned_item.get("pinned"))

        # Verify in SQLite
        db_mems = storage.get_memories()
        found = any("formato preferito GLTF" in m.get("text", "") for m in db_mems)
        self.assertTrue(found)


class TestKanbanAndSkillsEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        _migrate_todos_and_projects_on_startup(self.temp_dir)
        self.mgr = MemoryAndProjectsManager(data_dir=self.temp_dir)
        self.bridge = OllamaLocalBridge(model="test_model", memory_manager=self.mgr, data_dir=self.temp_dir)

    def tearDown(self):
        if self.bridge.storage:
            try:
                self.bridge.storage.close()
            except Exception:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_skills_learned_crud(self):
        # Initial skills
        skills = self.mgr.get_skills_learned()
        skill_names = [s["name"] for s in skills]
        self.assertIn("Blender", skill_names)
        self.assertIn("Three.js", skill_names)

        # Add skill
        self.mgr.add_skill_learned("WebGPU", category="3D Graphics", level="intermediate")
        skills2 = self.mgr.get_skills_learned()
        webgpu = next((s for s in skills2 if s["name"] == "WebGPU"), None)
        self.assertIsNotNone(webgpu)
        self.assertEqual(webgpu["category"], "3D Graphics")

        # Set level
        self.mgr.set_skill_level("WebGPU", "advanced")
        skills3 = self.mgr.get_skills_learned()
        webgpu_adv = next((s for s in skills3 if s["name"] == "WebGPU"), None)
        self.assertEqual(webgpu_adv["level"], "advanced")

        # Remove skill
        self.mgr.remove_skill_learned("WebGPU")
        skills4 = self.mgr.get_skills_learned()
        self.assertFalse(any(s["name"] == "WebGPU" for s in skills4))

    def test_todos_kanban_crud(self):
        # Add todo
        todo = self.bridge.add_todo(
            title="Scafo – Test idrodinamico",
            project="Nave Intermarine",
            due_label="dom 8/11",
            priority="high",
            subtasks=["Calibrare sensori", "Avviare simulazione"]
        )
        self.assertIsNotNone(todo["id"])
        self.assertEqual(todo["status"], "todo")
        self.assertEqual(todo["priority"], "high")
        self.assertEqual(len(todo["subtasks"]), 2)

        # Update status
        updated = self.bridge.update_todo_status(todo["id"], "doing")
        self.assertTrue(updated)
        todos = self.bridge.load_todos(as_strings=False)
        target = next(t for t in todos if t["id"] == todo["id"])
        self.assertEqual(target["status"], "doing")

        # Toggle subtask
        toggled = self.bridge.toggle_subtask(todo["id"], 0, done=True)
        self.assertTrue(toggled)
        todos = self.bridge.load_todos(as_strings=False)
        target = next(t for t in todos if t["id"] == todo["id"])
        self.assertTrue(target["subtasks"][0]["done"])

        # Edit todo
        edited = self.bridge.edit_todo(todo["id"], title="Scafo – Test idrodinamico completato", priority="medium")
        self.assertTrue(edited)
        todos = self.bridge.load_todos(as_strings=False)
        target = next(t for t in todos if t["id"] == todo["id"])
        self.assertEqual(target["title"], "Scafo – Test idrodinamico completato")
        self.assertEqual(target["priority"], "medium")

        # Delete todo
        deleted = self.bridge.delete_todo(todo["id"])
        self.assertTrue(deleted)
        todos = self.bridge.load_todos(as_strings=False)
        self.assertFalse(any(t["id"] == todo["id"] for t in todos))

    def test_llm_action_tags(self):
        # Test SKILL_LEARNED action tag
        text_skill = "Ottimo lavoro! [[ACTION:SKILL_LEARNED | AI | Prompt Engineering]] Ho appreso una nuova abilità."
        clean_text, actions = self.bridge._execute_llm_action_tags(text_skill)
        self.assertIn("Ottimo lavoro!", clean_text)
        self.assertIn("Competenza registrata", clean_text)
        skills = self.mgr.get_skills_learned()
        skill_names = [s["name"] for s in skills]
        self.assertIn("Prompt Engineering", skill_names)

        # Test PROJECT action tag
        text_proj = "Creo il progetto. [[ACTION:PROJECT | Progetto Drone | DroneCorp | Ricognizione aerea]] Fatto."
        clean_text, actions = self.bridge._execute_llm_action_tags(text_proj)
        projs = self.mgr.load_projects()
        found = any(p.get("name") == "Progetto Drone" for p in projs)
        self.assertTrue(found)

        # Test TODO action tag
        text_todo = "Aggiungo il task. [[ACTION:TODO | Progetto Drone – Assemblaggio rotori (lun 15/11)]] Aggiunto."
        clean_text, actions = self.bridge._execute_llm_action_tags(text_todo)
        todos = self.bridge.load_todos(as_strings=False)
        found_todo = any("Assemblaggio rotori" in t.get("title", "") for t in todos)
        self.assertTrue(found_todo)


class TestHttpApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import HTTPServer
        import threading
        cls.server = HTTPServer(("127.0.0.1", 0), CompanionRequestHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    def _http_get(self, path: str):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            return resp.status, json.loads(data.decode("utf-8"))

    def _http_post(self, path: str, payload: dict):
        import urllib.request
        url = f"http://127.0.0.1:{self.port}{path}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read()
            return resp.status, json.loads(data.decode("utf-8"))

    def test_skills_learned_http_crud(self):
        # 1. GET /api/skills_learned
        status, data = self._http_get("/api/skills_learned")
        self.assertEqual(status, 200)
        skills = data.get("skills_learned", [])
        self.assertIsInstance(skills, list)
        self.assertTrue(any(s["name"] == "Blender" for s in skills))

        # 2. POST /api/skills_learned/action - add
        status, res_add = self._http_post("/api/skills_learned/action", {
            "action": "add",
            "name": "Godot Engine",
            "category": "Game Engine",
            "level": "intermedio"
        })
        self.assertEqual(status, 200)
        self.assertEqual(res_add.get("status"), "ok")

        # Verify added in GET
        status, data_after = self._http_get("/api/skills_learned")
        skills_after = data_after.get("skills_learned", [])
        godot = next((s for s in skills_after if s["name"] == "Godot Engine"), None)
        self.assertIsNotNone(godot)
        self.assertEqual(godot["level"], "intermedio")

        # 3. POST /api/skills_learned/action - level
        status, res_lvl = self._http_post("/api/skills_learned/action", {
            "action": "level",
            "name": "Godot Engine",
            "level": "avanzato"
        })
        self.assertEqual(status, 200)

        # 4. POST /api/skills_learned/action - remove
        status, res_rm = self._http_post("/api/skills_learned/action", {
            "action": "remove",
            "name": "Godot Engine"
        })
        self.assertEqual(status, 200)

        # Verify removed
        status, data_final = self._http_get("/api/skills_learned")
        skills_final = data_final.get("skills_learned", [])
        self.assertFalse(any(s["name"] == "Godot Engine" for s in skills_final))

    def test_todos_kanban_http_crud(self):
        # 1. GET /api/todos
        status, data = self._http_get("/api/todos")
        self.assertEqual(status, 200)
        todos = data.get("todos", [])
        self.assertIsInstance(todos, list)

        # 2. POST /api/todos/action - add
        status, res_add = self._http_post("/api/todos/action", {
            "action": "add",
            "title": "Verifica finale collaudo scafo",
            "project": "Nave Intermarine",
            "due_label": "mer 11/11",
            "priority": "high",
            "subtasks": ["Controllo bulloneria", "Test tenuta stagna"]
        })
        self.assertEqual(status, 200)
        new_todo = res_add.get("item")
        self.assertIsNotNone(new_todo)
        todo_id = new_todo["id"]
        self.assertEqual(new_todo["project"], "Nave Intermarine")

        # 3. POST /api/todos/action - status
        status, res_stat = self._http_post("/api/todos/action", {
            "action": "status",
            "id": todo_id,
            "status": "doing"
        })
        self.assertEqual(status, 200)

        # 4. POST /api/todos/action - subtask_toggle
        status, res_sub = self._http_post("/api/todos/action", {
            "action": "subtask_toggle",
            "id": todo_id,
            "subtask_id": 0
        })
        self.assertEqual(status, 200)

        # 5. POST /api/todos/action - edit
        status, res_edit = self._http_post("/api/todos/action", {
            "action": "edit",
            "id": todo_id,
            "title": "Verifica finale collaudo scafo (Aggiornato)",
            "priority": "normal"
        })
        self.assertEqual(status, 200)

        # 6. POST /api/todos/action - delete
        status, res_del = self._http_post("/api/todos/action", {
            "action": "delete",
            "id": todo_id
        })
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
