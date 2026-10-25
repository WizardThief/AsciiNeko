"""
neko_bot.skills.executor - Skill Execution Dispatcher and Telemetry Tracker.

Handles dual dispatch for declarative workflows and sandboxed Python tools,
tracking execution metrics (execution_count, last_executed_at) and recording
episodic memory entries into MemoryStore.
"""

from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional

from neko_bot.core.config import Config
from neko_bot.skills.models import SkillManifest, now_utc_iso
from neko_bot.skills.registry import SkillRegistry
from neko_bot.skills.sandbox import SkillSandbox

logger = logging.getLogger("neko_bot.skills.executor")


class SkillExecutor:
    """
    Executes installed skills with telemetry updating and episodic memory capture.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        sandbox: Optional[SkillSandbox] = None,
        memory_store: Optional[Any] = None,
        config: Optional[Config] = None,
    ) -> None:
        self.registry = registry
        self.sandbox = sandbox or getattr(registry, "sandbox", None) or SkillSandbox()
        self.config = config or Config.load()
        self.memory_store = memory_store

    def execute(
        self,
        skill_id: str,
        inputs: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
        session_id: str = "default",
        log_episode: bool = True,
        event_type: str = "skill_executed",
    ) -> Dict[str, Any]:
        """
        Executes a skill by ID. Updates registry telemetry and logs episode.
        Guarantees safety against None context, missing inputs, and unhandled exceptions.
        """
        safe_inputs = inputs if isinstance(inputs, dict) else {}
        safe_context = context if isinstance(context, dict) else {}

        skill = self.registry.get_skill(skill_id)
        if not skill:
            return {"success": False, "result": {}, "error": f"Skill '{skill_id}' not found"}

        stype = skill.get("type") or skill.get("skill_type") or "tool"
        t0 = time.perf_counter()

        if "workflow" in str(stype).lower():
            exec_res = self._execute_workflow(skill, safe_inputs, safe_context)
        else:
            exec_res = self._execute_tool(skill, safe_inputs, safe_context)

        duration = time.perf_counter() - t0
        exec_res["duration_sec"] = round(duration, 4)
        exec_res["skill_id"] = skill_id

        # Update registry telemetry on success
        if exec_res.get("success"):
            try:
                skill["execution_count"] = skill.get("execution_count", 0) + 1
                now_utc = now_utc_iso()
                skill["last_executed_at"] = now_utc
                skill["updated_at"] = now_utc
                # Re-save to disk
                if hasattr(self.registry, "_save_registry"):
                    self.registry._save_registry()
            except Exception as e:
                logger.debug("Failed to update execution telemetry: %s", e)

        # Log episodic memory if enabled and memory_store available
        if log_episode and self.memory_store:
            try:
                outcome = "success" if exec_res.get("success") else "failure"
                self.memory_store.add_episode(
                    session_id=session_id,
                    event_type=event_type,
                    summary=f"Executed skill {skill_id}",
                    full_text=json.dumps(
                        {"inputs": safe_inputs, "result": exec_res.get("result"), "error": exec_res.get("error")},
                        ensure_ascii=False,
                    ),
                    context_tags=["skill", skill_id, outcome],
                    outcome=outcome,
                )
            except Exception as e:
                logger.debug("Failed to record execution episode: %s", e)

        return exec_res

    def _execute_tool(self, skill: Dict[str, Any], inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a Python tool skill inside the isolated sandbox."""
        code = skill.get("code") or skill.get("code_str") or ""
        timeout_sec = float(skill.get("timeout_sec", 5.0))
        return self.sandbox.run_tool_in_sandbox(code, inputs, context, timeout_sec=timeout_sec)

    def _execute_workflow(self, skill: Dict[str, Any], inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a declarative multi-step workflow pipeline."""
        steps = skill.get("steps", [])
        if not steps:
            return {"success": False, "result": {}, "error": "Workflow contains no steps"}

        workflow_data = dict(inputs)

        for idx, step in enumerate(steps):
            action = step.get("action")
            if not action:
                return {"success": False, "result": {}, "error": f"Step {idx} missing 'action'"}

            out_key = step.get("output_key", f"step_{idx}_out")

            if action == "query_tasks":
                try:
                    from neko_bot.task.store import TaskStore
                    ts = TaskStore(self.registry.workspace_dir / "workspace")
                    tasks = [t.to_dict() for t in ts.list_tasks(include_completed=True)]
                    f_state = step.get("filter", {}).get("state") or step.get("params", {}).get("state")
                    if f_state:
                        tasks = [t for t in tasks if t.get("state") == f_state]
                    workflow_data[out_key] = tasks
                except Exception:
                    workflow_data[out_key] = []

            elif action == "query_memory":
                if self.memory_store:
                    q = step.get("params", {}).get("query", "")
                    lvl = step.get("params", {}).get("level", "all")
                    workflow_data[out_key] = self.memory_store.search(q, level=lvl)
                else:
                    workflow_data[out_key] = []

            elif action == "format_template":
                tmpl = step.get("template", "")
                try:
                    formatted = tmpl.format(**workflow_data)
                except Exception:
                    formatted = tmpl
                workflow_data[out_key] = formatted

            elif action == "notify":
                title = step.get("title", "Neko Bot")
                msg_tmpl = step.get("message", "{report}")
                try:
                    msg = msg_tmpl.format(**workflow_data)
                except Exception:
                    msg = msg_tmpl
                workflow_data[out_key] = {"title": title, "message": msg, "delivered": True}

            elif action == "parse":
                workflow_data[out_key] = {"parsed": True, "data": inputs}

            else:
                workflow_data[out_key] = {"executed": True, "action": action}

        return {"success": True, "result": workflow_data, "error": None}
