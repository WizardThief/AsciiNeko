"""
neko_bot.ui.quick_bar - Floating Quick-Input Launcher Window & CLI Trigger (Feature F22).

Built exclusively with standard library Tkinter (zero external dependencies).
Features:
- Sleek dark theme (#1e1e2e, #313244, #cdd6f4, #89b4fa)
- Centered floating window positioned at upper-third screen
- Keybindings: Enter to submit, Escape to dismiss, FocusOut auto-dismiss
- Focus auto-grab on launch
- Two-tier submission: IPC HTTP POST /command {"action": "task_add", "text": "..."}
  with graceful in-process TaskStore fallback if daemon is offline
- Headless CI execution support without X11 or display requirements
"""

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from typing import Any, Callable, Dict, Optional
import urllib.error
import urllib.request

from neko_bot.core.config import Config, get_config

logger = logging.getLogger("neko_bot.ui.quick_bar")

# Design Palette (Catppuccin Mocha Inspired)
COLOR_BG = "#1e1e2e"          # Dark base
COLOR_SURFACE = "#313244"     # Input bar background
COLOR_TEXT = "#cdd6f4"        # Crisp text
COLOR_SUBTEXT = "#a6adc8"     # Hint text
COLOR_ACCENT = "#89b4fa"      # Bright blue accent
COLOR_SUCCESS = "#a6e3a1"     # Green feedback
COLOR_BORDER = "#45475a"      # Subtle boundary
COLOR_PLACEHOLDER = "#6c7086" # Muted placeholder

WINDOW_WIDTH = 560
WINDOW_HEIGHT = 64


@dataclass
class QuickBarResult:
    """Result of a quick-bar command submission."""
    success: bool
    mode: str  # "ipc" | "fallback" | "none" | "error"
    message: str
    task: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "mode": self.mode,
            "message": self.message,
            "task": self.task,
        }


def submit_command(
    text: str,
    config: Optional[Config] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    timeout: float = 1.0,
) -> QuickBarResult:
    """
    Submits command text to running daemon via IPC HTTP POST /command {"action": "task_add", "text": ...}.
    Falls back gracefully to in-process TaskStore if daemon is offline or unreachable.
    Never raises unhandled exceptions.
    """
    raw_text = text.strip()
    if not raw_text:
        return QuickBarResult(success=False, mode="none", message="Input empty")

    cfg = config or get_config()
    server_host = host or cfg.host
    server_port = port or cfg.port

    # --- Tier 1: IPC to running background daemon ---
    url = f"http://{server_host}:{server_port}/command"
    payload = json.dumps({"action": "task_add", "text": raw_text}).encode("utf-8")
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("success", True):
                    task_data = body.get("task")
                    msg = body.get("message", f"Task added: {raw_text}")
                    return QuickBarResult(success=True, mode="ipc", message=msg, task=task_data)
    except Exception as e:
        logger.debug("IPC submission to %s failed (%s), using in-process fallback", url, e)

    # --- Tier 2: Graceful In-Process TaskStore Fallback ---
    try:
        from neko_bot.task.parser import parse_task_input
        from neko_bot.task.store import TaskStore

        parsed = parse_task_input(raw_text, config=cfg)
        store = TaskStore(cfg.tasks_file, cfg.tasks_md_file, config=cfg)
        task = store.add_task(
            title=parsed.title,
            project=parsed.project,
            priority=parsed.priority,
            due_date=parsed.due_date,
            tags=parsed.tags,
            metadata={"source": "quick_bar", "raw_prompt": raw_text, "offline_fallback": True},
        )
        return QuickBarResult(
            success=True,
            mode="fallback",
            message=f"Task added (offline): {task.title}",
            task=task.to_dict(),
        )
    except Exception as e:
        logger.error("In-process fallback failed: %s", e)
        return QuickBarResult(success=False, mode="error", message=str(e))


class QuickBar:
    """Floating Dark-Themed Quick Launcher Window built on standard library Tkinter."""

    def __init__(
        self,
        config: Optional[Config] = None,
        headless: bool = False,
        on_submitted: Optional[Callable[[QuickBarResult], None]] = None,
    ) -> None:
        self.config = config or get_config()
        self.headless = headless
        self.on_submitted = on_submitted
        self.root: Optional[tk.Tk] = None
        self.entry: Optional[tk.Entry] = None
        self.prompt_label: Optional[tk.Label] = None
        self.last_result: Optional[QuickBarResult] = None
        self._is_submitting = False
        self._placeholder_active = True
        self.placeholder_text = "Digita un task o comando... (Invio invia, Esc esce)"

        if not self.headless:
            self._init_window()

    def _init_window(self) -> None:
        try:
            self.root = tk.Tk()
        except Exception as e:
            logger.warning("Tkinter failed to initialize display (%s). Falling back to headless.", e)
            self.headless = True
            self.root = None
            return

        try:
            self.root.title("neko quick launcher")
            self.root.configure(bg=COLOR_BG)
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)

            # Center horizontally, position at upper-third
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            pos_x = (screen_w - WINDOW_WIDTH) // 2
            pos_y = max(60, (screen_h - WINDOW_HEIGHT) // 3)
            self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{pos_x}+{pos_y}")

            # Outer border frame
            outer_frame = tk.Frame(
                self.root,
                bg=COLOR_SURFACE,
                highlightbackground=COLOR_BORDER,
                highlightthickness=1,
                padx=12,
                pady=8,
            )
            outer_frame.pack(fill="both", expand=True)

            # Cat prompt icon
            self.prompt_label = tk.Label(
                outer_frame,
                text="🐱 neko",
                bg=COLOR_SURFACE,
                fg=COLOR_ACCENT,
                font=("Segoe UI", 12, "bold"),
                padx=4,
            )
            self.prompt_label.pack(side="left")

            # Input Entry
            self.entry = tk.Entry(
                outer_frame,
                bg=COLOR_SURFACE,
                fg=COLOR_PLACEHOLDER,
                insertbackground=COLOR_ACCENT,
                borderwidth=0,
                relief="flat",
                font=("Segoe UI", 12),
            )
            self.entry.insert(0, self.placeholder_text)
            self.entry.pack(side="left", fill="both", expand=True, padx=8)

            # Event Bindings
            self.entry.bind("<FocusIn>", self._on_focus_in)
            self.entry.bind("<FocusOut>", self._on_focus_out)
            self.entry.bind("<Return>", self._on_enter)
            self.entry.bind("<KP_Enter>", self._on_enter)
            self.root.bind("<Escape>", lambda e: self.dismiss())
            self.root.bind("<FocusOut>", self._on_window_focus_out)

            # Auto-focus
            self.root.lift()
            self.root.focus_force()
            self.entry.focus_set()
        except Exception as e:
            logger.warning("Error constructing QuickBar window (%s). Falling back to headless.", e)
            if self.root:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            self.headless = True
            self.root = None

    def _on_focus_in(self, event: Any = None) -> None:
        if self._placeholder_active and self.entry:
            self.entry.delete(0, tk.END)
            self.entry.configure(fg=COLOR_TEXT)
            self._placeholder_active = False

    def _on_focus_out(self, event: Any = None) -> None:
        if self.entry and not self.entry.get().strip():
            self.entry.insert(0, self.placeholder_text)
            self.entry.configure(fg=COLOR_PLACEHOLDER)
            self._placeholder_active = True

    def _on_window_focus_out(self, event: Any = None) -> None:
        if not self._is_submitting:
            if self.root:
                self.root.after(150, self._check_focus_and_dismiss)

    def _check_focus_and_dismiss(self) -> None:
        if self.root:
            focused = self.root.focus_get()
            if focused is None:
                self.dismiss()

    def _on_enter(self, event: Any = None) -> None:
        if self._is_submitting:
            return
        text = "" if self._placeholder_active or not self.entry else self.entry.get().strip()
        if not text:
            self.dismiss()
            return

        self._is_submitting = True
        if self.prompt_label:
            self.prompt_label.configure(text="🐱 invio...", fg=COLOR_SUBTEXT)
        if self.entry:
            self.entry.configure(state="disabled")

        threading.Thread(target=self._execute_submit, args=(text,), daemon=True).start()

    def _execute_submit(self, text: str) -> None:
        result = submit_command(text, config=self.config)
        self.last_result = result
        if self.on_submitted:
            try:
                self.on_submitted(result)
            except Exception:
                pass

        if self.root and self.prompt_label:
            self.root.after(0, lambda: self._show_feedback_and_close(result))

    def _show_feedback_and_close(self, result: QuickBarResult) -> None:
        if self.prompt_label:
            if result.success:
                self.prompt_label.configure(text="🐱 salvato!", fg=COLOR_SUCCESS)
            else:
                self.prompt_label.configure(text="🐱 errore", fg="#f38ba8")
        if self.root:
            self.root.after(200, self.dismiss)

    def submit_headless(self, text: str) -> QuickBarResult:
        """Submits command directly in headless testing environments."""
        res = submit_command(text, config=self.config)
        self.last_result = res
        if self.on_submitted:
            self.on_submitted(res)
        return res

    def dismiss(self) -> None:
        """Closes and destroys the quick launcher window."""
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
            self.root = None

    def run(self) -> int:
        """Starts the Tkinter main event loop."""
        if self.root:
            try:
                self.root.mainloop()
                return 0
            except KeyboardInterrupt:
                return 0
        return 0


def main() -> int:
    """CLI entry point for python -m neko_bot.ui.quick_bar."""
    app = QuickBar()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
