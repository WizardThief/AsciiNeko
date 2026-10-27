"""
tests/test_ecc_hud_and_modes.py
Verification suite for Milestone 4 (Features F22 through F28):
- F22: Dynamic Context Modes (UI pill, CSS themes, SystemBridge methods, slash commands, backend API)
- F23, F24, F25: Playbook Evolution, Quality Gate Modal, and Quick Creation Bar
- F26: Real-time Context Budget Telemetry Meter (multi-segment tracks & tokens)
- F27: Atomic Instincts v2.1 HUD Column (sub-tabs, confidence bars, scope badges, card actions)
- F28: Smart Banners (DCC window task suggestions, session checkpoints, session action handlers)
"""

import json
import os
import unittest
from io import BytesIO

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestMilestone4ContextModes(unittest.TestCase):
    """F22: Dynamic Context Modes verification."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            self.css = f.read()
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            self.hud_js = f.read()
        with open(os.path.join(REPO_ROOT, "js", "system-bridge.js"), "r", encoding="utf-8") as f:
            self.bridge_js = f.read()

    def test_context_mode_pill_markup_and_css(self):
        # HTML Elements
        self.assertIn('id="context-mode-pill"', self.html)
        self.assertIn('id="context-mode-icon"', self.html)
        self.assertIn('id="context-mode-label"', self.html)

        # CSS Mode Classes
        self.assertIn(".context-mode-pill", self.css)
        self.assertIn(".context-mode-pill.mode-operativo", self.css)
        self.assertIn(".context-mode-pill.mode-project_manager", self.css)
        self.assertIn(".context-mode-pill.mode-knowledge_curator", self.css)

    def test_hud_terminal_context_mode_methods(self):
        self.assertIn("setContextMode(mode", self.hud_js)
        self.assertIn("this.elContextModePill", self.hud_js)
        self.assertIn("this.currentContextMode", self.hud_js)
        # Slash command /context
        self.assertIn("clean === '/context' || clean.startsWith('/context ')", self.hud_js)

    def test_system_bridge_context_mode(self):
        self.assertIn("async setContextMode(mode)", self.bridge_js)
        self.assertIn("/api/context_mode", self.bridge_js)
        self.assertIn("this.state.system.contextMode", self.bridge_js)


class TestMilestone4PlaybookAndQualityGate(unittest.TestCase):
    """F23, F24, F25: Playbook Evolution, Quality Gate Modal, and Quick Bar."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            self.css = f.read()
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            self.hud_js = f.read()
        with open(os.path.join(REPO_ROOT, "js", "system-bridge.js"), "r", encoding="utf-8") as f:
            self.bridge_js = f.read()

    def test_playbook_quick_bar_markup_and_css(self):
        self.assertIn('id="playbook-quick-bar"', self.html)
        self.assertIn('id="hub-playbook-select"', self.html)
        self.assertIn('id="hub-playbook-new-name"', self.html)
        self.assertIn('id="hub-playbook-new-client"', self.html)
        self.assertIn('id="btn-create-from-playbook"', self.html)
        self.assertIn(".playbook-quick-bar", self.css)

    def test_project_card_action_buttons(self):
        # Card buttons exist in HUD JS rendering
        self.assertIn("btn-proj-evolve", self.hud_js)
        self.assertIn("btn-proj-quality-gate", self.hud_js)
        self.assertIn(".btn-proj-evolve", self.css)
        self.assertIn(".btn-proj-quality-gate", self.css)

    def test_quality_gate_modal_structure(self):
        self.assertIn('id="quality-gate-modal"', self.html)
        self.assertIn('id="qg-proj-title"', self.html)
        self.assertIn('id="qg-score-val"', self.html)
        self.assertIn('id="qg-status-label"', self.html)
        self.assertIn('id="qg-status-desc"', self.html)
        self.assertIn('id="qg-tasks-summary"', self.html)
        self.assertIn('id="qg-tasks-badge"', self.html)
        self.assertIn('id="qg-tasks-list"', self.html)
        self.assertIn('id="qg-rules-summary"', self.html)
        self.assertIn('id="qg-rules-badge"', self.html)
        self.assertIn('id="qg-rules-list"', self.html)
        self.assertIn('id="qg-instincts-badge"', self.html)
        self.assertIn('id="qg-instincts-list"', self.html)
        self.assertIn('id="qg-recommendations"', self.html)
        self.assertIn('id="btn-qg-retest"', self.html)
        self.assertIn('id="btn-qg-close"', self.html)

    def test_hud_terminal_playbook_and_qg_methods(self):
        self.assertIn("populatePlaybooksDropdown()", self.hud_js)
        self.assertIn("openQualityGateModal(", self.hud_js)
        self.assertIn("renderQualityGateReport(", self.hud_js)
        self.assertIn("clean === '/evolve' || clean.startsWith('/evolve ')", self.hud_js)
        self.assertIn("clean === '/quality-gate' || clean.startsWith('/quality-gate ')", self.hud_js)

    def test_system_bridge_playbook_and_qg_methods(self):
        self.assertIn("async evolveProject(projectId)", self.bridge_js)
        self.assertIn("async projectQualityGate(projectId)", self.bridge_js)
        self.assertIn("async createProjectFromPlaybook(playbookSlug, name, client)", self.bridge_js)
        self.assertIn("async fetchPlaybooks()", self.bridge_js)


class TestMilestone4ContextBudgetTelemetry(unittest.TestCase):
    """F26: Context Budget Telemetry Meter."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            self.css = f.read()
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            self.hud_js = f.read()
        with open(os.path.join(REPO_ROOT, "js", "system-bridge.js"), "r", encoding="utf-8") as f:
            self.bridge_js = f.read()

    def test_context_budget_markup(self):
        self.assertIn('id="context-budget-container"', self.html)
        self.assertIn('id="budget-used-tokens"', self.html)
        self.assertIn('id="budget-used-pct"', self.html)
        self.assertIn('id="budget-status-pill"', self.html)

        # Multi-segment track elements
        for seg_id in ["seg-system", "seg-profile", "seg-instincts", "seg-memories", "seg-projects", "seg-chat"]:
            self.assertIn(f'id="{seg_id}"', self.html)

        # Legend token counters
        for lbl_id in ["lbl-tok-system", "lbl-tok-profile", "lbl-tok-instincts", "lbl-tok-memories", "lbl-tok-projects", "lbl-tok-chat"]:
            self.assertIn(f'id="{lbl_id}"', self.html)

    def test_context_budget_styling(self):
        self.assertIn(".context-budget-container", self.css)
        self.assertIn(".budget-multi-track", self.css)
        self.assertIn(".budget-seg.seg-system", self.css)
        self.assertIn(".budget-seg.seg-profile", self.css)
        self.assertIn(".budget-seg.seg-instincts", self.css)
        self.assertIn(".budget-seg.seg-memories", self.css)
        self.assertIn(".budget-seg.seg-projects", self.css)
        self.assertIn(".budget-seg.seg-chat", self.css)

    def test_context_budget_js_ingestion(self):
        self.assertIn("data.context_budget", self.bridge_js)
        self.assertIn("this.state.system.contextBudget", self.hud_js)


class TestMilestone4AtomicInstinctsHUD(unittest.TestCase):
    """F27: Atomic Instincts v2.1 HUD Column."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            self.css = f.read()
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            self.hud_js = f.read()
        with open(os.path.join(REPO_ROOT, "js", "system-bridge.js"), "r", encoding="utf-8") as f:
            self.bridge_js = f.read()

    def test_instincts_column_markup(self):
        self.assertIn('id="mem-col1-subnav"', self.html)
        self.assertIn('id="btn-subtab-instincts"', self.html)
        self.assertIn('id="btn-subtab-skills"', self.html)
        self.assertIn('id="count-instincts"', self.html)
        self.assertIn('id="count-skills"', self.html)
        self.assertIn('id="mem-instinct-add-row"', self.html)
        self.assertIn('id="mem-new-instinct-trigger"', self.html)
        self.assertIn('id="mem-new-instinct-action"', self.html)
        self.assertIn('id="mem-new-instinct-scope"', self.html)
        self.assertIn('id="btn-add-instinct"', self.html)
        self.assertIn('id="mem-instincts-list"', self.html)

    def test_instincts_styling(self):
        self.assertIn(".instinct-card", self.css)
        self.assertIn(".badge-scope", self.css)
        self.assertIn(".badge-scope.scope-global", self.css)
        self.assertIn(".badge-scope.scope-project", self.css)
        self.assertIn(".instinct-conf-track", self.css)
        self.assertIn(".instinct-conf-bar", self.css)

    def test_instincts_actions_in_js(self):
        self.assertIn("btn-instinct-confirm", self.hud_js)
        self.assertIn("btn-instinct-promote", self.hud_js)
        self.assertIn("btn-instinct-prune", self.hud_js)
        self.assertIn("btn-instinct-delete", self.hud_js)
        self.assertIn("async instinctsAction(payload)", self.bridge_js)
        self.assertIn("clean === '/prune'", self.hud_js)


class TestMilestone4SmartBanners(unittest.TestCase):
    """F28: Smart Banners (Task Suggestion & Session Checkpoint)."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            self.css = f.read()
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            self.hud_js = f.read()
        with open(os.path.join(REPO_ROOT, "js", "system-bridge.js"), "r", encoding="utf-8") as f:
            self.bridge_js = f.read()

    def test_smart_banner_markup_and_css(self):
        self.assertIn('id="hud-smart-banner"', self.html)
        self.assertIn(".hud-smart-banner", self.css)
        self.assertIn(".smart-chip", self.css)
        self.assertIn(".smart-chip.chip-suggestion", self.css)
        self.assertIn(".smart-chip.chip-checkpoint", self.css)

    def test_smart_banner_rendering_methods(self):
        self.assertIn("renderSmartBanner()", self.hud_js)
        self.assertIn("this.elSmartBanner", self.hud_js)
        self.assertIn("async sessionAction(payload)", self.bridge_js)
        self.assertIn("clean === '/save-session'", self.hud_js)
        self.assertIn("clean === '/resume-session'", self.hud_js)


class TestBackendContextModeEndpoints(unittest.TestCase):
    """Tests POST/GET /api/context_mode endpoint logic in bridge/ai_monitor.py."""

    def test_backend_context_mode_contract(self):
        monitor_path = os.path.join(REPO_ROOT, "bridge", "ai_monitor.py")
        with open(monitor_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Check endpoints and snapshot keys exist in source
        self.assertTrue('self.path.startswith("/api/context_mode")' in code or 'self.path == "/api/context_mode"' in code)
        self.assertIn('"active_context_mode"', code)
        self.assertIn('"context_mode"', code)


if __name__ == "__main__":
    unittest.main()
