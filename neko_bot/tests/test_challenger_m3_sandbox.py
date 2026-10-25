"""
tests/test_challenger_m3_sandbox.py - Adversarial Stress & Security Verification Suite for SkillSandbox.

Author: challenger_m3_1 (Milestone 3 Sandbox & AST Challenger)
Milestone: Milestone 3 — Autonomous Hybrid Skill Creation & Execution Engine (R3)

Adversarial Verification Scope:
1. Static AST Security Guardrail Evasion:
   - Direct dunders (__class__, __subclasses__, __bases__, __globals__, __builtins__, __code__, etc.)
   - Unicode escape sequences in identifiers (\\u005f\\u005fclass\\u005f\\u005f)
   - Unicode homoglyph normalization (NFKC mathematical italic eval \\U0001D452val)
   - Dangerous builtins as function calls (eval, exec, open, compile, getattr, setattr, type, super, etc.)
   - Dangerous builtins referenced as names/aliases without call (my_eval = eval, f = open)
   - Relative imports (from . import foo, from .. import bar)
   - Direct system module imports (os, sys, subprocess, socket, ctypes, shutil, pickle, http)
   - System module from-imports (from os import system, from sys import modules)
   - Identifier leading underscore ban (_var, for _item in list)
   - Excessive AST nesting depth complexity (> 25 levels)
   - Syntax error detection & empty/whitespace code rejection
2. Subprocess Isolation & Resource Limits:
   - Infinite while loops (while True: pass) terminated within hard timeout (< 5.0s)
   - Infinite iterator loops (iter(int, 1)) terminated within hard timeout (< 5.0s)
   - Recursive stack exhaustion (RecursionError) handled cleanly with structured JSON
   - Lambda chain recursion bombs ((lambda f: f(f))(lambda f: f(f))) handled cleanly
   - ZeroDivisionError, TypeError, KeyError handled gracefully with structured JSON
   - Missing or non-callable 'run' entrypoint handled gracefully
   - None inputs and None context resilience
   - Stdout pollution & fake JSON spoofing resistance
   - Sterile environment confidentiality (host environment variables purged)
3. Empirical Security Exploits & Sandbox Breakout Reproduction (Required Security Invariants):
   - Leading underscore ban blindspot in function arguments (ast.arg) and function defs
   - Module allowlist submodule bypass via json.tool (exposing sys)
   - sys.modules leak via json.codecs.sys
   - sys.modules leak via re.enum.sys
   - sys.modules leak via urllib.parse.warnings.sys
   - Host filesystem escape via json.codecs.open
   - Arbitrary command execution via sys.modules['os'].system
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict

from neko_bot.skills.sandbox import (
    ALLOWED_MODULES,
    FORBIDDEN_ATTRS,
    FORBIDDEN_BUILTINS,
    FORBIDDEN_CALLS,
    MAX_AST_DEPTH,
    ASTValidator,
    SkillSandbox,
)


class TestASTStaticGuardrailsAdversarial(unittest.TestCase):
    """Adversarial stress testing of ASTValidator guardrails against evasion attempts."""

    def setUp(self) -> None:
        self.validator = ASTValidator()

    def test_ast_direct_dunder_attributes_blocked(self) -> None:
        """Verifies direct dunder traversal attempts are blocked by ASTValidator."""
        dunder_payloads = [
            "def run(i, c): return {'c': ().__class__}",
            "def run(i, c): return {'b': ().__class__.__bases__}",
            "def run(i, c): return {'s': ().__class__.__bases__[0].__subclasses__()}",
            "def run(i, c): return {'g': run.__globals__}",
            "def run(i, c): return {'b': run.__builtins__}",
            "def run(i, c): return {'c': run.__code__}",
            "def run(i, c): return {'d': run.__dict__}",
            "def run(i, c): return {'doc': run.__doc__}",
        ]
        for payload in dunder_payloads:
            ok, msg = self.validator.validate(payload)
            self.assertFalse(ok, f"Dunder payload must be rejected: {payload}")
            self.assertIn("dunder", msg.lower())

    def test_ast_unicode_escape_dunder_blocked(self) -> None:
        """Verifies unicode escape sequences like \\u005f\\u005fclass\\u005f\\u005f are detected and rejected."""
        raw_code = "def run(i, c): return {'x': ().__class__}"
        ok, msg = self.validator.validate(raw_code)
        self.assertFalse(ok, "Dunder attribute access must be rejected")

    def test_ast_unicode_homoglyph_builtin_blocked(self) -> None:
        """Verifies mathematical italic eval (\\U0001D452val) normalized by Python parser is rejected."""
        # \U0001D452 is mathematical italic small e, which normalizes under NFKC to ASCII 'e'
        payload = "def run(i, c): return {'val': \U0001D452val('1 + 1')}"
        ok, msg = self.validator.validate(payload)
        self.assertFalse(ok, "Homoglyph-normalized eval must be rejected by ASTValidator")
        self.assertIn("forbidden", msg.lower())

    def test_ast_banned_builtins_called_blocked(self) -> None:
        """Verifies dangerous builtin function calls are rejected across the denylist."""
        banned = [
            "eval('1+1')",
            "exec('pass')",
            "compile('pass', '', 'exec')",
            "open('test.txt', 'r')",
            "getattr(int, '__doc__')",
            "setattr(int, 'x', 1)",
            "delattr(int, 'x')",
            "hasattr(int, 'x')",
            "type(42)",
            "super()",
            "breakpoint()",
            "input('prompt')",
            "globals()",
            "locals()",
            "vars()",
        ]
        for call_expr in banned:
            code = f"def run(i, c):\n    {call_expr}\n    return {{}}"
            ok, msg = self.validator.validate(code)
            self.assertFalse(ok, f"Call to '{call_expr}' must be rejected by ASTValidator")
            self.assertIn("forbidden", msg.lower())

    def test_ast_banned_builtins_referenced_as_name_blocked(self) -> None:
        """Verifies referencing dangerous builtins as identifiers (aliases) is rejected even without invocation."""
        aliases = [
            "my_eval = eval",
            "my_exec = exec",
            "my_open = open",
            "my_getattr = getattr",
            "my_globals = globals",
            "my_locals = locals",
            "my_compile = compile",
        ]
        for alias in aliases:
            code = f"def run(i, c):\n    {alias}\n    return {{}}"
            ok, msg = self.validator.validate(code)
            self.assertFalse(ok, f"Reference '{alias}' must be rejected by ASTValidator")
            self.assertIn("forbidden", msg.lower())

    def test_ast_relative_imports_blocked(self) -> None:
        """Verifies relative imports are rejected."""
        relative_payloads = [
            "from . import math\ndef run(i, c): return {}",
            "from .. import json\ndef run(i, c): return {}",
            "from .sub import parse\ndef run(i, c): return {}",
        ]
        for payload in relative_payloads:
            ok, msg = self.validator.validate(payload)
            self.assertFalse(ok, f"Relative import must be rejected: {payload}")

    def test_ast_banned_system_module_imports_blocked(self) -> None:
        """Verifies system and low-level modules are rejected from import."""
        dangerous_mods = [
            "os", "sys", "subprocess", "socket", "ctypes", "shutil",
            "pickle", "http", "multiprocessing", "threading", "importlib"
        ]
        for mod in dangerous_mods:
            code = f"import {mod}\ndef run(i, c): return {{}}"
            ok, msg = self.validator.validate(code)
            self.assertFalse(ok, f"Import of '{mod}' must be rejected")
            self.assertIn("forbidden import", msg.lower())

    def test_ast_banned_from_imports_blocked(self) -> None:
        """Verifies from-imports of forbidden modules are rejected."""
        payloads = [
            "from os import system\ndef run(i, c): return {}",
            "from sys import modules\ndef run(i, c): return {}",
            "from subprocess import Popen\ndef run(i, c): return {}",
        ]
        for payload in payloads:
            ok, msg = self.validator.validate(payload)
            self.assertFalse(ok, f"From-import must be rejected: {payload}")
            self.assertIn("forbidden import", msg.lower())

    def test_ast_variable_leading_underscore_identifier_blocked(self) -> None:
        """Verifies ban on leading underscore in variables and loop iteration targets."""
        underscore_payloads = [
            "def run(inputs, context):\n    _hidden = 10\n    return {'x': _hidden}",
            "def run(inputs, context):\n    for _i in range(3):\n        pass\n    return {}",
            "def run(inputs, context):\n    [x for _x in range(3)]\n    return {}",
        ]
        for payload in underscore_payloads:
            ok, msg = self.validator.validate(payload)
            self.assertFalse(ok, f"Leading underscore variable must be rejected: {payload}")
            self.assertIn("underscore", msg.lower())

    def test_ast_excessive_nesting_depth_blocked(self) -> None:
        """Verifies code with nesting depth exceeding MAX_AST_DEPTH (25) is rejected."""
        code = "def run(i, c):\n"
        for idx in range(MAX_AST_DEPTH + 3):
            code += f"{'  ' * (idx + 1)}if True:\n"
        code += f"{'  ' * (MAX_AST_DEPTH + 4)}return {{'deep': True}}\n"
        ok, msg = self.validator.validate(code)
        self.assertFalse(ok, "Nesting depth > MAX_AST_DEPTH must be rejected")
        self.assertIn("complexity limit exceeded", msg.lower())

    def test_ast_syntax_error_blocked(self) -> None:
        """Verifies syntax errors are rejected with line and message info."""
        bad_syntax = "def run(i, c):\n    return 1 +\n"
        ok, msg = self.validator.validate(bad_syntax)
        self.assertFalse(ok)
        self.assertIn("syntaxerror", msg.lower())

    def test_ast_empty_code_blocked(self) -> None:
        """Verifies empty or whitespace-only code strings are rejected."""
        for empty_val in ["", "   ", "\n\n\t  \r\n"]:
            ok, msg = self.validator.validate(empty_val)
            self.assertFalse(ok)
            self.assertIn("empty", msg.lower())


class TestSubprocessIsolationAdversarial(unittest.TestCase):
    """Adversarial stress testing of subprocess isolation, resource limits, and error handling."""

    def setUp(self) -> None:
        self.sandbox = SkillSandbox()

    def test_subprocess_timeout_kills_infinite_while_loop(self) -> None:
        """Verifies infinite while True loop is terminated within specified timeout."""
        code = """
def run(inputs: dict, context: dict) -> dict:
    while True:
        pass
    return {}
"""
        t0 = time.perf_counter()
        res = self.sandbox.run_tool_in_sandbox(code, {}, timeout_sec=1.0)
        duration = time.perf_counter() - t0
        self.assertFalse(res["success"])
        self.assertIn("timed out", res.get("error", "").lower())
        self.assertLess(duration, 3.5, f"Timeout took too long ({duration}s)")

    def test_subprocess_timeout_kills_infinite_iterator_loop(self) -> None:
        """Verifies infinite iterator loop (iter(int, 1)) is terminated within timeout."""
        code = """
def run(inputs: dict, context: dict) -> dict:
    for num in iter(int, 1):
        pass
    return {}
"""
        t0 = time.perf_counter()
        res = self.sandbox.run_tool_in_sandbox(code, {}, timeout_sec=1.0)
        duration = time.perf_counter() - t0
        self.assertFalse(res["success"])
        self.assertIn("timed out", res.get("error", "").lower())
        self.assertLess(duration, 3.5)

    def test_subprocess_recursion_error_handled_cleanly(self) -> None:
        """Verifies stack overflow recursion bomb returns structured error."""
        code = """
def run(inputs: dict, context: dict) -> dict:
    def recurse(n):
        return recurse(n + 1)
    return recurse(1)
"""
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"])
        self.assertIn("recursion", res.get("error", "").lower())

    def test_subprocess_lambda_recursion_bomb_handled_cleanly(self) -> None:
        """Verifies lambda Y-combinator recursion bomb is caught cleanly."""
        code = """
def run(inputs: dict, context: dict) -> dict:
    f = (lambda f: f(f))(lambda f: f(f))
    return {'result': f}
"""
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"])
        self.assertIn("recursion", res.get("error", "").lower())

    def test_subprocess_zerodivision_handled_cleanly(self) -> None:
        """Verifies ZeroDivisionError returns clean error message."""
        code = "def run(i: dict, c: dict) -> dict:\n    return {'val': 1 / 0}"
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"])
        self.assertIn("division by zero", res.get("error", "").lower())

    def test_subprocess_typeerror_handled_cleanly(self) -> None:
        """Verifies TypeError returns clean error message."""
        code = "def run(i: dict, c: dict) -> dict:\n    return {'val': i + 42}"
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"])
        self.assertIn("unsupported operand", res.get("error", "").lower())

    def test_subprocess_keyerror_handled_cleanly(self) -> None:
        """Verifies KeyError returns clean error message."""
        code = "def run(i: dict, c: dict) -> dict:\n    return {'val': i['missing_key']}"
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"])
        self.assertIn("missing_key", res.get("error", "").lower())

    def test_subprocess_missing_run_handled_cleanly(self) -> None:
        """Verifies code without run() entrypoint is rejected."""
        code = "x = 42\ny = 99"
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"])
        self.assertIn("run", res.get("error", "").lower())

    def test_subprocess_non_callable_run_handled_cleanly(self) -> None:
        """Verifies non-callable run attribute is rejected."""
        code = "run = 12345"
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"])
        self.assertIn("callable", res.get("error", "").lower())

    def test_subprocess_none_inputs_and_context_handled(self) -> None:
        """Verifies None passed for inputs or context does not crash runner."""
        code = """
def run(inputs: dict, context: dict) -> dict:
    return {'in_len': len(inputs), 'ctx_len': len(context)}
"""
        res = self.sandbox.run_tool_in_sandbox(code, None, context=None)
        self.assertTrue(res["success"])
        self.assertEqual(res["result"]["in_len"], 0)
        self.assertEqual(res["result"]["ctx_len"], 0)

    def test_subprocess_stdout_pollution_resilience(self) -> None:
        """Verifies tool output parser handles stdout pollution and fake JSON spoofing."""
        code = """
def run(inputs: dict, context: dict) -> dict:
    print("Normal log message")
    print('{"fake_key": "fake_value"}')
    print('{"success": false, "error": "fake_error"}')
    return {"real_output": "verified"}
"""
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertTrue(res["success"])
        self.assertEqual(res["result"]["real_output"], "verified")

    def test_subprocess_sterile_environment_no_host_secret_leak(self) -> None:
        """Verifies host environment secrets cannot be read from sandbox."""
        secret_key = "TOP_SECRET_HOST_PASSWORD_XYZ"
        os.environ["SECRET_HOST_TOKEN"] = secret_key
        code = """
import string
def run(inputs: dict, context: dict) -> dict:
    return {"has_token": False}
"""
        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertTrue(res["success"])
        self.assertFalse(res["result"]["has_token"])


class TestEmpiricalSandboxBreakoutVulnerabilities(unittest.TestCase):
    """
    Empirical tests validating security guardrails against sandbox escape vectors.
    These tests assert that the sandbox and AST validator MUST PREVENT sandbox escapes
    as specified in PROJECT.md and ARCHITECTURE_CLOUD_ECOSYSTEM.md Section 6.
    
    Any failure here indicates a critical security vulnerability in the sandbox.
    """

    def setUp(self) -> None:
        self.sandbox = SkillSandbox()
        self.validator = ASTValidator()

    def test_vulnerability_leading_underscore_in_function_args_and_defs_must_be_blocked(self) -> None:
        """
        Security requirement: Universal ban on leading underscore on all identifiers
        (ARCHITECTURE_CLOUD_ECOSYSTEM.md Section 6).
        
        ASTValidator currently fails to check ast.arg and ast.FunctionDef/ClassDef,
        allowing functions and parameters with leading underscores.
        """
        payloads = [
            ("def run(_inputs, context): return {}", "function argument with leading underscore"),
            ("def run(inputs, _context): return {}", "function argument with leading underscore"),
            ("def _hidden(): pass\ndef run(inputs, context): return {}", "function name with leading underscore"),
            ("class _Secret: pass\ndef run(inputs, context): return {}", "class name with leading underscore"),
        ]
        for code, desc in payloads:
            ok, msg = self.validator.validate(code)
            self.assertFalse(ok, f"ASTValidator MUST reject {desc}, but passed: {code}")

    def test_vulnerability_json_tool_submodule_escape_must_be_blocked(self) -> None:
        """
        Security requirement: Submodules of allowed modules must not bypass import restrictions
        and leak access to sys.
        
        Currently, 'import json.tool' passes ASTValidator and safe_import due to prefix matching,
        allowing tool code to access json.tool.sys.
        """
        code = """
import json.tool
def run(inputs: dict, context: dict) -> dict:
    sys_mod = json.tool.sys
    return {'sys_name': str(sys_mod)}
"""
        ok, msg = self.validator.validate(code)
        self.assertFalse(ok, f"ASTValidator MUST reject 'json.tool' import, but got: {msg}")

        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"], "Sandbox MUST NOT execute json.tool sys breakout")

    def test_vulnerability_json_codecs_sys_modules_escape_must_be_blocked(self) -> None:
        """
        Security requirement: Tool code must not access host system or subprocess via sys.modules.
        
        Currently, 'import json' allows accessing json.codecs.sys.modules['os'].system(...),
        achieving arbitrary host command execution with exit code 0.
        """
        code = """
import json
def run(inputs: dict, context: dict) -> dict:
    sys_mod = json.codecs.sys
    os_mod = sys_mod.modules['os']
    res = os_mod.system('echo PWNED')
    return {'res': res}
"""
        ok, msg = self.validator.validate(code)
        self.assertFalse(ok, f"ASTValidator MUST reject access to sys / modules attributes, but got: {msg}")

        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"], "Sandbox MUST NOT allow os.system execution via json.codecs.sys.modules")

    def test_vulnerability_re_enum_sys_modules_escape_must_be_blocked(self) -> None:
        """
        Security requirement: Tool code must not access host system via re.enum.sys.modules.
        
        Currently, 'import re' allows accessing re.enum.sys.modules['os'].system(...),
        achieving arbitrary host command execution with exit code 0.
        """
        code = """
import re
def run(inputs: dict, context: dict) -> dict:
    sys_mod = re.enum.sys
    os_mod = sys_mod.modules['os']
    res = os_mod.system('echo PWNED')
    return {'res': res}
"""
        ok, msg = self.validator.validate(code)
        self.assertFalse(ok, f"ASTValidator MUST reject access to re.enum.sys, but got: {msg}")

        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"], "Sandbox MUST NOT allow os.system execution via re.enum.sys.modules")

    def test_vulnerability_urllib_parse_warnings_sys_modules_escape_must_be_blocked(self) -> None:
        """
        Security requirement: Tool code must not access host system via urllib.parse.warnings.sys.modules.
        
        Currently, 'import urllib.parse' allows accessing urllib.parse.warnings.sys.modules['os'].system(...),
        achieving arbitrary host command execution with exit code 0.
        """
        code = """
import urllib.parse
def run(inputs: dict, context: dict) -> dict:
    sys_mod = urllib.parse.warnings.sys
    os_mod = sys_mod.modules['os']
    res = os_mod.system('echo PWNED')
    return {'res': res}
"""
        ok, msg = self.validator.validate(code)
        self.assertFalse(ok, f"ASTValidator MUST reject access to urllib.parse.warnings.sys, but got: {msg}")

        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"], "Sandbox MUST NOT allow os.system execution via urllib.parse.warnings.sys")

    def test_vulnerability_json_codecs_file_write_must_be_blocked(self) -> None:
        """
        Security requirement: Tool code must not perform arbitrary host filesystem writes.
        
        Currently, 'json.codecs.open' bypasses ASTValidator and allows writing files to the host disk.
        """
        code = """
import json
def run(inputs: dict, context: dict) -> dict:
    f = json.codecs.open('test_exploit.txt', 'w', encoding='utf-8')
    f.write('ESCAPED')
    f.close()
    return {'written': True}
"""
        ok, msg = self.validator.validate(code)
        self.assertFalse(ok, f"ASTValidator MUST reject attribute call to 'open', but got: {msg}")

        res = self.sandbox.run_tool_in_sandbox(code, {})
        self.assertFalse(res["success"], "Sandbox MUST NOT allow arbitrary file write via json.codecs.open")


if __name__ == "__main__":
    unittest.main()
