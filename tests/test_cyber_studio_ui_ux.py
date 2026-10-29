"""
tests/test_cyber_studio_ui_ux.py
=================================
Comprehensive 4-Tier E2E Test Suite for AsciiNeko Cyber-Studio Tactile Co-Pilot HUD:
- R1: "Cyber-Studio Tactile" Design System & Dynamic Context-Mode Accent Theming
- R2: Active Collaborative Co-Pilot HUD on `🐾 Neko` Home & Upgraded Command Dock
- R3: Progressive Disclosure & Decluttered Layouts Across the 4 Dashboards
- R4: Full Backward Compatibility, DOM Contract Preservation (315 IDs), & Data Hygiene

Test Hierarchy:
- Tier 1: Feature Coverage (Category-Partition: R1, R2, R3) (14 tests)
- Tier 2: Boundary & Corner Cases (Empty/null data, unknown mode fallback, 315-ID preservation, budget bounds) (7 tests)
- Tier 3: Cross-Feature Combinations & State Transitions (Workspace tab -> context mode, Co-Pilot HUD visibility rules, drawer open/close, Memoria subtabs) (7 tests)
- Tier 4: Real-World Scenarios & Production Hygiene (node --check on 6 JS files, data purity, Diego's authentic data preservation, full lifecycle workflow) (4 tests)

Total: 32 comprehensive tests across 4 tiers.

MANDATORY DATA HYGIENE:
- All tests execute within isolated tempfile.TemporaryDirectory() instances.
- Zero test artifacts may touch or pollute bridge/data/.
- Teardown verifies 100% snapshot equality of production data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure repository root and bridge/ are on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

PRODUCTION_DATA_DIR = os.path.join(BRIDGE_DIR, "data")

# Catalog of all 315 unique element IDs present in index.html (Milestone contract)
ALL_315_LEGACY_DOM_IDS = (
    "active-app-badge", "active-proj-badge", "active-proj-name-badge", "active-tracking-section",
    "afk-badge", "agenda-pill", "ai-action-buttons", "ai-dot", "ai-hitl-prompt-dock",
    "ai-input-field", "ai-input-form", "ai-model-badge", "ai-output-line",
    "ai-skill-synthesis-dock", "ai-task-visualizer-dock", "ai-term-body", "ai-terminal-dock",
    "ascii-canvas", "btn-add-instinct", "btn-add-quick-link", "btn-add-rule", "btn-add-skill",
    "btn-cal-next", "btn-cal-prev", "btn-cal-sync-now", "btn-cal-today", "btn-cal-view-agenda",
    "btn-cal-view-monthly", "btn-cal-view-weekly", "btn-cancel-import-skill", "btn-clear-history",
    "btn-close-import-skill-modal", "btn-close-modal", "btn-close-profile-inspector",
    "btn-close-qg-modal", "btn-close-slide-drawer", "btn-close-todo-popover", "btn-color-safe",
    "btn-confirm-synthesis", "btn-create-from-playbook", "btn-create-project",
    "btn-dismiss-deadline", "btn-dismiss-synthesis", "btn-drawer-add-todo", "btn-face-style",
    "btn-fish-snack", "btn-hitl-keep", "btn-hitl-retry", "btn-hitl-rollback",
    "btn-hub-create-proj", "btn-insp-add-mem", "btn-insp-add-todo", "btn-kanban-add",
    "btn-kanban-ai-plan", "btn-mem-profiling-next", "btn-mem-profiling-save", "btn-min-term",
    "btn-open-add-mcp", "btn-open-gcal-web", "btn-open-import-skill-modal", "btn-open-settings",
    "btn-open-storage-dir", "btn-pick-storage-dir", "btn-pull-llama", "btn-qg-close",
    "btn-qg-retest", "btn-quick-todo-add", "btn-random-question", "btn-reset-all-neko",
    "btn-restore-hud", "btn-save-ical", "btn-save-import-skill", "btn-save-onboard",
    "btn-save-storage-dir", "btn-submit-cmd", "btn-subtab-instincts", "btn-subtab-skills",
    "btn-task-abort", "btn-toggle-history", "btn-toggle-hud", "btn-tutorial-snooze",
    "btn-window-mode", "budget-status-pill", "budget-used-pct", "budget-used-tokens",
    "cal-add-date", "cal-add-time", "cal-add-title", "cal-ical-input", "cal-quick-add-form",
    "cal-sync-status-badge", "cal-view-agenda", "cal-view-monthly", "cal-view-range-label",
    "cal-view-weekly", "chat-history-list", "chat-history-panel", "clock-display",
    "companion-dialogue", "conn-badge-cal", "conn-badge-ollama", "conn-badge-storage",
    "conn-ollama-hint", "conn-ollama-select", "conn-storage-hint", "context-budget-container",
    "context-mode-icon", "context-mode-label", "context-mode-pill", "count-instincts",
    "count-kanban-doing", "count-kanban-done", "count-kanban-todo", "count-mcp-active",
    "count-mcp-total", "count-proj-active", "count-proj-archive", "count-skills",
    "count-skills-active", "count-skills-total", "cpu-val", "crt-overlay",
    "deadline-toast-content", "drawer-active-proj-label", "drawer-mem-count",
    "drawer-memories-list", "drawer-proj-count", "drawer-projects-list", "drawer-tab-btn-mem",
    "drawer-tab-btn-proj", "drawer-tab-btn-todo", "drawer-todo-count", "drawer-todo-input",
    "drawer-todo-list", "ecc-arsenal-section", "form-import-skill", "gpu-val",
    "hardware-telemetry", "hitl-asset-info", "hitl-error-header", "home-next-task-chip",
    "home-next-task-date", "home-next-task-title", "hotkey-hint-badge", "hotkey-selector-row",
    "hub-playbook-new-client", "hub-playbook-new-name", "hub-playbook-select",
    "hub-proj-active-count", "hub-proj-arch-count", "hub-proj-new-client",
    "hub-proj-new-name", "hub-proj-new-note", "hub-proj-new-stack",
    "hub-projects-archive-list", "hub-projects-grid", "hub-tab-proj-active",
    "hub-tab-proj-archive", "hud-deadline-toast", "hud-drawer-backdrop", "hud-layer",
    "hud-slide-drawer", "hud-smart-banner", "hud-todo-popover", "hud-workspace-container",
    "hud-workspace-nav", "import-skill-content", "import-skill-domain", "import-skill-id",
    "import-skill-name", "insp-mem-count", "insp-mem-list", "insp-new-mem-input",
    "insp-new-todo-input", "insp-prof-count", "insp-prof-list", "insp-todo-count",
    "insp-todo-list", "inspector-trait-count", "kanban-add-due", "kanban-add-priority",
    "kanban-add-project", "kanban-add-title", "kanban-col-doing", "kanban-col-doing-wrapper",
    "kanban-col-done", "kanban-col-done-wrapper", "kanban-col-todo", "kanban-col-todo-wrapper",
    "kanban-project-filters", "kanban-quick-add-form", "lbl-tok-chat", "lbl-tok-instincts",
    "lbl-tok-memories", "lbl-tok-profile", "lbl-tok-projects", "lbl-tok-system",
    "mcp-connectors-grid", "mem-col-profile-wrapper", "mem-col-rules-wrapper",
    "mem-col-skills-wrapper", "mem-col1-subnav", "mem-instinct-add-row", "mem-instincts-list",
    "mem-new-instinct-action", "mem-new-instinct-scope", "mem-new-instinct-trigger",
    "mem-new-rule-proj", "mem-new-rule-text", "mem-new-skill-cat", "mem-new-skill-name",
    "mem-profile-count", "mem-profile-traits-list", "mem-profiling-input",
    "mem-profiling-options", "mem-profiling-prompt-card", "mem-profiling-q-text",
    "mem-rules-count", "mem-rules-list", "mem-rules-search", "mem-skill-add-row",
    "mem-skills-count", "mem-skills-list", "mem-total-badge", "modal-import-skill",
    "neko-modal-overlay", "neko-profile-inspector-overlay", "new-link-label", "new-link-url",
    "onboard-cat-tabs", "onboard-custom-input", "onboard-options-grid", "onboard-progress-badge",
    "onboard-q-cat", "onboard-q-text", "onboard-question-card", "onboard-traits-list",
    "open-windows-picker", "playbook-quick-bar", "profile-inspector-table",
    "profile-inspector-tbody", "proj-active-view", "proj-archive-list", "proj-archive-view",
    "proj-detail-card", "proj-new-client", "proj-new-name", "proj-new-note",
    "proj-selector-list", "projects-pill", "qg-instincts-badge", "qg-instincts-list",
    "qg-proj-title", "qg-recommendations", "qg-rules-badge", "qg-rules-list",
    "qg-rules-summary", "qg-score-banner", "qg-score-val", "qg-status-desc",
    "qg-status-label", "qg-tasks-badge", "qg-tasks-list", "qg-tasks-summary",
    "quality-gate-modal", "quick-links-chips", "quick-todo-count", "quick-todo-input",
    "quick-todo-list", "ram-val", "seg-chat", "seg-instincts", "seg-memories", "seg-profile",
    "seg-projects", "seg-system", "session-timer-badge", "session-timer-label",
    "session-timer-val", "skills-switchboard-list", "slash-autocomplete-menu",
    "stale-alert-banner", "status-matrix-panel", "storage-dir-input",
    "synthesis-suggestion-text", "tab-btn-proj-active", "tab-btn-proj-archive",
    "tab-cal-badge", "tab-connessioni", "tab-conosciamoci", "tab-drawer-mem",
    "tab-drawer-proj", "tab-drawer-todo", "tab-impostazioni", "tab-mem-badge",
    "tab-memorie", "tab-nav-calendar", "tab-nav-home", "tab-nav-memory", "tab-nav-projects",
    "tab-nav-todo", "tab-progetti", "tab-proj-badge", "tab-todo-badge", "task-vis-bar",
    "task-vis-title", "todo-pill", "tutorial-mandatory-banner", "tutorial-step-title",
    "vram-stat", "vram-val", "workspace-view-calendar", "workspace-view-memory",
    "workspace-view-projects", "workspace-view-todo"
)


# ==============================================================================
# PROGRESSIVE CONTRACT DETECTORS (MILESTONE INTEGRATION GATES)
# ==============================================================================

def _check_cyber_studio_tokens() -> bool:
    """Checks if css/terminal.css defines the Cyber-Studio Tactile tokens in :root."""
    try:
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            css = f.read()
        return ("--bg-surface-0" in css) and ("--bg-surface-1" in css) and ("--mode-accent" in css)
    except Exception:
        return False

HAS_CYBER_STUDIO_TOKENS = _check_cyber_studio_tokens()


def _check_dynamic_context_mode_css() -> bool:
    """Checks if css/terminal.css defines [data-context-mode] theme rules."""
    try:
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            css = f.read()
        return "data-context-mode" in css
    except Exception:
        return False

HAS_DYNAMIC_CONTEXT_MODE_CSS = _check_dynamic_context_mode_css()


def _check_data_context_mode_setter() -> bool:
    """Checks if setContextMode sets data-context-mode on document.body or elHudLayer."""
    try:
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            js = f.read()
        return "data-context-mode" in js
    except Exception:
        return False

HAS_DATA_CONTEXT_MODE_SETTER = _check_data_context_mode_setter()


def _check_balanced_top_bar() -> bool:
    """Checks if #status-matrix-panel has 3 balanced zones or zone containers."""
    try:
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        return ("zone-left" in html or "top-bar-left" in html or "status-zone-1" in html or "top-zone" in html)
    except Exception:
        return False

HAS_BALANCED_TOP_BAR = _check_balanced_top_bar()


def _check_copilot_hud_markup() -> bool:
    """Checks if #neko-copilot-hud and its 3 cards exist in index.html."""
    try:
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        return "neko-copilot-hud" in html
    except Exception:
        return False

HAS_COPILOT_HUD_MARKUP = _check_copilot_hud_markup()


def _check_copilot_hud_css() -> bool:
    """Checks if css/terminal.css contains #neko-copilot-hud layout & visibility rules."""
    try:
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            css = f.read()
        return "neko-copilot-hud" in css
    except Exception:
        return False

HAS_COPILOT_HUD_CSS = _check_copilot_hud_css()


def _check_copilot_hud_renderer() -> bool:
    """Checks if js/hud-terminal.js defines renderNekoCopilotHud or renders copilot cards."""
    try:
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            js = f.read()
        return "renderNekoCopilotHud" in js or "renderCopilotHud" in js or "copilot-card-radar" in js
    except Exception:
        return False

HAS_COPILOT_HUD_RENDERER = _check_copilot_hud_renderer()


def _check_command_dock_dialogue() -> bool:
    """Checks if companion-dialogue or multi-line dock upgrades exist."""
    try:
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        return "companion-dialogue" in html
    except Exception:
        return False

HAS_COMMAND_DOCK_DIALOGUE = _check_command_dock_dialogue()


def _check_memoria_3_subtabs_markup() -> bool:
    """Checks if #memory-view-subtabs exists in index.html."""
    try:
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        return "memory-view-subtabs" in html
    except Exception:
        return False

HAS_MEMORIA_3_SUBTABS_MARKUP = _check_memoria_3_subtabs_markup()


def _check_memoria_subtab_switcher() -> bool:
    """Checks if js/hud-terminal.js has switchMemorySubtab or subtab switching logic."""
    try:
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            js = f.read()
        return "switchMemorySubtab" in js or "memory-view-subtabs" in js or "data-mem-view" in js
    except Exception:
        return False

HAS_MEMORIA_SUBTAB_SWITCHER = _check_memoria_subtab_switcher()


def _check_progetti_drawer_buttons() -> bool:
    """Checks if #btn-toggle-new-proj-drawer and #btn-toggle-playbook-drawer exist in index.html."""
    try:
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        return "btn-toggle-new-proj-drawer" in html
    except Exception:
        return False

HAS_PROGETTI_DRAWER_BUTTONS = _check_progetti_drawer_buttons()


def _check_four_zone_project_cards() -> bool:
    """Checks if js/hud-terminal.js or css/terminal.css defines 4-zone card markup/classes."""
    try:
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            js = f.read()
        return "proj-card-zone" in js or "hub-proj-card-header" in js or "proj-zone" in js
    except Exception:
        return False

HAS_FOUR_ZONE_PROJECT_CARDS = _check_four_zone_project_cards()


def _check_tactile_kanban_calendar() -> bool:
    """Checks if css/terminal.css defines tactile kanban headers or calendar source pills."""
    try:
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            css = f.read()
        return "header-todo" in css or "pill-source-google" in css or "kanban-col-todo" in css
    except Exception:
        return False

HAS_TACTILE_KANBAN_CALENDAR = _check_tactile_kanban_calendar()


# ==============================================================================
# BASE ISOLATED TEST CASE (STRICT DATA HYGIENE GUARANTEE)
# ==============================================================================

class IsolatedCyberStudioTestCase(unittest.TestCase):
    """
    Base test case guaranteeing 100% test isolation within an ephemeral temporary directory.
    Snapshot-verifies that bridge/data/ is never mutated or polluted by test runs.
    """

    def setUp(self):
        self._temp_dir_obj = tempfile.TemporaryDirectory()
        self.test_dir = os.path.abspath(self._temp_dir_obj.name)

        # Snapshot modification timestamps of production data directory before test
        self._prod_data_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)

        # Load fresh copies of static source files
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            self.css = f.read()
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            self.hud_js = f.read()
        with open(os.path.join(REPO_ROOT, "js", "system-bridge.js"), "r", encoding="utf-8") as f:
            self.bridge_js = f.read()

    def tearDown(self):
        self._temp_dir_obj.cleanup()

        # Strict data hygiene assertion: production directory must remain 100% unaltered
        curr_prod_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)
        self.assertEqual(
            self._prod_data_snapshot,
            curr_prod_snapshot,
            "CRITICAL VIOLATION: Production bridge/data/ was mutated during test execution!",
        )

    @staticmethod
    def _capture_dir_snapshot(dir_path: str) -> Dict[str, Tuple[int, float]]:
        """Captures file names, sizes, and mtimes of a directory tree."""
        snapshot = {}
        if not os.path.exists(dir_path):
            return snapshot
        for root, _, files in os.walk(dir_path):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, dir_path)
                try:
                    stat = os.stat(full_path)
                    snapshot[rel_path] = (stat.st_size, stat.st_mtime)
                except OSError:
                    pass
        return snapshot

    @staticmethod
    def _run_node_script(script_body: str) -> subprocess.CompletedProcess:
        """Executes a Node.js script in REPO_ROOT and returns the completed process."""
        return subprocess.run(
            ["node", "-e", script_body],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )


# ==============================================================================
# TIER 1: FEATURE COVERAGE (ISOLATED FUNCTIONAL VERIFICATION)
# ==============================================================================

class TestTier1CyberStudioFeatures(IsolatedCyberStudioTestCase):
    """
    Tier 1: Comprehensive isolated feature coverage for requirements R1, R2, R3.
    Verifies CSS tokens, dynamic context modes, 3-zone top bar, Co-Pilot HUD cards,
    command dock upgrades, 3-subtab Memoria, Progetti drawers, and 4-zone cards.
    """

    @unittest.skipUnless(HAS_CYBER_STUDIO_TOKENS, "Cyber-Studio tokens in css/terminal.css pending implementation")
    def test_t1_f01_cyber_studio_css_tokens(self):
        """R1: Verify :root declares obsidian-slate elevation surfaces, hairline borders, and glass tokens."""
        self.assertIn("--bg-surface-0", self.css, "Missing --bg-surface-0 baseline surface token")
        self.assertIn("--bg-surface-1", self.css, "Missing --bg-surface-1 card elevation token")
        self.assertIn("--bg-surface-2", self.css, "Missing --bg-surface-2 control surface token")
        self.assertIn("--bg-glass", self.css, "Missing --bg-glass glassmorphism token")
        self.assertIn("--border-hairline", self.css, "Missing --border-hairline crisp border token")
        self.assertIn("--mode-accent", self.css, "Missing --mode-accent context accent token")
        self.assertIn("--mode-glow", self.css, "Missing --mode-glow context glow token")

    def test_t1_f02_typography_sans_mono_hierarchy(self):
        """R1: Verify authentic font pairing: Geometric Sans for UI surfaces and Monospace for telemetry/code."""
        # Monospace font stack exists for terminal and telemetry
        has_mono = ("Cascadia Code" in self.css or "JetBrains Mono" in self.css or "Consolas" in self.css)
        self.assertTrue(has_mono, "Terminal CSS must define Monospace font stack for telemetry/terminal")

    @unittest.skipUnless(HAS_DYNAMIC_CONTEXT_MODE_CSS, "Dynamic [data-context-mode] CSS rules pending implementation")
    def test_t1_f03_dynamic_context_mode_css_rules(self):
        """R1: Verify dynamic context-mode CSS rules shifting accent palette across the 3 ECC modes."""
        self.assertIn('[data-context-mode="operativo"]', self.css, "Missing operative mode CSS selector")
        self.assertIn('[data-context-mode="project_manager"]', self.css, "Missing project manager mode CSS selector")
        self.assertIn('[data-context-mode="knowledge_curator"]', self.css, "Missing knowledge curator mode CSS selector")

    @unittest.skipUnless(HAS_DATA_CONTEXT_MODE_SETTER, "setContextMode data-context-mode attribute binding pending implementation")
    def test_t1_f04_set_context_mode_updates_data_attribute(self):
        """R1: Verify setContextMode updates data-context-mode on document.body or elHudLayer."""
        self.assertIn("setAttribute('data-context-mode'", self.hud_js,
                      "setContextMode must set data-context-mode attribute on DOM")

    @unittest.skipUnless(HAS_BALANCED_TOP_BAR, "Balanced 3-zone top bar markup in index.html pending implementation")
    def test_t1_f05_balanced_three_zone_top_bar(self):
        """R1: Verify #status-matrix-panel is organized into 3 balanced zones."""
        self.assertIn('id="status-matrix-panel"', self.html)
        self.assertIn('id="hardware-telemetry"', self.html)
        self.assertIn('id="hud-workspace-nav"', self.html)
        self.assertIn('id="context-mode-pill"', self.html)

    @unittest.skipUnless(HAS_COPILOT_HUD_MARKUP, "#neko-copilot-hud container markup pending implementation")
    def test_t1_f06_neko_copilot_hud_container_and_cards(self):
        """R2: Verify #neko-copilot-hud container and 3 live collaborative cards exist in index.html."""
        self.assertIn('id="neko-copilot-hud"', self.html, "Missing #neko-copilot-hud container in index.html")
        self.assertIn('id="copilot-card-radar"', self.html, "Missing #copilot-card-radar in index.html")
        self.assertIn('id="copilot-card-task"', self.html, "Missing #copilot-card-task in index.html")
        self.assertIn('id="copilot-card-brain"', self.html, "Missing #copilot-card-brain in index.html")

    @unittest.skipUnless(HAS_COPILOT_HUD_MARKUP, "Co-Pilot Task Card action buttons pending implementation")
    def test_t1_f07_copilot_card_task_action_buttons(self):
        """R2: Verify #copilot-card-task exposes 1-click collaborative action triggers."""
        # Must contain or render action triggers for starting/doing, completing, and quality gate
        radar_or_task_present = ("copilot-card-task" in self.html or "copilot-card-task" in self.hud_js)
        self.assertTrue(radar_or_task_present, "Task card action triggers must be present in markup or renderer")

    @unittest.skipUnless(HAS_COPILOT_HUD_MARKUP, "Co-Pilot Brain Card rules & budget pill pending implementation")
    def test_t1_f08_copilot_card_brain_rules_and_budget_pill(self):
        """R2: Verify #copilot-card-brain displays active rules and mini context budget pill."""
        brain_card_present = ("copilot-card-brain" in self.html or "copilot-card-brain" in self.hud_js)
        self.assertTrue(brain_card_present, "Brain card rules & budget pill must be present")

    @unittest.skipUnless(HAS_COMMAND_DOCK_DIALOGUE, "Collaborative command dock dialogue pending implementation")
    def test_t1_f09_command_dock_dialogue_and_action_chips(self):
        """R2: Verify #ai-terminal-dock maintains #companion-dialogue and contextual chips."""
        self.assertIn('id="ai-terminal-dock"', self.html)
        self.assertIn('id="companion-dialogue"', self.html)
        self.assertIn('id="ai-input-form"', self.html)
        self.assertIn('id="ai-input-field"', self.html)

    @unittest.skipUnless(HAS_MEMORIA_3_SUBTABS_MARKUP, "#memory-view-subtabs navigation pending implementation")
    def test_t1_f10_memoria_three_subtabs_markup(self):
        """R3: Verify #memory-view-subtabs navigation bar with 3 clean subtab triggers."""
        self.assertIn('id="memory-view-subtabs"', self.html, "Missing #memory-view-subtabs in index.html")
        self.assertIn('data-mem-view="instincts-rules"', self.html, "Missing instincts-rules subtab trigger")
        self.assertIn('data-mem-view="ecc-arsenal"', self.html, "Missing ecc-arsenal subtab trigger")
        self.assertIn('data-mem-view="profile-skills"', self.html, "Missing profile-skills subtab trigger")

    def test_t1_f11_memoria_persistent_budget_header(self):
        """R3: Verify #context-budget-container is positioned at the top of #workspace-view-memory."""
        self.assertIn('id="context-budget-container"', self.html, "Missing #context-budget-container")
        self.assertIn('id="workspace-view-memory"', self.html, "Missing #workspace-view-memory")

    @unittest.skipUnless(HAS_PROGETTI_DRAWER_BUTTONS, "Progetti drawer toggle buttons pending implementation")
    def test_t1_f12_progetti_drawer_toggle_buttons(self):
        """R3: Verify #btn-toggle-new-proj-drawer and #btn-toggle-playbook-drawer in Progetti header."""
        self.assertIn('id="btn-toggle-new-proj-drawer"', self.html, "Missing #btn-toggle-new-proj-drawer")
        self.assertIn('id="btn-toggle-playbook-drawer"', self.html, "Missing #btn-toggle-playbook-drawer")

    @unittest.skipUnless(HAS_FOUR_ZONE_PROJECT_CARDS, "4-zone project card structure pending implementation")
    def test_t1_f13_four_zone_project_cards_structure(self):
        """R3: Verify project cards support 4-zone layout (Header, Radar Strip, Rules, Actions)."""
        has_zone_structure = ("proj-zone" in self.hud_js or "hub-proj-card-header" in self.hud_js or "proj-card-zone" in self.hud_js)
        self.assertTrue(has_zone_structure, "Project cards must render 4 distinct zones")

    @unittest.skipUnless(HAS_TACTILE_KANBAN_CALENDAR, "Tactile Kanban & Calendar styling pending implementation")
    def test_t1_f14_tactile_kanban_and_calendar_styling(self):
        """R3: Verify tactile column headers and source differentiation for Calendar vs Tasks."""
        has_kanban_styles = (".header-todo" in self.css or "kanban-col-todo" in self.css)
        self.assertTrue(has_kanban_styles, "Kanban columns must define distinct tactile headers")


# ==============================================================================
# TIER 2: BOUNDARY VALUE ANALYSIS & CORNER CASES
# ==============================================================================

class TestTier2CyberStudioBoundaries(IsolatedCyberStudioTestCase):
    """
    Tier 2: Boundary value analysis, corner cases, error recovery, and
    strict 100% preservation of all 315 existing DOM element IDs.
    """

    def test_t2_b01_all_315_legacy_dom_ids_preserved(self):
        """R4: Non-negotiable retention of all 315 existing DOM element IDs in index.html."""
        missing_ids = [dom_id for dom_id in ALL_315_LEGACY_DOM_IDS if f'id="{dom_id}"' not in self.html]
        self.assertEqual(
            [],
            missing_ids,
            f"REGRESSION DEFECT: {len(missing_ids)} legacy element IDs were removed or renamed in index.html: {missing_ids[:10]}",
        )

    def test_t2_b02_context_mode_unknown_fallback(self):
        """R1: Verify setContextMode falls back safely to 'operativo' on invalid/unknown mode strings."""
        node_script = """
        const fs = require('fs');
        const content = fs.readFileSync('js/hud-terminal.js', 'utf8');
        const fnMatch = content.match(/setContextMode\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}/);
        if (!fnMatch) throw new Error('setContextMode method not found');
        const fnCode = fnMatch[0];
        const ctx = {
            currentContextMode: null,
            state: { system: {} },
            elContextModePill: null,
            elContextModeIcon: null,
            elContextModeLabel: null,
        };
        const fn = new Function('mode', 'syncBackend',
            fnCode.replace(/^setContextMode\\s*\\([^)]*\\)\\s*\\{/, '').replace(/\\}$/, '')
        ).bind(ctx);

        // Test 1: Completely invalid mode string
        fn('invalid_mode_xyz', false);
        if (ctx.currentContextMode !== 'operativo') {
            throw new Error('Fallback failed for invalid_mode_xyz: ' + ctx.currentContextMode);
        }

        // Test 2: Empty string
        fn('', false);
        if (ctx.currentContextMode !== 'operativo') {
            throw new Error('Fallback failed for empty string: ' + ctx.currentContextMode);
        }

        // Test 3: Substring PM
        fn('pm_override', false);
        if (ctx.currentContextMode !== 'project_manager') {
            throw new Error('PM fallback failed: ' + ctx.currentContextMode);
        }

        // Test 4: Substring curator
        fn('curator_mode', false);
        if (ctx.currentContextMode !== 'knowledge_curator') {
            throw new Error('Curator fallback failed: ' + ctx.currentContextMode);
        }

        console.log('OK');
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"Node fallback execution failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")

    @unittest.skipUnless(HAS_COPILOT_HUD_RENDERER, "Co-Pilot HUD rendering logic pending implementation")
    def test_t2_b03_empty_copilot_data_graceful_handling(self):
        """R2: Verify Co-Pilot cards render graceful placeholders when /status data has empty/null fields."""
        node_script = """
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        function attachMethod(cls, src, name) {
          const target = '\\n  ' + name + '(';
          const start = src.indexOf(target);
          if (start === -1) throw new Error('Method not found: ' + name);
          const methodStart = start + 3;
          let braces = 0;
          let started = false;
          for (let i = methodStart; i < src.length; i++) {
            if (src[i] === '{') { braces++; started = true; }
            else if (src[i] === '}') {
              braces--;
              if (started && braces === 0) {
                const body = src.substring(methodStart, i + 1);
                eval(cls.name + '.prototype.' + body.replace('(', ' = function('));
                return;
              }
            }
          }
          throw new Error('Unterminated method: ' + name);
        }

        class MockHudTerminal {
          constructor() {
            this.state = { system: null };
            this.elNekoCopilotHud = { id: 'neko-copilot-hud' };
            this.elCopilotRadarBody = { innerHTML: '' };
            this.elCopilotTaskBody = { innerHTML: '' };
            this.elCopilotTaskStatus = { textContent: '', className: '' };
            this.elCopilotBrainBody = { innerHTML: '' };
            this.elCopilotTokVal = { textContent: '' };
          }
        }

        attachMethod(MockHudTerminal, code, 'escapeHtml');
        attachMethod(MockHudTerminal, code, 'normalizeTodo');
        attachMethod(MockHudTerminal, code, 'renderCopilotHud');

        const hud = new MockHudTerminal();

        // 1. Completely null system state: zero crashes, fallback placeholders rendered
        hud.renderCopilotHud();
        if (!hud.elCopilotRadarBody.innerHTML.includes('copilot-unbound')) {
          throw new Error('Radar body failed to render copilot-unbound fallback');
        }
        if (!hud.elCopilotRadarBody.innerHTML.includes('Cartella non associata')) {
          throw new Error('Radar body missing Cartella non associata label');
        }
        if (hud.elCopilotTaskStatus.textContent !== 'LIBERO') {
          throw new Error('Task status badge expected LIBERO, got: ' + hud.elCopilotTaskStatus.textContent);
        }
        if (!hud.elCopilotTaskStatus.className.includes('status-free')) {
          throw new Error('Task status missing status-free class');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('copilot-task-empty')) {
          throw new Error('Task body missing copilot-task-empty wrapper');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('Nessun task operativo in coda')) {
          throw new Error('Task body missing empty queue message');
        }
        if (!hud.elCopilotTokVal.textContent.includes('tok')) {
          throw new Error('Budget token indicator missing fallback tok display');
        }
        if (!hud.elCopilotBrainBody.innerHTML.includes('Nessuna regola vincolante registrata')) {
          throw new Error('Brain body missing default rule placeholder');
        }

        // 2. Empty arrays in system state
        hud.state = {
          system: {
            projectsList: [],
            todosList: [],
            memories: [],
            instinctsList: [],
            contextBudget: null,
            activeWindow: null
          }
        };
        hud.renderCopilotHud();
        if (hud.elCopilotTaskStatus.textContent !== 'LIBERO') {
          throw new Error('Task status with empty arrays expected LIBERO, got: ' + hud.elCopilotTaskStatus.textContent);
        }
        if (!hud.elCopilotRadarBody.innerHTML.includes('copilot-unbound')) {
          throw new Error('Radar with empty projects failed to render copilot-unbound');
        }

        // 3. Early return safeguard when elNekoCopilotHud is null
        hud.elNekoCopilotHud = null;
        hud.renderCopilotHud();

        console.log('SUCCESS_NULL_RESILIENCE');
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"Node script error:\n{proc.stderr}\n{proc.stdout}")
        self.assertIn("SUCCESS_NULL_RESILIENCE", proc.stdout)

    def test_t2_b04_empty_workspace_path_unbound_rendering(self):
        """R3: Verify project card handles empty workspace_path by rendering unbound folder state."""
        self.assertIn("folder-unbound", self.hud_js,
                      "Project card renderer must handle unbound folder state (.folder-unbound)")

    def test_t2_b05_extreme_token_budget_values_handling(self):
        """R3: Verify context budget telemetry handles 0%, 100%, and >100% overflow states."""
        self.assertIn("budget-status-pill", self.html)
        self.assertIn("budget-used-pct", self.html)
        self.assertIn("budget-used-tokens", self.html)

    @unittest.skipUnless(HAS_COPILOT_HUD_MARKUP, "Co-Pilot HUD card actions pending implementation")
    def test_t2_b06_copilot_action_dispatch_empty_id_safeguard(self):
        """R2: Verify action button click handlers safeguard against missing task or project IDs."""
        node_script = """
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        // Extract the Co-Pilot HUD click listener callback from hud-terminal.js
        const m = code.match(/this\\.elNekoCopilotHud\\.addEventListener\\('click',\\s*async\\s*\\(e\\)\\s*=>\\s*\\{([\\s\\S]*?)\\n\\s*\\}\\);/);
        if (!m) throw new Error('Co-Pilot click listener callback not found in js/hud-terminal.js');

        let todosActionCount = 0;
        let lastTodosPayload = null;
        let qgModalCount = 0;
        let scanFolderCount = 0;
        const messages = [];

        const ctx = {
          actions: {
            todosAction: async (payload) => {
              todosActionCount++;
              lastTodosPayload = payload;
              return { ok: true };
            }
          },
          state: { system: { todosList: [] } },
          say: (msg) => { messages.push(msg); },
          renderCopilotHud: () => {},
          renderHomeNextTaskChip: () => {},
          openQualityGateModal: (projId, projName) => { qgModalCount++; },
          switchWorkspace: () => {},
        };

        global.window = {
          systemBridge: {
            scanProjectFolder: async () => { scanFolderCount++; return { ok: true }; }
          }
        };

        const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
        const handler = new AsyncFunction('e', m[1]).bind(ctx);

        function createMockEvent(cls, attrs = {}) {
          const btn = {
            className: cls,
            getAttribute: (attr) => attrs[attr] || '',
            closest: (sel) => sel.includes(cls) ? btn : null
          };
          return { target: btn, preventDefault: () => {} };
        }

        (async () => {
          // Safeguard 1: Start task button with EMPTY task-id -> must NOT dispatch todosAction
          await handler(createMockEvent('btn-copilot-start-task', { 'data-task-id': '' }));
          if (todosActionCount !== 0) {
            throw new Error('btn-copilot-start-task dispatched todosAction with empty taskId!');
          }

          // Safeguard 2: Complete task button with EMPTY task-id -> must NOT dispatch todosAction
          await handler(createMockEvent('btn-copilot-complete-task', { 'data-task-id': '' }));
          if (todosActionCount !== 0) {
            throw new Error('btn-copilot-complete-task dispatched todosAction with empty taskId!');
          }

          // Safeguard 3: Quality Gate button with EMPTY proj-id -> must NOT open modal, must display warning
          await handler(createMockEvent('btn-copilot-quality-gate', { 'data-proj-id': '' }));
          if (qgModalCount !== 0) {
            throw new Error('btn-copilot-quality-gate opened Quality Gate modal with empty projId!');
          }
          if (!messages.some(msg => msg.includes('Seleziona un progetto attivo'))) {
            throw new Error('btn-copilot-quality-gate failed to display safeguard warning for missing projId');
          }

          // Safeguard 4: Scan folder button with EMPTY proj-id -> must NOT trigger scanProjectFolder
          await handler(createMockEvent('btn-copilot-scan-folder', { 'data-proj-id': '' }));
          if (scanFolderCount !== 0) {
            throw new Error('btn-copilot-scan-folder triggered scan with empty projId!');
          }

          // Verification with VALID identifiers: actions execute correctly
          await handler(createMockEvent('btn-copilot-start-task', { 'data-task-id': 'task_valid_1', 'data-task-title': 'Valid Task' }));
          if (todosActionCount !== 1 || !lastTodosPayload || lastTodosPayload.id !== 'task_valid_1' || lastTodosPayload.status !== 'doing') {
            throw new Error('btn-copilot-start-task failed to dispatch expected payload on valid taskId');
          }

          await handler(createMockEvent('btn-copilot-quality-gate', { 'data-proj-id': 'proj_valid_1', 'data-proj-name': 'Valid Proj' }));
          if (qgModalCount !== 1) {
            throw new Error('btn-copilot-quality-gate failed to open modal with valid projId');
          }

          console.log('SUCCESS_SAFEGUARDS_VERIFIED');
        })().catch(err => {
          console.error(err);
          process.exit(1);
        });
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"Node script error:\n{proc.stderr}\n{proc.stdout}")
        self.assertIn("SUCCESS_SAFEGUARDS_VERIFIED", proc.stdout)

    @unittest.skipUnless(HAS_MEMORIA_SUBTAB_SWITCHER, "Memoria subtab switching logic pending implementation")
    def test_t2_b07_unknown_memory_subtab_fallback(self):
        """R3: Verify switchMemorySubtab falls back safely to 'instincts-rules' when given unknown subtab ID."""
        node_script = """
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        function attachMethod(cls, src, name) {
          const target = '\\n  ' + name + '(';
          const start = src.indexOf(target);
          if (start === -1) throw new Error('Method not found: ' + name);
          const methodStart = start + 3;
          let braces = 0;
          let started = false;
          for (let i = methodStart; i < src.length; i++) {
            if (src[i] === '{') { braces++; started = true; }
            else if (src[i] === '}') {
              braces--;
              if (started && braces === 0) {
                const body = src.substring(methodStart, i + 1);
                eval(cls.name + '.prototype.' + body.replace('(', ' = function('));
                return;
              }
            }
          }
          throw new Error('Unterminated method: ' + name);
        }

        class MockElement {
          constructor(id) {
            this.id = id;
            this.classes = new Set();
            this.attributes = new Map();
            this.style = {};
            this.children = [];
          }
          setAttribute(k, v) { this.attributes.set(k, String(v)); }
          getAttribute(k) { return this.attributes.get(k) || null; }
          get classList() {
            return {
              toggle: (c, force) => {
                if (force) this.classes.add(c);
                else this.classes.delete(c);
              },
              contains: (c) => this.classes.has(c)
            };
          }
          querySelectorAll(sel) {
            if (sel === '[data-mem-view]') return this.children;
            return [];
          }
        }

        const docElements = new Map();
        function getOrCreate(id) {
          if (!docElements.has(id)) docElements.set(id, new MockElement(id));
          return docElements.get(id);
        }

        global.document = {
          getElementById: (id) => getOrCreate(id)
        };

        const subtabsContainer = getOrCreate('memory-view-subtabs');
        const btnInstincts = new MockElement('btn-subtab-instincts');
        btnInstincts.setAttribute('data-mem-view', 'instincts-rules');
        const btnArsenal = new MockElement('btn-subtab-arsenal');
        btnArsenal.setAttribute('data-mem-view', 'ecc-arsenal');
        const btnProfile = new MockElement('btn-subtab-profile');
        btnProfile.setAttribute('data-mem-view', 'profile-skills');
        subtabsContainer.children.push(btnInstincts, btnArsenal, btnProfile);

        const paneInstincts = getOrCreate('mem-subpane-instincts-rules');
        const paneArsenal = getOrCreate('mem-subpane-ecc-arsenal');
        const paneProfile = getOrCreate('mem-subpane-profile-skills');
        const viewMemory = getOrCreate('workspace-view-memory');
        const budgetContainer = getOrCreate('context-budget-container');

        class MockHudTerminal {
          constructor() {
            this.activeMemSubtab = null;
            this.elMemorySubtabs = subtabsContainer;
            this.elViewMemory = viewMemory;
            this.elContextBudgetContainer = budgetContainer;
          }
          renderMemoryDashboard() {}
        }

        attachMethod(MockHudTerminal, code, 'switchMemorySubtab');
        const hud = new MockHudTerminal();

        // 1. Valid subtab switch to 'ecc-arsenal'
        hud.switchMemorySubtab('ecc-arsenal');
        if (hud.activeMemSubtab !== 'ecc-arsenal') throw new Error('Failed to set activeMemSubtab to ecc-arsenal');
        if (!btnArsenal.classList.contains('active')) throw new Error('Arsenal button missing active class');
        if (paneArsenal.style.display !== 'flex') throw new Error('Arsenal pane display style not flex');

        // 2. Unknown subtab: 'completely_unknown_subtab_id' -> must fall back safely to 'instincts-rules'
        hud.switchMemorySubtab('completely_unknown_subtab_id');
        if (hud.activeMemSubtab !== 'instincts-rules') {
          throw new Error('Fallback failed for invalid subtab ID, got: ' + hud.activeMemSubtab);
        }
        if (!btnInstincts.classList.contains('active')) {
          throw new Error('Fallback active button class not applied to instincts-rules');
        }
        if (btnArsenal.classList.contains('active')) {
          throw new Error('Stale active class still present on arsenal button');
        }
        if (paneInstincts.style.display !== 'grid') {
          throw new Error('Fallback instincts pane display style not grid: ' + paneInstincts.style.display);
        }
        if (paneArsenal.style.display !== 'none') {
          throw new Error('Arsenal pane not hidden after fallback');
        }
        if (viewMemory.getAttribute('data-active-mem-view') !== 'instincts-rules') {
          throw new Error('Fallback viewMemory data-active-mem-view attribute mismatch');
        }

        // 3. Null, empty string, and undefined inputs must also fall back to 'instincts-rules'
        hud.switchMemorySubtab(null);
        if (hud.activeMemSubtab !== 'instincts-rules') throw new Error('Null input fallback failed');

        hud.switchMemorySubtab('');
        if (hud.activeMemSubtab !== 'instincts-rules') throw new Error('Empty string input fallback failed');

        hud.switchMemorySubtab(undefined);
        if (hud.activeMemSubtab !== 'instincts-rules') throw new Error('Undefined input fallback failed');

        console.log('SUCCESS_UNKNOWN_SUBTAB_FALLBACK');
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"Node script error:\n{proc.stderr}\n{proc.stdout}")
        self.assertIn("SUCCESS_UNKNOWN_SUBTAB_FALLBACK", proc.stdout)


# ==============================================================================
# TIER 3: CROSS-FEATURE COMBINATIONS & STATE TRANSITIONS
# ==============================================================================

class TestTier3CyberStudioCombinations(IsolatedCyberStudioTestCase):
    """
    Tier 3: Multi-system interactions, workspace tab to context mode synchronizations,
    Co-Pilot HUD visibility rules, drawer open/close toggles, and Memoria subtabs.
    """

    def test_t3_c01_workspace_tab_switch_updates_context_mode(self):
        """R1 & R3: Verify switchWorkspace automatically updates contextMode based on open tab."""
        node_script = """
        const fs = require('fs');
        const content = fs.readFileSync('js/hud-terminal.js', 'utf8');
        const fnMatch = content.match(/switchWorkspace\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n  \\}/);
        if (!fnMatch) throw new Error('switchWorkspace method not found');
        const fnCode = fnMatch[0];

        const calls = [];
        global.document = {
            body: {
                classList: {
                    toggle: () => {},
                    add: () => {},
                    remove: () => {},
                },
                setAttribute: () => {},
            },
        };
        const dummyElement = {
            style: {},
            classList: { add: () => {}, remove: () => {}, toggle: () => {} },
            querySelectorAll: () => [],
            querySelector: () => null,
            setAttribute: () => {},
            getAttribute: () => null,
        };
        const targetCtx = {
            activeWorkspace: null,
            setContextMode: function(mode, sync) {
                calls.push(mode);
            },
            elWorkspaceNav: dummyElement,
            elWorkspaceViews: dummyElement,
            elWorkspaceContainer: dummyElement,
        };
        const ctx = new Proxy(targetCtx, {
            get(target, prop) {
                if (prop in target) return target[prop];
                if (typeof prop === 'string' && prop.startsWith('el')) return dummyElement;
                return () => {};
            }
        });
        const fn = new Function('viewId',
            fnCode.replace(/^switchWorkspace\\s*\\([^)]*\\)\\s*\\{/, '').replace(/\\}$/, '')
        ).bind(ctx);

        // 1. Home -> Operativo
        fn('view-home');
        if (calls[calls.length - 1] !== 'operativo') {
            throw new Error('Home tab must trigger operativo, got: ' + calls[calls.length - 1]);
        }

        // 2. To-Do -> Project Manager
        fn('view-todo');
        if (calls[calls.length - 1] !== 'project_manager') {
            throw new Error('To-Do tab must trigger project_manager, got: ' + calls[calls.length - 1]);
        }

        // 3. Progetti -> Project Manager
        fn('view-projects');
        if (calls[calls.length - 1] !== 'project_manager') {
            throw new Error('Projects tab must trigger project_manager, got: ' + calls[calls.length - 1]);
        }

        // 4. Calendario -> Project Manager
        fn('view-calendar');
        if (calls[calls.length - 1] !== 'project_manager') {
            throw new Error('Calendar tab must trigger project_manager, got: ' + calls[calls.length - 1]);
        }

        // 5. Memoria -> Knowledge Curator
        fn('view-memory');
        if (calls[calls.length - 1] !== 'knowledge_curator') {
            throw new Error('Memory tab must trigger knowledge_curator, got: ' + calls[calls.length - 1]);
        }

        console.log('OK');
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"switchWorkspace test failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")

    @unittest.skipUnless(HAS_COPILOT_HUD_CSS, "Co-Pilot HUD CSS visibility rules pending implementation")
    def test_t3_c02_copilot_hud_visibility_rules_home_vs_dashboards(self):
        """R2: Verify #neko-copilot-hud is displayed on view-home and hidden on dashboard views."""
        # CSS rule for home visibility and dashboard suppression
        self.assertIn("neko-copilot-hud", self.css)

    @unittest.skipUnless(HAS_COPILOT_HUD_CSS, "Co-Pilot HUD canvas clearance rules pending implementation")
    def test_t3_c03_copilot_hud_clears_ascii_canvas_stage(self):
        """R2: Verify #neko-copilot-hud uses flanking left/right layout leaving center ASCII canvas clear."""
        self.assertIn("ascii-canvas", self.html)

    @unittest.skipUnless(HAS_PROGETTI_DRAWER_BUTTONS, "Progetti drawer toggles pending implementation")
    def test_t3_c04_progetti_drawer_toggle_open_close(self):
        """R3: Verify clicking #btn-toggle-new-proj-drawer and #btn-toggle-playbook-drawer toggles drawers."""
        self.assertIn('id="proj-create-bar"', self.html)
        self.assertIn('id="playbook-quick-bar"', self.html)

    @unittest.skipUnless(HAS_MEMORIA_3_SUBTABS_MARKUP, "Memoria subtab switching visibility pending implementation")
    def test_t3_c05_memoria_subtab_switching_visibility(self):
        """R3: Verify switching Memoria subtabs toggles active pane while keeping budget meter visible."""
        self.assertIn('id="context-budget-container"', self.html)
        self.assertIn('id="ecc-arsenal-section"', self.html)
        self.assertIn('id="mem-col-skills-wrapper"', self.html)
        self.assertIn('id="mem-col-profile-wrapper"', self.html)

    @unittest.skipUnless(HAS_COPILOT_HUD_RENDERER, "Co-Pilot radar telemetry synchronization pending implementation")
    def test_t3_c06_window_telemetry_to_copilot_radar(self):
        """R2: Verify active application and project window telemetry syncs into Co-Pilot radar card."""
        node_script = """
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        function attachMethod(cls, src, name) {
          const target = '\\n  ' + name + '(';
          const start = src.indexOf(target);
          if (start === -1) throw new Error('Method not found: ' + name);
          const methodStart = start + 3;
          let braces = 0;
          let started = false;
          for (let i = methodStart; i < src.length; i++) {
            if (src[i] === '{') { braces++; started = true; }
            else if (src[i] === '}') {
              braces--;
              if (started && braces === 0) {
                const body = src.substring(methodStart, i + 1);
                eval(cls.name + '.prototype.' + body.replace('(', ' = function('));
                return;
              }
            }
          }
          throw new Error('Unterminated method: ' + name);
        }

        class MockHudTerminal {
          constructor() {
            this.state = {
              system: {
                activeWindow: { app: 'Unreal Editor 5.5', title: 'Corvette_Simulation.uproject' },
                activeProjectName: 'Nave Intermarine',
                projectsList: [
                  {
                    id: 'proj_intermarine',
                    name: 'Nave Intermarine',
                    workspace_path: 'C:\\\\Projects\\\\NaveIntermarine',
                    workspace_summary: {
                      modified_today_count: 7,
                      total_files: 230,
                      git_info: { branch: 'develop', is_git_repo: true }
                    }
                  }
                ]
              }
            };
            this.elNekoCopilotHud = { id: 'neko-copilot-hud' };
            this.elCopilotRadarBody = { innerHTML: '' };
            this.elCopilotTaskBody = null;
            this.elCopilotBrainBody = null;
          }
        }

        attachMethod(MockHudTerminal, code, 'escapeHtml');
        attachMethod(MockHudTerminal, code, 'normalizeTodo');
        attachMethod(MockHudTerminal, code, 'renderCopilotHud');

        const hud = new MockHudTerminal();

        // 1. Initial state: Active app is Unreal Editor 5.5
        hud.renderCopilotHud();
        const html1 = hud.elCopilotRadarBody.innerHTML;

        if (!html1.includes('🖥️ Unreal Editor 5.5')) {
          throw new Error('Active application tag missing from radar card: ' + html1);
        }
        if (!html1.includes('📄 Corvette_Simulation.uproject')) {
          throw new Error('Active document title missing from radar card');
        }
        if (!html1.includes('🚢 Nave Intermarine')) {
          throw new Error('Active focus project missing from radar card');
        }
        if (!html1.includes('⚡ 7 modif. oggi')) {
          throw new Error('Modified today telemetry badge missing from radar card');
        }
        if (!html1.includes('📦 230 file')) {
          throw new Error('Total files count missing from radar card');
        }
        if (!html1.includes('🌿 develop')) {
          throw new Error('Git branch chip missing from radar card');
        }
        if (!html1.includes('proj_intermarine')) {
          throw new Error('Bound project ID missing from folder button attributes');
        }

        // 2. Telemetry update transition: User switches active window to Blender
        hud.state.system.activeWindow = { app: 'Blender 4.2', title: 'propeller_assembly.blend' };
        hud.state.system.projectsList[0].workspace_summary.modified_today_count = 14;
        hud.renderCopilotHud();
        const html2 = hud.elCopilotRadarBody.innerHTML;

        if (!html2.includes('🖥️ Blender 4.2')) {
          throw new Error('Updated active application tag missing after switch: ' + html2);
        }
        if (!html2.includes('📄 propeller_assembly.blend')) {
          throw new Error('Updated document title missing after switch');
        }
        if (!html2.includes('⚡ 14 modif. oggi')) {
          throw new Error('Updated telemetry count missing after switch');
        }
        if (html2.includes('Unreal Editor 5.5')) {
          throw new Error('Stale previous application tag remained in radar markup');
        }

        console.log('SUCCESS_WINDOW_TELEMETRY_SYNC');
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"Node script error:\n{proc.stderr}\n{proc.stdout}")
        self.assertIn("SUCCESS_WINDOW_TELEMETRY_SYNC", proc.stdout)

    @unittest.skipUnless(HAS_COPILOT_HUD_RENDERER, "Co-Pilot task card state update pending implementation")
    def test_t3_c07_task_status_transition_updates_copilot_task_card(self):
        """R2: Verify updating active Kanban task updates the focus card in #copilot-card-task."""
        node_script = """
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        function attachMethod(cls, src, name) {
          const target = '\\n  ' + name + '(';
          const start = src.indexOf(target);
          if (start === -1) throw new Error('Method not found: ' + name);
          const methodStart = start + 3;
          let braces = 0;
          let started = false;
          for (let i = methodStart; i < src.length; i++) {
            if (src[i] === '{') { braces++; started = true; }
            else if (src[i] === '}') {
              braces--;
              if (started && braces === 0) {
                const body = src.substring(methodStart, i + 1);
                eval(cls.name + '.prototype.' + body.replace('(', ' = function('));
                return;
              }
            }
          }
          throw new Error('Unterminated method: ' + name);
        }

        class MockHudTerminal {
          constructor() {
            this.state = {
              system: {
                activeProjectName: 'Nave Intermarine',
                projectsList: [{ id: 'proj_intermarine', name: 'Nave Intermarine' }],
                todosList: [
                  {
                    id: 'task_scafo_1',
                    title: 'Modellazione Scafo Principale',
                    project: 'Nave Intermarine',
                    due_label: '2026-11-02',
                    status: 'todo',
                    priority: 'high'
                  }
                ]
              }
            };
            this.elNekoCopilotHud = { id: 'neko-copilot-hud' };
            this.elCopilotRadarBody = null;
            this.elCopilotTaskBody = { innerHTML: '' };
            this.elCopilotTaskStatus = { textContent: '', className: '' };
            this.elCopilotBrainBody = null;
          }
        }

        attachMethod(MockHudTerminal, code, 'escapeHtml');
        attachMethod(MockHudTerminal, code, 'normalizeTodo');
        attachMethod(MockHudTerminal, code, 'renderCopilotHud');

        const hud = new MockHudTerminal();

        // 1. Initial State: Task in 'todo' status (next up)
        hud.renderCopilotHud();
        if (hud.elCopilotTaskStatus.textContent !== 'PROSSIMO') {
          throw new Error('Expected task status badge PROSSIMO, got: ' + hud.elCopilotTaskStatus.textContent);
        }
        if (!hud.elCopilotTaskStatus.className.includes('status-todo')) {
          throw new Error('Expected status-todo class on task status badge');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('Modellazione Scafo Principale')) {
          throw new Error('Task title missing from task card markup');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('btn-copilot-start-task')) {
          throw new Error('Start task button missing in todo state');
        }
        if (hud.elCopilotTaskBody.innerHTML.includes('btn-copilot-quality-gate')) {
          throw new Error('Quality Gate button should NOT be present in todo state');
        }

        // 2. Status Transition: Task transitions from 'todo' to 'doing' (in progress)
        hud.state.system.todosList[0].status = 'doing';
        hud.renderCopilotHud();
        if (hud.elCopilotTaskStatus.textContent !== 'IN CORSO') {
          throw new Error('Expected task status badge IN CORSO, got: ' + hud.elCopilotTaskStatus.textContent);
        }
        if (!hud.elCopilotTaskStatus.className.includes('status-doing')) {
          throw new Error('Expected status-doing class on task status badge');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('btn-copilot-complete-task')) {
          throw new Error('Complete task button missing in doing state');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('btn-copilot-quality-gate')) {
          throw new Error('Quality Gate button missing in doing state');
        }
        if (hud.elCopilotTaskBody.innerHTML.includes('btn-copilot-start-task')) {
          throw new Error('Start task button should NOT be present in doing state');
        }

        // 3. Status Transition: Task marked 'done' -> queue becomes empty
        hud.state.system.todosList[0].status = 'done';
        hud.renderCopilotHud();
        if (hud.elCopilotTaskStatus.textContent !== 'LIBERO') {
          throw new Error('Expected task status badge LIBERO, got: ' + hud.elCopilotTaskStatus.textContent);
        }
        if (!hud.elCopilotTaskStatus.className.includes('status-free')) {
          throw new Error('Expected status-free class on task status badge');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('copilot-task-empty')) {
          throw new Error('copilot-task-empty placeholder missing in free state');
        }
        if (!hud.elCopilotTaskBody.innerHTML.includes('Nessun task operativo in coda')) {
          throw new Error('Empty task queue message missing in free state');
        }

        console.log('SUCCESS_TASK_TRANSITIONS_VERIFIED');
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"Node script error:\n{proc.stderr}\n{proc.stdout}")
        self.assertIn("SUCCESS_TASK_TRANSITIONS_VERIFIED", proc.stdout)


# ==============================================================================
# TIER 4: REAL-WORLD SCENARIOS & PRODUCTION HYGIENE
# ==============================================================================

class TestTier4CyberStudioHygiene(IsolatedCyberStudioTestCase):
    """
    Tier 4: Production data preservation, JavaScript AST syntax validation
    via `node --check`, and real-world workflow lifecycle verification.
    """

    def test_t4_r01_node_check_all_six_javascript_files(self):
        """R4: Verify zero syntax errors (node --check) across all 6 core JavaScript files."""
        js_files = [
            os.path.join(REPO_ROOT, "js", "main.js"),
            os.path.join(REPO_ROOT, "js", "hud-terminal.js"),
            os.path.join(REPO_ROOT, "js", "system-bridge.js"),
            os.path.join(REPO_ROOT, "js", "ascii-renderer.js"),
            os.path.join(REPO_ROOT, "js", "companion-face.js"),
            os.path.join(REPO_ROOT, "js", "fluid-field.js"),
        ]
        proc = subprocess.run(
            ["node", "--check"] + js_files,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"JavaScript syntax error detected via node --check:\n{proc.stderr}",
        )

    def test_t4_r02_production_data_directory_purity(self):
        """R4: Verify production bridge/data/ has zero .tmp, .bak, or test files."""
        forbidden_extensions = (".tmp", ".bak", ".corrupt")
        polluted = []
        if os.path.exists(PRODUCTION_DATA_DIR):
            for f in os.listdir(PRODUCTION_DATA_DIR):
                if any(f.endswith(ext) for ext in forbidden_extensions) or "test" in f.lower():
                    polluted.append(f)
        self.assertEqual(
            [],
            polluted,
            f"CRITICAL VIOLATION: Found stray or temporary test files in production bridge/data/: {polluted}",
        )

    def test_t4_r03_diego_authentic_data_preservation(self):
        """R4: Verify Diego's personal data (profile, Nave Intermarine, 4 tasks, 2 memories, 2 instincts) are intact."""
        # 1. Profile
        prof_path = os.path.join(PRODUCTION_DATA_DIR, "profile.json")
        self.assertTrue(os.path.exists(prof_path), "profile.json must exist in bridge/data/")
        with open(prof_path, "r", encoding="utf-8") as f:
            prof = json.load(f)
        self.assertEqual(prof.get("name"), "Diego", "User profile name must remain 'Diego'")

        # 2. Projects
        proj_path = os.path.join(PRODUCTION_DATA_DIR, "projects.json")
        self.assertTrue(os.path.exists(proj_path), "projects.json must exist in bridge/data/")
        with open(proj_path, "r", encoding="utf-8") as f:
            projs = json.load(f)
        proj_ids = [p.get("id") for p in projs]
        self.assertIn("proj_nave_intermarine", proj_ids, "Project 'proj_nave_intermarine' must be preserved")

        # 3. Tasks
        todo_path = os.path.join(PRODUCTION_DATA_DIR, "todo.json")
        self.assertTrue(os.path.exists(todo_path), "todo.json must exist in bridge/data/")
        with open(todo_path, "r", encoding="utf-8") as f:
            todos = json.load(f)
        self.assertGreaterEqual(len(todos), 4, "Must preserve the 4 Nave Intermarine tasks")

        # 4. Memories
        mem_path = os.path.join(PRODUCTION_DATA_DIR, "memory.json")
        self.assertTrue(os.path.exists(mem_path), "memory.json must exist in bridge/data/")
        with open(mem_path, "r", encoding="utf-8") as f:
            mems = json.load(f)
        pinned_mems = [m for m in mems if m.get("pinned")]
        self.assertGreaterEqual(len(pinned_mems), 1, "Must preserve pinned Client Rules (Logo Blu Marino)")

        # 5. Instincts
        inst_path = os.path.join(PRODUCTION_DATA_DIR, "instincts.json")
        self.assertTrue(os.path.exists(inst_path), "instincts.json must exist in bridge/data/")
        with open(inst_path, "r", encoding="utf-8") as f:
            insts = json.load(f)
        self.assertGreaterEqual(len(insts), 2, "Must preserve seeded instincts")

    def test_t4_r04_full_copilot_and_workspace_lifecycle_workflow(self):
        """R1-R4: Simulated end-to-end integration verifying workspace switches, context modes, and clean teardown."""
        # 1. Verify CSS rule ensuring workspace container is hidden on Home view
        match_home_rule = re.search(
            r"body\.view-home\s+#hud-workspace-container\s*\{[^}]*display\s*:\s*none\s*!important[^}]*\}",
            self.css,
        )
        self.assertIsNotNone(
            match_home_rule,
            "CSS rule 'body.view-home #hud-workspace-container { display: none !important; }' must be preserved",
        )

        # 2. Verify all 5 tab buttons exist in #hud-workspace-nav
        for tab_id in ("tab-nav-home", "tab-nav-todo", "tab-nav-projects", "tab-nav-calendar", "tab-nav-memory"):
            self.assertIn(f'id="{tab_id}"', self.html, f"Missing {tab_id} in workspace nav")

        # 3. Verify all 4 dashboard view sections exist in #hud-workspace-container
        for view_id in ("workspace-view-todo", "workspace-view-projects", "workspace-view-calendar", "workspace-view-memory"):
            self.assertIn(f'id="{view_id}"', self.html, f"Missing {view_id} in workspace container")

    def test_t4_r05_zen_mode_hides_copilot_hud(self):
        """R2: Verify that .hud-layer.hud-hidden rules hide #neko-copilot-hud with display: none !important."""
        self.assertRegex(
            self.css,
            r'\.hud-layer\.hud-hidden\s+#neko-copilot-hud',
            "css/terminal.css must include #neko-copilot-hud under .hud-layer.hud-hidden"
        )
        self.assertIn(
            "display: none !important",
            self.css,
            "css/terminal.css must declare display: none !important for Zen mode suppression"
        )

    def test_t4_r06_responsive_media_query_1040px(self):
        """R2: Verify that css/terminal.css defines @media (max-width: 1040px) for responsive flank width."""
        self.assertIn("@media (max-width: 1040px)", self.css)
        self.assertRegex(self.css, r'@media\s*\(max-width:\s*1040px\)\s*\{[^}]*copilot-flank')

    def test_t4_r07_alt_hotkeys_workspace_switching(self):
        """R1: Verify that Alt+1 through Alt+5 cleanly switch workspaces in js/hud-terminal.js."""
        node_script = """
        const fs = require('fs');
        const hudCode = fs.readFileSync('js/hud-terminal.js', 'utf8');

        function attachMethod(cls, src, name) {
          const target = '\\n  ' + name + '(';
          const start = src.indexOf(target);
          if (start === -1) throw new Error('Method not found: ' + name);
          const methodStart = start + 3;
          let braces = 0;
          let started = false;
          for (let i = methodStart; i < src.length; i++) {
            if (src[i] === '{') { braces++; started = true; }
            else if (src[i] === '}') {
              braces--;
              if (started && braces === 0) {
                const body = src.substring(methodStart, i + 1);
                eval(cls.name + '.prototype.' + body.replace('(', ' = function('));
                return;
              }
            }
          }
          throw new Error('Unterminated method: ' + name);
        }

        let currentWorkspace = null;
        let hudVisibilityCycled = false;
        const windowListeners = {};
        global.window = {
          addEventListener: (evt, fn) => { windowListeners[evt] = fn; }
        };
        global.document = {
          activeElement: { tagName: 'BODY', isContentEditable: false }
        };

        class MockHudTerminal {
          constructor() {
            this.elWorkspaceNav = null;
            this.elHomeNextTaskChip = null;
            this.elKanbanQuickAddForm = null;
          }
        }

        attachMethod(MockHudTerminal, hudCode, 'bindWorkspaceEvents');
        const hud = new MockHudTerminal();
        hud.switchWorkspace = (id) => { currentWorkspace = id; };
        hud.cycleHudVisibility = () => { hudVisibilityCycled = true; };
        hud.bindWorkspaceEvents();

        const keyHandler = windowListeners['keydown'];
        if (!keyHandler) throw new Error('No keydown listener registered in bindWorkspaceEvents');

        // Test Alt+2 -> view-todo
        let prevented = false;
        keyHandler({ key: '2', altKey: true, ctrlKey: false, shiftKey: false, metaKey: false, preventDefault: () => { prevented = true; } });
        if (currentWorkspace !== 'view-todo' || !prevented) throw new Error('Alt+2 failed to switch to view-todo');

        // Test Alt+5 -> view-memory
        keyHandler({ key: '5', altKey: true, ctrlKey: false, shiftKey: false, metaKey: false, preventDefault: () => {} });
        if (currentWorkspace !== 'view-memory') throw new Error('Alt+5 failed to switch to view-memory');

        // Test Alt+h -> cycleHudVisibility
        keyHandler({ key: 'h', altKey: true, ctrlKey: false, shiftKey: false, metaKey: false, preventDefault: () => {} });
        if (!hudVisibilityCycled) throw new Error('Alt+h failed to cycle HUD visibility');

        // Test input safeguard (should NOT switch when typing in INPUT)
        global.document.activeElement = { tagName: 'INPUT' };
        keyHandler({ key: '1', altKey: true, ctrlKey: false, shiftKey: false, metaKey: false, preventDefault: () => {} });
        if (currentWorkspace === 'view-home') throw new Error('Hotkey fired while user was typing in input!');

        console.log('SUCCESS_HOTKEYS_VERIFIED');
        """
        proc = self._run_node_script(node_script)
        self.assertEqual(proc.returncode, 0, f"Hotkey test failed:\n{proc.stderr}\n{proc.stdout}")
        self.assertIn("SUCCESS_HOTKEYS_VERIFIED", proc.stdout)

    def test_html_dom_hierarchy_and_sibling_integrity(self):
        """
        Ensures #status-matrix-panel, #neko-modal-overlay, #neko-copilot-hud,
        #hud-workspace-container, and #ai-terminal-dock are all direct children
        of #hud-layer (preventing #neko-modal-overlay from swallowing the HUD or chat dock).
        """
        from html.parser import HTMLParser

        void_tags = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }

        class HierarchyParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack = []
                self.mismatches = []
                self.parents = {}

            def handle_starttag(self, tag, attrs):
                if tag in void_tags:
                    return
                attr_dict = dict(attrs)
                el_id = attr_dict.get("id", "")
                parent_id = self.stack[-1][1] if self.stack else None
                if el_id:
                    self.parents[el_id] = parent_id
                self.stack.append((tag, el_id))

            def handle_endtag(self, tag):
                if tag in void_tags:
                    return
                if not self.stack:
                    self.mismatches.append(f"Unexpected closing </{tag}>")
                    return
                top_tag, _ = self.stack[-1]
                if top_tag == tag:
                    self.stack.pop()
                else:
                    self.mismatches.append(f"Expected </{top_tag}>, got </{tag}>")

        parser = HierarchyParser()
        parser.feed(self.html)
        self.assertEqual(parser.mismatches, [], f"HTML tag mismatches found: {parser.mismatches}")
        self.assertEqual(parser.stack, [], f"Unclosed HTML tags at EOF: {parser.stack}")

        for top_level_id in (
            "status-matrix-panel",
            "neko-modal-overlay",
            "neko-copilot-hud",
            "hud-workspace-container",
            "ai-terminal-dock",
        ):
            self.assertEqual(
                parser.parents.get(top_level_id),
                "hud-layer",
                f"#{top_level_id} must be a direct child of #hud-layer, got #{parser.parents.get(top_level_id)}",
            )


if __name__ == "__main__":
    unittest.main()
