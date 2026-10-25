"""
neko_bot.skills.synthesizer - Autonomous Skill Synthesis Engine.

Synthesizes declarative workflows and executable Python tool skills from
natural language prompts, ensuring zero external paid API dependencies through
deterministic pattern templates, while supporting Ollama router expansion
and enforcing strict pre-registration AST and sandbox verification.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from neko_bot.core.config import Config
from neko_bot.skills.models import SkillManifest, SkillStatus, SkillType, now_utc_iso
from neko_bot.skills.registry import SkillRegistry
from neko_bot.skills.sandbox import SkillSandbox

logger = logging.getLogger("neko_bot.skills.synthesizer")


class SkillSynthesizer:
    """
    Autonomous synthesizer generating verified skill manifests and self-tests.
    """

    def __init__(
        self,
        sandbox: Optional[SkillSandbox] = None,
        registry: Optional[SkillRegistry] = None,
        config: Optional[Config] = None,
    ) -> None:
        self.sandbox = sandbox or SkillSandbox()
        self.config = config or Config.load()
        self.registry = registry or SkillRegistry(self.config.workspace_dir, self.sandbox)

    def synthesize_skill(self, prompt: str) -> Optional[Dict[str, Any]]:
        """
        Attempts to generate a candidate skill manifest from a prompt.
        Uses deterministic template synthesis with regex pattern matching.
        """
        p_clean = prompt.strip()
        p_low = p_clean.lower()

        # 1. Cryptographic Hasher Pattern (sha256, md5, hash, digest)
        if any(k in p_low for k in ("sha256", "hash", "digest", "crittograf")):
            code = (
                "def run(inputs: dict, context: dict) -> dict:\n"
                "    import hashlib\n"
                "    text = str(inputs.get('text', ''))\n"
                "    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()\n"
                "    return {'algorithm': 'sha256', 'digest': digest, 'length': len(text)}\n"
            )
            return {
                "id": "tool-sha256-hasher",
                "name": "SHA256 Payload Hasher",
                "version": "1.0.0",
                "type": "tool",
                "skill_type": "python_tool",
                "description": "Calculates SHA256 cryptographic digest of arbitrary string",
                "trigger_patterns": ["sha256", "calcola sha256", "hash text", "digest"],
                "input_schema": {"text": "string"},
                "output_schema": {"algorithm": "string", "digest": "string", "length": "integer"},
                "code": code,
                "self_tests": [
                    {
                        "name": "test_standard_hash",
                        "inputs": {"text": "hello world"},
                        "expected_output": {
                            "algorithm": "sha256",
                            "digest": hashlib.sha256("hello world".encode("utf-8")).hexdigest(),
                            "length": 11,
                        },
                    }
                ],
            }

        # 2. Text Statistics Pattern (word count, conta parole, text stats)
        if any(k in p_low for k in ("word count", "conta parole", "text stats", "analizza testo")):
            code = (
                "def run(inputs: dict, context: dict) -> dict:\n"
                "    text = str(inputs.get('text', ''))\n"
                "    words = text.split()\n"
                "    return {'word_count': len(words), 'uppercase': text.upper()}\n"
            )
            return {
                "id": "tool-text-stats",
                "name": "text_stats_formatter",
                "version": "1.0.0",
                "type": "tool",
                "skill_type": "python_tool",
                "description": "Conta parole e converte in maiuscolo",
                "trigger_patterns": ["text stats", "analizza testo", "conta parole"],
                "code": code,
                "self_tests": [
                    {
                        "name": "test_basic",
                        "inputs": {"text": "ciao mondo"},
                        "expected_output": {"word_count": 2, "uppercase": "CIAO MONDO"},
                    }
                ],
            }

        # 3. Math Squarer / Calculator Pattern
        if any(k in p_low for k in ("calcola quadrato", "square", "eleva a potenza", "sqrt", "radice")):
            code = (
                "def run(inputs: dict, context: dict) -> dict:\n"
                "    import math\n"
                "    val = float(inputs.get('val', 0))\n"
                "    return {'squared': val * val, 'root': math.sqrt(abs(val))}\n"
            )
            return {
                "id": "tool-math-squarer",
                "name": "math_squarer",
                "version": "1.0.0",
                "type": "tool",
                "code": code,
                "trigger_patterns": ["quadrato", "square", "sqrt"],
                "self_tests": [
                    {
                        "name": "test_math",
                        "inputs": {"val": 4},
                        "expected_output": {"squared": 16.0, "root": 2.0},
                    }
                ],
            }

        # 4. Declarative Standup Workflow Pattern
        if any(k in p_low for k in ("standup", "daily report", "report ieri", "sommario")):
            return {
                "id": "workflow-daily-standup",
                "name": "daily_standup_pipeline",
                "version": "1.0.0",
                "type": "workflow",
                "description": "Raccoglie i task completati e prepara il report standup",
                "trigger_patterns": ["standup", "prepara standup", "daily report"],
                "steps": [
                    {"action": "query_tasks", "params": {"state": "done", "since_days": 1}, "output_key": "completed_tasks"},
                    {"action": "format_template", "template": "Report Standup:\n{completed_tasks}", "output_key": "report"},
                    {"action": "notify", "title": "Standup Neko", "message": "{report}"},
                ],
            }

        return None

    def synthesize_and_register(self, prompt: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Synthesizes a candidate skill from prompt, validates AST & self-tests in sandbox,
        and persists into registry upon passing 100% of checks.
        Returns: (success, skill_manifest_or_none, message)
        """
        candidate = self.synthesize_skill(prompt)
        if not candidate:
            return False, None, f"No synthesis template matched prompt: '{prompt}'"

        # Verification Gate
        verified, err_msg = self.sandbox.verify_skill(candidate)
        if not verified:
            logger.warning("Synthesis verification rejected candidate '%s': %s", candidate.get("id"), err_msg)
            return False, candidate, f"Verification failed: {err_msg}"

        # Register in catalog
        ok, reg_msg = self.registry.register_skill(candidate)
        if not ok:
            return False, candidate, f"Registration failed: {reg_msg}"

        logger.info("Successfully synthesized and registered skill: %s", candidate.get("id"))
        return True, self.registry.get_skill(candidate["id"]), "Skill synthesized and verified successfully"
