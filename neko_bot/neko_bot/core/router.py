"""
neko_bot.core.router - Resource-Aware Ollama Router with Instant Deterministic Fallback.

Dynamically adapts to active creative processes and system load:
- When heavy creative apps (Unreal, Blender, Photoshop, 3ds Max, etc.) are active:
  enforces deterministic zero-VRAM parsing (or keep_alive: 0 with lightweight model)
  so creative and viewport performance is never degraded.
- Enforces strict 1.5s network timeout and instant fallback ensuring < 2s SLA
  even if Ollama is offline, unreachable, or missing model weights.
"""

from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import urllib.error
import urllib.request

from neko_bot.core.resource_monitor import ResourceMonitor

logger = logging.getLogger("neko_bot.router")


@dataclass
class RouteResult:
    """Standardized response container for routed requests."""
    success: bool
    source: str          # "ollama" | "deterministic_fallback" | "direct_deterministic"
    resource_mode: str   # "creative_detected" | "low_vram" | "normal"
    model: str           # model name or "deterministic-engine"
    data: Any
    elapsed_ms: float
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source,
            "resource_mode": self.resource_mode,
            "model": self.model,
            "data": self.data,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "error": self.error,
        }


def default_deterministic_fallback(prompt: str) -> Dict[str, Any]:
    """
    Built-in zero-VRAM deterministic parser for Italian and general productivity prompts.
    Extracts title, project hashtag (#tag), priority (!alta, !media, !bassa), and due date hints.
    """
    raw = prompt.strip()

    # Extract project hashtag (e.g. #CyberpunkCity or #art)
    project_match = re.search(r"#([a-zA-Z0-9_\-]+)", raw)
    project = project_match.group(1) if project_match else "default"

    # Extract priority tag (e.g. !alta, !urgente, !media, !bassa)
    priority = "medium"
    prio_match = re.search(r"!([a-zA-Z]+)", raw)
    if prio_match:
        p_val = prio_match.group(1).lower()
        if p_val in ("alta", "urgente", "high", "urgent"):
            priority = "high"
        elif p_val in ("bassa", "low"):
            priority = "low"
        elif p_val in ("media", "medium", "normale"):
            priority = "medium"

    # Extract deadline / date hints
    due_date = None
    if re.search(r"\b(oggi|today)\b", raw, re.IGNORECASE):
        due_date = "today"
    elif re.search(r"\b(domani|tomorrow)\b", raw, re.IGNORECASE):
        due_date = "tomorrow"
    else:
        date_match = re.search(r"\bentro\s+([0-9\-\/]+(\s+[0-9\:]+)?)", raw, re.IGNORECASE)
        if date_match:
            due_date = date_match.group(1).strip()

    # Clean title by removing project and priority markers
    title = raw
    if project_match:
        title = title.replace(project_match.group(0), "")
    if prio_match:
        title = title.replace(prio_match.group(0), "")

    # Strip command prefix keywords like "task", "aggiungi", "crea", "do"
    title = re.sub(
        r"^(task|aggiungi(\s+task)?|crea(\s+task)?|do|promemoria)\s*[:\-]?\s*",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()

    if not title:
        title = raw

    return {
        "title": title,
        "project": project,
        "priority": priority,
        "due_date": due_date,
        "raw_prompt": raw,
        "parsed_by": "deterministic_engine",
    }


class OllamaRouter:
    """Resource-aware Ollama router with deterministic fallback and < 2s SLA."""

    def __init__(
        self,
        resource_monitor: Optional[ResourceMonitor] = None,
        ollama_base_url: str = "http://127.0.0.1:11434",
        default_model: str = "qwen3.8:latest",
        lightweight_model: str = "clef:latest",
        ollama_timeout: float = 1.5,
        force_deterministic: bool = False,
    ) -> None:
        self.resource_monitor = resource_monitor if resource_monitor is not None else ResourceMonitor()
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.default_model = default_model
        self.lightweight_model = lightweight_model
        self.ollama_timeout = float(ollama_timeout)
        self.force_deterministic = bool(force_deterministic)

    def is_ollama_online(self) -> bool:
        """Probes Ollama service availability via GET /api/tags."""
        try:
            req = urllib.request.Request(f"{self.ollama_base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_available_models(self) -> List[str]:
        """Lists models currently present in local Ollama storage."""
        try:
            req = urllib.request.Request(f"{self.ollama_base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m.get("name") for m in data.get("models", []) if m.get("name")]
        except Exception:
            return []

    def route_request(
        self,
        prompt: str,
        intent: str = "general",
        fallback_func: Optional[Callable] = None,
        force_llm: bool = False,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Routes the user request dynamically based on creative app status and system load.

        Under creative load (Unreal, Blender, Photoshop, 3ds Max, etc.):
        - Routes to instant deterministic zero-VRAM execution unless force_llm=True.
        - If forced LLM, unloads model immediately after inference (keep_alive: 0).

        Guarantees response within < 2.0s even when Ollama is offline or unpulled.
        """
        start_time = time.perf_counter()
        fb = fallback_func if fallback_func is not None else default_deterministic_fallback
        mode = self.resource_monitor.get_resource_mode()

        # Decision 1: Direct deterministic bypass when creative software is active or forced
        if self.force_deterministic or (mode == "creative_detected" and not force_llm):
            fb_data, fb_err = self._execute_fallback(fb, prompt)
            elapsed = (time.perf_counter() - start_time) * 1000
            return RouteResult(
                success=fb_err is None,
                source="direct_deterministic",
                resource_mode=mode,
                model="deterministic-engine",
                data=fb_data,
                elapsed_ms=elapsed,
                error=fb_err,
            ).to_dict()

        # Decision 2: Select Model & Parameters based on Resource Mode
        if mode in ("creative_detected", "low_vram"):
            target_model = self.lightweight_model
            keep_alive = 0  # Unload immediately after inference to reclaim VRAM
            timeout = min(self.ollama_timeout, 1.5)
            num_predict = 128
        else:
            target_model = self.default_model
            keep_alive = 300  # Retain model in memory during normal quiet periods
            timeout = min(self.ollama_timeout, 1.5)
            num_predict = 256

        # Decision 3: Attempt Ollama generation with strict timeout
        ollama_success = False
        ollama_data = None
        error_msg = None

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload: Dict[str, Any] = {
                "model": target_model,
                "messages": messages,
                "stream": False,
                "keep_alive": keep_alive,
                "options": {
                    "num_predict": num_predict,
                },
            }
            if intent in ("task_parse", "json"):
                payload["format"] = "json"

            body_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.ollama_base_url}/api/chat",
                data=body_bytes,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    resp_body = json.loads(resp.read().decode("utf-8"))
                    content = resp_body.get("message", {}).get("content", "").strip()
                    if intent in ("task_parse", "json"):
                        try:
                            ollama_data = json.loads(content)
                        except Exception:
                            ollama_data = {"raw_content": content}
                    else:
                        ollama_data = content
                    ollama_success = True
                else:
                    error_msg = f"Ollama HTTP {resp.status}"
        except Exception as e:
            error_msg = str(e)
            logger.debug("Ollama request failed or timed out: %s", e)

        # Decision 4: Return Ollama response if successful
        if ollama_success:
            elapsed = (time.perf_counter() - start_time) * 1000
            return RouteResult(
                success=True,
                source="ollama",
                resource_mode=mode,
                model=target_model,
                data=ollama_data,
                elapsed_ms=elapsed,
            ).to_dict()

        # Decision 5: Instant deterministic fallback (< 5ms)
        fb_data, fb_err = self._execute_fallback(fb, prompt)
        elapsed = (time.perf_counter() - start_time) * 1000
        return RouteResult(
            success=fb_err is None,
            source="deterministic_fallback",
            resource_mode=mode,
            model="deterministic-engine",
            data=fb_data,
            elapsed_ms=elapsed,
            error=error_msg or fb_err,
        ).to_dict()

    def route_prompt(self, prompt: str) -> Dict[str, Any]:
        """Convenience method for one-line prompt routing."""
        return self.route_request(prompt=prompt, intent="general")

    @staticmethod
    def _execute_fallback(fallback_func: Callable, prompt: str) -> Tuple[Any, Optional[str]]:
        """Safely executes fallback callable with prompt or zero arguments."""
        try:
            try:
                return fallback_func(prompt), None
            except TypeError:
                return fallback_func(), None
        except Exception as e:
            return {"raw_prompt": prompt}, f"Fallback execution error: {e}"


_default_router: Optional[OllamaRouter] = None


def get_default_router(config: Optional[Any] = None) -> OllamaRouter:
    """Returns OllamaRouter configured from config or default singleton."""
    global _default_router
    if config is not None:
        return OllamaRouter(
            ollama_base_url=getattr(config, "ollama_url", "http://127.0.0.1:11434"),
            default_model=getattr(config, "ollama_model", "qwen3.8:latest"),
            lightweight_model=getattr(config, "ollama_lightweight_model", "clef:latest"),
            ollama_timeout=getattr(config, "ollama_timeout", 1.5),
        )
    if _default_router is None:
        _default_router = OllamaRouter()
    return _default_router
