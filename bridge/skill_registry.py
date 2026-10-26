"""
bridge/skill_registry.py
========================
Declarative Skill Registry, 3 Safety Tiers, AST & Regex Safety Validation Engine,
and Autonomous Skill Synthesis for AsciiNeko (Phase 3: F25, F26, F27, F28).

Safety Tiers:
- Tier 1 (READ_ONLY): Silent direct execution in background (telemetry, query, listing).
- Tier 2 (CONFIRM_ONCE): Inline confirmation popover required before action (render, sync, write).
- Tier 3 (DESTRUCTIVE_REQUIRE_AUTH): Full modal authentication required (delete, reset, wipe).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

logger = logging.getLogger("AsciiNeko.SkillRegistry")

# Disallowed regex pattern blocking dangerous shell calls and escapes
DISALLOWED_REGEX = re.compile(
    r"(?:\b(rmdir|del\s+/f|DROP\s+TABLE|reg\s+delete|powershell\s+-enc|Invoke-Expression|iex)\b|\bformat\s+[a-z]:(?:\s|$))",
    re.IGNORECASE,
)

DISALLOWED_MODULES = {"os", "subprocess", "shutil", "ctypes", "sys", "importlib"}
DISALLOWED_FUNCTIONS = {"eval", "exec", "__import__"}

SAFETY_TIERS = {"READ_ONLY", "CONFIRM_ONCE", "DESTRUCTIVE_REQUIRE_AUTH"}


def validate_code_safety(code: str) -> Tuple[bool, str]:
    """
    F27: AST & Regex Safety Validation.
    1. Scans code string against destructive shell regex blacklist.
    2. Parses Python code via ast.parse and verifies imports/function calls.
    """
    if not code:
        return True, "Codice sicuro"

    # Step 1: Regex scan
    if DISALLOWED_REGEX.search(code):
        return False, "Pattern distruttivo bloccato dal filtro regex"

    # Step 2: AST scan (for Python code/scripts)
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in DISALLOWED_MODULES:
                        return False, f"Modulo non autorizzato: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                if mod in DISALLOWED_MODULES:
                    return False, f"Modulo non autorizzato: {node.module}"
            elif isinstance(node, ast.Call):
                func = node.func
                func_name = None
                if isinstance(func, ast.Name):
                    func_name = func.id
                elif isinstance(func, ast.Attribute):
                    func_name = func.attr
                if func_name in DISALLOWED_FUNCTIONS:
                    return False, f"Chiamata pericolosa: {func_name}"
    except SyntaxError:
        # If not standard Python (e.g. pure MaxScript or custom shell template),
        # verify no dangerous regex pattern leaked through
        pass

    return True, "Codice sicuro"


def validate_skill_schema(skill: Dict[str, Any]) -> Tuple[bool, str]:
    """
    F25: Declarative Skill Schema Validator.
    Ensures required fields and valid safety tier.
    """
    required = ["schema_version", "id", "name", "safety_tier", "trigger_patterns", "execution_contract"]
    for f in required:
        if f not in skill:
            return False, f"Campo mancante: {f}"

    tier = skill.get("safety_tier")
    if tier not in SAFETY_TIERS:
        return False, f"Safety tier non valido: {tier}"

    return True, "Valido"


class SkillRegistry:
    """
    Manages declarative skills loaded from YAML/JSON files and built-in system skills.
    Enforces 3 Safety Tiers, AST/Regex verification, and autonomous synthesis.
    """

    def __init__(self, skills_dir: Optional[str] = None, storage_manager: Any = None):
        self._lock = threading.RLock()
        self.storage_manager = storage_manager

        if skills_dir is not None:
            self.skills_dir = os.path.abspath(skills_dir)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.skills_dir = os.path.join(base_dir, "data", "skills")

        os.makedirs(self.skills_dir, exist_ok=True)

        self._skills: Dict[str, Dict[str, Any]] = {}
        self._action_logs: List[Dict[str, Any]] = []

        # Load built-ins and directory skills
        self._register_builtins()
        self.load_skills()

    def _register_builtins(self) -> None:
        """Register default built-in commands/skills."""
        builtins = [
            {
                "schema_version": "1.0",
                "id": "briefing",
                "name": "Briefing Oggi",
                "description": "Riepilogo giornaliero impegni, progetti e stato PC",
                "safety_tier": "READ_ONLY",
                "trigger_patterns": ["/briefing", "briefing", "riassumi oggi"],
                "parameters": [],
                "execution_contract": {"type": "INTERNAL_ACTION", "action": "show_briefing"},
                "is_builtin": True,
            },
            {
                "schema_version": "1.0",
                "id": "calendar",
                "name": "Agenda & Calendario",
                "description": "Visualizza prossimi eventi Google Calendar",
                "safety_tier": "READ_ONLY",
                "trigger_patterns": ["/cal", "calendario", "agenda"],
                "parameters": [],
                "execution_contract": {"type": "INTERNAL_ACTION", "action": "show_calendar"},
                "is_builtin": True,
            },
            {
                "schema_version": "1.0",
                "id": "todo",
                "name": "To-Do Drawer",
                "description": "Apri il cassetto laterale delle attività",
                "safety_tier": "READ_ONLY",
                "trigger_patterns": ["/todo", "todo", "compiti"],
                "parameters": [],
                "execution_contract": {"type": "INTERNAL_ACTION", "action": "open_todo_drawer"},
                "is_builtin": True,
            },
            {
                "schema_version": "1.0",
                "id": "profilo",
                "name": "Ispettore Profilo",
                "description": "Visualizza, pinna, modifica o elimina regole e tratti",
                "safety_tier": "CONFIRM_ONCE",
                "trigger_patterns": ["/profilo", "profilo", "memorie"],
                "parameters": [],
                "execution_contract": {"type": "INTERNAL_ACTION", "action": "open_profile_inspector"},
                "is_builtin": True,
            },
            {
                "schema_version": "1.0",
                "id": "colorsafe",
                "name": "Color-Safe Mode",
                "description": "Attiva/disattiva scanlines e vignette CRT",
                "safety_tier": "READ_ONLY",
                "trigger_patterns": ["/colorsafe", "colorsafe", "colore sicuro"],
                "parameters": [],
                "execution_contract": {"type": "INTERNAL_ACTION", "action": "toggle_colorsafe"},
                "is_builtin": True,
            },
            {
                "schema_version": "1.0",
                "id": "sync",
                "name": "Sincronizza PC",
                "description": "Sincronizzazione dati e log tra PC lavoro e casa",
                "safety_tier": "CONFIRM_ONCE",
                "trigger_patterns": ["/sync", "sincronizza", "git sync"],
                "parameters": [],
                "execution_contract": {"type": "INTERNAL_ACTION", "action": "sync_workspace"},
                "is_builtin": True,
            },
            {
                "schema_version": "1.0",
                "id": "dcc_backup",
                "name": "Backup Scena DCC",
                "description": "Salva copia e incrementa versione file 3D",
                "safety_tier": "CONFIRM_ONCE",
                "trigger_patterns": ["/dcc_backup", "backup scena", "salva incrementale"],
                "parameters": [{"name": "format", "type": "string", "default": "max"}],
                "execution_contract": {
                    "type": "NAMED_PIPE_DCC",
                    "target_dcc": "3dsmax",
                    "command_template": "max_save_incremental()",
                },
                "is_builtin": True,
            },
        ]
        for s in builtins:
            self._skills[s["id"]] = s

    def load_skills(self) -> List[Dict[str, Any]]:
        """
        Loads all declarative YAML/JSON files from skills_dir and updates registry.
        Skips invalid schemas or unsafe commands with warning logs.
        """
        with self._lock:
            if os.path.exists(self.skills_dir):
                for fname in sorted(os.listdir(self.skills_dir)):
                    fpath = os.path.join(self.skills_dir, fname)
                    if not os.path.isfile(fpath):
                        continue

                    skill_data = None
                    if fname.endswith((".yaml", ".yml")):
                        if HAS_YAML:
                            try:
                                with open(fpath, "r", encoding="utf-8") as f:
                                    skill_data = yaml.safe_load(f)
                            except Exception as e:
                                logger.warning("Failed to parse YAML skill %s: %s", fname, e)
                        else:
                            # Fallback basic YAML parser for simple key-value/lists
                            pass
                    elif fname.endswith(".json"):
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                skill_data = json.load(f)
                        except Exception as e:
                            logger.warning("Failed to parse JSON skill %s: %s", fname, e)

                    if isinstance(skill_data, dict):
                        ok, msg = self.validate_skill(skill_data)
                        if ok:
                            sid = skill_data["id"]
                            self._skills[sid] = skill_data
                            logger.debug("Loaded skill [%s] from %s", sid, fname)
                        else:
                            logger.warning("Skill %s validation failed: %s", fname, msg)

            return list(self._skills.values())

    def validate_skill(self, skill_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates schema, safety tiers, and code safety of command templates.
        """
        ok, msg = validate_skill_schema(skill_data)
        if not ok:
            return False, msg

        # Inspect execution contract commands
        contract = skill_data.get("execution_contract", {})
        cmd_template = contract.get("command_template") or contract.get("script") or ""
        if cmd_template:
            c_ok, c_msg = validate_code_safety(cmd_template)
            if not c_ok:
                return False, f"Violazione di sicurezza: {c_msg}"

        return True, "Valido"

    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves registered skill by ID."""
        with self._lock:
            return self._skills.get(skill_id)

    def find_matching_skills(self, query_or_cmd: str) -> List[Dict[str, Any]]:
        """Finds skills matching a slash command or natural language trigger."""
        q = query_or_cmd.strip().lower()
        matches = []
        with self._lock:
            for s in self._skills.values():
                triggers = [t.lower() for t in s.get("trigger_patterns", [])]
                if any(q == t or q.startswith(t) or t in q for t in triggers):
                    matches.append(s)
                elif q and (q in s.get("id", "").lower() or q in s.get("name", "").lower()):
                    matches.append(s)
        return matches

    def execute_skill(
        self,
        skill_id: str,
        params: Optional[Dict[str, Any]] = None,
        confirmed: bool = False,
        auth_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        F26: Enforces 3-Tier Safety Permissions Model.
        - READ_ONLY: Executes immediately without confirmation.
        - CONFIRM_ONCE: Requires inline confirmation popover.
        - DESTRUCTIVE_REQUIRE_AUTH: Requires modal typed auth confirmation.
        """
        skill = self.get_skill(skill_id)
        if not skill:
            return {"status": "ERROR", "message": f"Skill '{skill_id}' non trovata"}

        tier = skill.get("safety_tier", "CONFIRM_ONCE")
        params = params or {}

        if tier == "READ_ONLY":
            # Tier 1: direct silent execution
            return {
                "status": "EXECUTED",
                "prompt": None,
                "skill_id": skill_id,
                "output": f"Esecuzione completata per {skill['name']}",
            }
        elif tier == "CONFIRM_ONCE":
            # Tier 2: inline popover confirmation required
            if confirmed:
                return {
                    "status": "EXECUTED",
                    "prompt": None,
                    "skill_id": skill_id,
                    "output": f"Azione confermata ed eseguita per {skill['name']}",
                }
            return {
                "status": "WAITING_CONFIRMATION",
                "prompt": "INLINE_POPOVER",
                "skill_id": skill_id,
                "message": f"Confermi l'esecuzione di '{skill['name']}'? [Invio / Esc]",
            }
        elif tier == "DESTRUCTIVE_REQUIRE_AUTH":
            # Tier 3: modal requiring typing confirmation string
            req_text = skill.get("validation_rules", {}).get("confirmation_required_text", "CONFERMA")
            if confirmed and (auth_token == req_text or auth_token is True):
                return {
                    "status": "EXECUTED",
                    "prompt": None,
                    "skill_id": skill_id,
                    "output": f"Azione distruttiva autorizzata ed eseguita: {skill['name']}",
                }
            return {
                "status": "WAITING_CONFIRMATION",
                "prompt": "MODAL_AUTH",
                "skill_id": skill_id,
                "required_confirmation_text": req_text,
                "message": f"ATTENZIONE: Azione distruttiva '{skill['name']}'. Digita '{req_text}' per autorizzare.",
            }

        raise ValueError(f"Unknown tier {tier}")

    # =========================================================================
    # F28: Autonomous Skill Synthesis (>= 3 Repetitions)
    # =========================================================================

    def record_action(self, signature: str) -> None:
        """Records an invoked command/action pattern for frequency analysis."""
        sig = signature.strip().lower()
        if not sig:
            return
        now = time.time()
        with self._lock:
            self._action_logs.append({"sig": sig, "ts": now})
            # Prune logs older than 7 days
            cutoff = now - (7 * 86400)
            self._action_logs = [entry for entry in self._action_logs if entry["ts"] >= cutoff]

    def check_synthesis_trigger(self, signature: str, threshold: int = 3) -> bool:
        """Checks if an action signature was repeated >= threshold times in sliding 7-day window."""
        sig = signature.strip().lower()
        with self._lock:
            count = sum(1 for entry in self._action_logs if entry["sig"] == sig)
            return count >= threshold

    def get_synthesis_candidates(self, threshold: int = 3) -> List[Dict[str, Any]]:
        """Returns all patterns repeated >= threshold times eligible for 1-click synthesis."""
        with self._lock:
            counts: Dict[str, int] = {}
            for entry in self._action_logs:
                sig = entry["sig"]
                counts[sig] = counts.get(sig, 0) + 1

            candidates = []
            for sig, cnt in counts.items():
                if cnt >= threshold:
                    # Check if already registered as a skill
                    existing = self.find_matching_skills(sig)
                    if not existing:
                        slug = re.sub(r"[^a-z0-9_]+", "_", sig).strip("_") or "synthesized_skill"
                        candidates.append({
                            "signature": sig,
                            "count": cnt,
                            "suggested_slug": slug,
                            "suggested_name": sig.replace("_", " ").title(),
                        })
            return candidates

    def synthesize_skill(
        self,
        signature: str,
        name: Optional[str] = None,
        safety_tier: str = "CONFIRM_ONCE",
        command_template: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        F28 1-Click Synthesis:
        Serializes a new declarative skill YAML file into bridge/data/skills/{slug}.yaml,
        marks safety tier as CONFIRM_ONCE, and registers it immediately.
        """
        clean_sig = signature.strip().lower()
        slug = re.sub(r"[^a-z0-9_]+", "_", clean_sig).strip("_") or f"skill_{int(time.time())}"
        skill_name = name or clean_sig.replace("_", " ").title()

        skill_doc = {
            "schema_version": "1.0",
            "id": slug,
            "name": skill_name,
            "description": f"Skill creata autonomamente da azioni ripetute ({clean_sig})",
            "author": "autonomous_synthesis",
            "safety_tier": safety_tier if safety_tier in SAFETY_TIERS else "CONFIRM_ONCE",
            "trigger_patterns": [f"/{slug}", clean_sig],
            "parameters": [],
            "execution_contract": {
                "type": "SYNTHESIZED_MACRO",
                "command_template": command_template or f"# Macro per {clean_sig}",
            },
            "validation_rules": {
                "disallowed_patterns": ["rmdir", "del /f", "format ", "DROP TABLE", "reg delete"],
            },
            "created_at": time.time(),
        }

        # Write YAML or JSON to disk
        out_path = os.path.join(self.skills_dir, f"{slug}.yaml")
        with self._lock:
            if HAS_YAML:
                with open(out_path, "w", encoding="utf-8") as f:
                    yaml.dump(skill_doc, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            else:
                out_path = os.path.join(self.skills_dir, f"{slug}.json")
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(skill_doc, f, ensure_ascii=False, indent=2)

            self._skills[slug] = skill_doc

        logger.info("Synthesized skill [%s] saved to %s", slug, out_path)
        return skill_doc
