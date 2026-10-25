"""
neko_bot.skills.sandbox - Static AST Security Guardrails & Isolated Subprocess Runner.

Implements R3.2 Automated Verification & Safety Sandbox:
1. AST Static Guardrails (universal '_' ban, dangerous builtin denylist, module allowlist, depth <= 25).
2. Subprocess Isolation Runner ('python -I', sterile tempdir, sterile env, 5.0s timeout).
3. Automated Self-Test Harness for skill validation.
"""

import ast
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("neko_bot.skills.sandbox")

ALLOWED_MODULES: Set[str] = {
    "math", "re", "json", "datetime", "random", "collections",
    "collections.abc", "itertools", "string", "hashlib", "urllib.parse", "decimal"
}

FORBIDDEN_CALLS: Set[str] = {
    "eval", "exec", "compile", "__import__", "open", "globals",
    "locals", "vars", "breakpoint"
}

FORBIDDEN_ATTRS: Set[str] = {
    "__subclasses__", "__bases__", "__base__", "__mro__", "__code__",
    "__globals__", "__builtins__", "__class__", "__traceback__",
    "tb_frame", "f_globals", "f_locals", "f_code", "f_back",
    "sys", "os", "subprocess", "modules", "system", "popen", "spawn",
    "open", "eval", "exec", "compile", "builtins", "__builtins__",
}

FORBIDDEN_BUILTINS: Set[str] = {
    "eval", "exec", "compile", "__import__", "open", "globals",
    "locals", "vars", "breakpoint", "input", "getattr", "setattr",
    "delattr", "hasattr", "type", "super", "memoryview", "classmethod",
    "staticmethod", "__build_class__", "dir", "help"
}

REDOS_NESTED_QUANTIFIERS_RE = re.compile(
    r"(\([^)]*[\+\*][^)]*\)[\+\*\{]|\([^)]*\|[^)]*\)[\+\*\{])"
)


def is_nested_quantifier_redos(pattern: str) -> bool:
    """Checks whether a regex pattern contains nested quantifiers prone to ReDoS."""
    if not pattern or not isinstance(pattern, str):
        return False
    return bool(REDOS_NESTED_QUANTIFIERS_RE.search(pattern))

MAX_AST_DEPTH = 25
DEFAULT_TIMEOUT_SEC = 5.0


class ASTValidator:
    """Performs static syntax and security guardrail verification on Python AST."""

    def __init__(self, allowed_modules: Optional[Set[str]] = None) -> None:
        self.allowed_modules = set(allowed_modules or ALLOWED_MODULES)

    def is_module_allowed(self, mod_name: str) -> bool:
        if not mod_name:
            return False
        return mod_name in self.allowed_modules

    def validate(self, code_str: str) -> Tuple[bool, str]:
        if not isinstance(code_str, str) or not code_str.strip():
            return False, "Skill code must be a non-empty string"

        try:
            tree = ast.parse(code_str)
        except SyntaxError as e:
            return False, f"SyntaxError: {e.msg} at line {e.lineno}"

        # Complexity / Nesting depth limit
        def check_depth(node: ast.AST, depth: int = 1) -> None:
            if depth > MAX_AST_DEPTH:
                raise ValueError(f"AST complexity limit exceeded (> {MAX_AST_DEPTH} levels)")
            for child in ast.iter_child_nodes(node):
                check_depth(child, depth + 1)

        try:
            check_depth(tree)
        except ValueError as e:
            return False, str(e)

        # Walk AST nodes
        for node in ast.walk(tree):
            # Check Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("_") or (alias.asname and alias.asname.startswith("_")):
                        return False, f"Forbidden import with leading underscore: {alias.name}"
                    if not self.is_module_allowed(alias.name):
                        return False, f"Forbidden import: {alias.name}"

            elif isinstance(node, ast.ImportFrom):
                if not node.module:
                    return False, "Relative imports are forbidden"
                if node.module.startswith("_"):
                    return False, f"Forbidden import from module with leading underscore: {node.module}"
                if not self.is_module_allowed(node.module):
                    return False, f"Forbidden import: {node.module}"
                for alias in node.names:
                    if alias.name.startswith("_") or (alias.asname and alias.asname.startswith("_")):
                        return False, f"Forbidden imported member: {alias.name}"

            # Universal ban on leading underscore on parameter and definition identifiers
            elif isinstance(node, ast.arg):
                if node.arg.startswith("_"):
                    return False, f"Forbidden parameter with leading underscore: {node.arg}"

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name.startswith("_"):
                    return False, f"Forbidden definition with leading underscore: {node.name}"

            # Universal ban on leading underscore in attribute access & dangerous system attributes
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("_"):
                    return False, f"Forbidden dunder attribute access: {node.attr}"
                if (
                    node.attr in FORBIDDEN_ATTRS
                    or node.attr in FORBIDDEN_CALLS
                    or node.attr in FORBIDDEN_BUILTINS
                ):
                    return False, f"Forbidden attribute access: {node.attr}"

            # Universal ban on leading underscore & dangerous builtins in identifiers
            elif isinstance(node, ast.Name):
                if node.id.startswith("_"):
                    return False, f"Forbidden identifier with leading underscore: {node.id}"
                if (
                    node.id in FORBIDDEN_CALLS
                    or node.id in FORBIDDEN_BUILTINS
                    or node.id in FORBIDDEN_ATTRS
                ):
                    return False, f"Forbidden builtin call: {node.id}"

            # Builtin & attribute function call checks
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if (
                        node.func.id in FORBIDDEN_CALLS
                        or node.func.id in FORBIDDEN_BUILTINS
                        or node.func.id in FORBIDDEN_ATTRS
                    ):
                        return False, f"Forbidden builtin call: {node.func.id}"
                    if node.func.id == "getattr":
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and (
                                str(arg.value).startswith("_")
                                or str(arg.value) in FORBIDDEN_ATTRS
                                or str(arg.value) in FORBIDDEN_CALLS
                                or str(arg.value) in FORBIDDEN_BUILTINS
                            ):
                                return False, f"Forbidden dunder attribute access via getattr: {arg.value}"
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr.startswith("_"):
                        return False, f"Forbidden dunder attribute access: {node.func.attr}"
                    if (
                        node.func.attr in FORBIDDEN_ATTRS
                        or node.func.attr in FORBIDDEN_CALLS
                        or node.func.attr in FORBIDDEN_BUILTINS
                    ):
                        return False, f"Forbidden attribute call: {node.func.attr}"

        return True, "AST verification passed"


class SkillSandbox:
    """
    Subprocess sandbox runner and skill verification engine.
    Satisfies PROJECT.md, ARCHITECTURE_CLOUD_ECOSYSTEM.md, and E2E test suite contracts.
    """

    ALLOWED_MODULES = ALLOWED_MODULES
    FORBIDDEN_CALLS = FORBIDDEN_CALLS
    FORBIDDEN_ATTRS = FORBIDDEN_ATTRS

    def __init__(self) -> None:
        self.validator = ASTValidator(self.ALLOWED_MODULES)

    def validate_ast(self, code_str: str) -> Tuple[bool, str]:
        """Validates code string against AST security guardrails."""
        return self.validator.validate(code_str)

    def run_tool_in_sandbox(
        self,
        code_str: str,
        inputs: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> Dict[str, Any]:
        """
        Executes a Python tool skill within an isolated subprocess runner.
        Uses 'python -I', sterile tempdir, sterile env, 5.0s timeout, and runtime safe_builtins.
        """
        valid, msg = self.validate_ast(code_str)
        if not valid:
            return {"success": False, "result": {}, "error": f"AST Validation Failed: {msg}"}

        with tempfile.TemporaryDirectory(prefix="neko_sandbox_") as tmpdir:
            script_path = Path(tmpdir) / "tool.py"
            runner_path = Path(tmpdir) / "runner.py"

            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code_str)

            runner_code = """
import sys, os, json, builtins

ALLOWED_MODULES = {
    'math', 're', 'json', 'datetime', 'random', 'collections',
    'collections.abc', 'itertools', 'string', 'hashlib', 'urllib.parse', 'decimal'
}

SAFE_BUILTIN_ALLOWLIST = {
    'abs', 'all', 'any', 'bin', 'bool', 'bytearray', 'bytes', 'chr', 'complex',
    'dict', 'divmod', 'enumerate', 'filter', 'float', 'format', 'frozenset',
    'hash', 'hex', 'int', 'isinstance', 'issubclass', 'iter', 'len', 'list',
    'map', 'max', 'min', 'next', 'oct', 'ord', 'pow', 'print', 'range', 'repr',
    'reversed', 'round', 'set', 'slice', 'sorted', 'str', 'sum', 'tuple', 'zip',
    'True', 'False', 'None', 'Exception', 'ValueError', 'TypeError', 'KeyError',
    'IndexError', 'ZeroDivisionError', 'RuntimeError', 'AttributeError', 'ImportError'
}

real_import = __import__
def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name not in ALLOWED_MODULES:
        raise ImportError(f"Import of '{name}' is forbidden in sandbox")
    return real_import(name, globals, locals, fromlist, level)

safe_builtins = {k: getattr(builtins, k) for k in dir(builtins) if k in SAFE_BUILTIN_ALLOWLIST}
safe_builtins['__import__'] = safe_import

try:
    raw_input = sys.stdin.read()
    data = json.loads(raw_input) if raw_input.strip() else {}
    inputs = data.get("inputs", {})
    if inputs is None:
        inputs = {}
    context = data.get("context", {})
    if context is None:
        context = {}

    tool_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool.py")
    with open(tool_path, "r", encoding="utf-8") as f:
        tool_code = f.read()

    # Sanitize sys.modules to purge dangerous host modules
    for mod in ['os', 'subprocess', 'shutil', 'socket', 'ctypes']:
        sys.modules.pop(mod, None)

    tool_ns = {"__builtins__": safe_builtins}
    exec(compile(tool_code, "tool.py", "exec"), tool_ns)

    if "run" not in tool_ns or not callable(tool_ns["run"]):
        raise ValueError("Tool code must define a callable 'run(inputs, context)' function")

    res = tool_ns["run"](inputs, context)
    if not isinstance(res, dict):
        res = {"result": res}
    print(json.dumps({"success": True, "result": res, "error": None}))
except Exception as e:
    err_msg = str(e) or type(e).__name__
    print(json.dumps({"success": False, "result": {}, "error": err_msg}))
"""
            with open(runner_path, "w", encoding="utf-8") as f:
                f.write(runner_code)

            payload = json.dumps({"inputs": inputs if inputs is not None else {}, "context": context if context is not None else {}})

            # Sterile Environment: purge all host secrets, user tokens, API keys
            sys_root = os.environ.get("SYSTEMROOT", "C:\\Windows")
            python_dir = os.path.dirname(sys.executable)
            sterile_path = os.pathsep.join([python_dir, os.path.join(sys_root, "System32"), sys_root])
            env = {
                "SYSTEMROOT": sys_root,
                "SystemDrive": os.environ.get("SystemDrive", "C:"),
                "PATH": sterile_path,
                "PYTHONUNBUFFERED": "1",
            }

            try:
                proc = subprocess.run(
                    [sys.executable, "-I", str(runner_path)],
                    input=payload,
                    text=True,
                    capture_output=True,
                    timeout=timeout_sec,
                    cwd=tmpdir,
                    env=env,
                )

                if proc.returncode != 0:
                    out_text = proc.stdout.strip()
                    if out_text:
                        try:
                            parsed = json.loads(out_text)
                            if isinstance(parsed, dict) and "success" in parsed:
                                return parsed
                        except Exception:
                            pass
                    return {
                        "success": False,
                        "result": {},
                        "error": proc.stderr.strip() or f"Exited code {proc.returncode}",
                    }

                out_str = proc.stdout.strip()
                try:
                    return json.loads(out_str)
                except json.JSONDecodeError:
                    # Look for JSON payload in lines
                    for line in reversed(out_str.splitlines()):
                        line_str = line.strip()
                        if line_str.startswith("{") and line_str.endswith("}"):
                            try:
                                parsed = json.loads(line_str)
                                if isinstance(parsed, dict) and "success" in parsed:
                                    return parsed
                            except Exception:
                                continue
                    return {
                        "success": False,
                        "result": {},
                        "error": f"Invalid JSON stdout: {proc.stdout}",
                    }

            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "result": {},
                    "error": f"Execution timed out after {timeout_sec}s",
                }

    def verify_skill(self, skill_def: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Verifies a skill definition prior to catalog registration.
        Validates structure for workflows; executes AST inspection and self-tests for tools.
        """
        patterns = skill_def.get("trigger_patterns", [])
        if isinstance(patterns, list):
            for pat in patterns:
                if isinstance(pat, str) and is_nested_quantifier_redos(pat):
                    return False, f"Trigger pattern contains nested quantifiers prone to ReDoS: {pat}"

        stype = skill_def.get("type") or skill_def.get("skill_type") or "tool"
        stype = "workflow" if "workflow" in str(stype).lower() else "tool"

        if stype == "workflow":
            steps = skill_def.get("steps", [])
            if not isinstance(steps, list) or len(steps) == 0:
                return False, "Workflow must contain at least one step"
            for step in steps:
                if not isinstance(step, dict) or "action" not in step or not str(step["action"]).strip():
                    return False, "Workflow step missing action"
            return True, "Workflow verified"

        else:
            code = skill_def.get("code") or skill_def.get("code_str") or ""
            if not isinstance(code, str) or not code.strip():
                return False, "Skill code must be a non-empty string"

            valid, msg = self.validate_ast(code)
            if not valid:
                return False, msg

            self_tests = skill_def.get("self_tests", [])
            for test in self_tests:
                inp = test.get("inputs", {})
                expected = test.get("expected_output", {})
                ctx = test.get("context", {})
                t_limit = float(test.get("timeout_sec", DEFAULT_TIMEOUT_SEC))
                res = self.run_tool_in_sandbox(code, inp, ctx, timeout_sec=t_limit)
                if not res.get("success"):
                    return False, f"Self-test failed: {res.get('error')}"
                if res.get("result") != expected:
                    return False, f"Output mismatch: got {res.get('result')}, expected {expected}"

            return True, "All self-tests passed"
