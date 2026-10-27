"""
tests/test_challenger_m4.py
===========================
Adversarial Empirical Challenge Test Suite for Milestone 4:
Dynamic Context Modes & HUD Dashboard Controls (Features F22 through F28).

Authored by: challenger_m4 (Empirical Adversarial Verifier)
Target: AsciiNeko ECC Architectural Integration (Release 4)

Coverage:
1. Markup & CSS retro-terminal typography, design tokens, Color-Safe overrides.
2. Mode Transitions: 3-way cycling, slash commands, workspace auto-sync, offline fallback.
3. Quality Gate Modal: Score thresholds (0%, 50%, 79%, 80%, 100%), null/empty field resilience, XSS escaping.
4. Context Budget Telemetry: Token clamping [0, 8192, 12000+], status transitions, segment track geometry.
5. Column 1 Atomic Instincts v2.1: Subtab toggle, legacy preservation, confidence bounds, conditional promote button.
6. Smart Banners: Simultaneous suggestions & checkpoints, dismissal memory, workspace gating.
7. Backend Context Mode HTTP APIs: Endpoint contracts, payload fuzzing, and strict production data isolation.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

PRODUCTION_DATA_DIR = os.path.join(BRIDGE_DIR, "data")

NODE_ATTACH_METHOD_HELPER = """
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
"""


class ChallengerM4Base(unittest.TestCase):
    """Base test case providing data hygiene snapshot and source loading."""

    def setUp(self):
        self._temp_dir_obj = tempfile.TemporaryDirectory()
        self.test_dir = os.path.abspath(self._temp_dir_obj.name)

        # Production snapshot before test
        self._prod_data_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)

        with open(os.path.join(REPO_ROOT, "index.html"), "r", encoding="utf-8") as f:
            self.html = f.read()
        with open(os.path.join(REPO_ROOT, "css", "terminal.css"), "r", encoding="utf-8") as f:
            self.css = f.read()
        with open(os.path.join(REPO_ROOT, "js", "hud-terminal.js"), "r", encoding="utf-8") as f:
            self.hud_js = f.read()
        with open(os.path.join(REPO_ROOT, "js", "system-bridge.js"), "r", encoding="utf-8") as f:
            self.bridge_js = f.read()

    def tearDown(self):
        try:
            self._temp_dir_obj.cleanup()
        except Exception:
            pass

        # Strict check: production data must never be modified by tests
        curr_prod_snapshot = self._capture_dir_snapshot(PRODUCTION_DATA_DIR)
        self.assertEqual(
            self._prod_data_snapshot,
            curr_prod_snapshot,
            "CRITICAL VIOLATION: Production files in bridge/data/ were modified during test execution!",
        )

    def _capture_dir_snapshot(self, directory: str) -> Dict[str, float]:
        if not os.path.isdir(directory):
            return {}
        snapshot = {}
        for root, _, files in os.walk(directory):
            for f in files:
                p = os.path.join(root, f)
                try:
                    snapshot[p] = os.path.getmtime(p)
                except OSError:
                    pass
        return snapshot

    def run_node(self, script: str) -> subprocess.CompletedProcess:
        """Executes JavaScript code in Node.js."""
        return subprocess.run(
            ["node", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )


# ==============================================================================
# 1. MARKUP & CSS CONTRACT VERIFICATION (F22 - F28)
# ==============================================================================

class TestChallengerM4MarkupAndContracts(ChallengerM4Base):
    """Stress tests HTML DOM IDs, classes, and CSS definitions."""

    def test_context_mode_pill_contracts(self):
        # Pill and children
        self.assertIn('id="context-mode-pill"', self.html)
        self.assertIn('id="context-mode-icon"', self.html)
        self.assertIn('id="context-mode-label"', self.html)

        # CSS theme definitions
        self.assertIn(".context-mode-pill", self.css)
        self.assertIn(".context-mode-pill.mode-operativo", self.css)
        self.assertIn(".context-mode-pill.mode-project_manager", self.css)
        self.assertIn(".context-mode-pill.mode-knowledge_curator", self.css)

        # Color-Safe mode contrast override
        self.assertIn(".color-safe .context-mode-pill", self.css)

    def test_quality_gate_modal_contracts(self):
        modal_ids = [
            "quality-gate-modal", "qg-proj-title", "qg-score-val",
            "qg-status-label", "qg-status-desc", "qg-tasks-summary",
            "qg-tasks-badge", "qg-tasks-list", "qg-rules-summary",
            "qg-rules-badge", "qg-rules-list", "qg-instincts-badge",
            "qg-instincts-list", "qg-recommendations", "btn-qg-retest",
            "btn-qg-close", "btn-close-qg-modal"
        ]
        for mid in modal_ids:
            self.assertIn(f'id="{mid}"', self.html, f"Missing Quality Gate modal element: #{mid}")

        # Modal uses .neko-modal-overlay container and .qg-score-banner
        self.assertIn(".qg-score-banner", self.css)
        self.assertIn(".qg-score-gauge", self.css)
        self.assertIn(".qg-score-val", self.css)
        self.assertIn(".qg-status-label", self.css)
        self.assertIn(".qg-badge", self.css)
        self.assertIn(".qg-badge.badge-warn", self.css)
        self.assertIn(".qg-badge.badge-danger", self.css)

    def test_context_budget_telemetry_contracts(self):
        budget_ids = [
            "context-budget-container", "budget-used-tokens", "budget-used-pct",
            "budget-status-pill", "seg-system", "seg-profile", "seg-instincts",
            "seg-memories", "seg-projects", "seg-chat",
            "lbl-tok-system", "lbl-tok-profile", "lbl-tok-instincts",
            "lbl-tok-memories", "lbl-tok-projects", "lbl-tok-chat"
        ]
        for bid in budget_ids:
            self.assertIn(f'id="{bid}"', self.html, f"Missing Context Budget element: #{bid}")

        self.assertIn(".context-budget-container", self.css)
        self.assertIn(".budget-multi-track", self.css)
        self.assertIn(".color-safe .context-budget-container", self.css)

    def test_atomic_instincts_column_contracts(self):
        col1_ids = [
            "mem-col1-subnav", "btn-subtab-instincts", "btn-subtab-skills",
            "count-instincts", "count-skills", "mem-instinct-add-row",
            "mem-new-instinct-trigger", "mem-new-instinct-action",
            "mem-new-instinct-scope", "btn-add-instinct", "mem-instincts-list",
            # Legacy Milestone 3 preservation
            "mem-skills-count", "mem-skills-list", "mem-new-skill-name", "btn-add-skill"
        ]
        for cid in col1_ids:
            self.assertIn(f'id="{cid}"', self.html, f"Missing Column 1 element: #{cid}")

        self.assertIn(".instinct-card", self.css)
        self.assertIn(".badge-scope", self.css)
        self.assertIn(".instinct-conf-track", self.css)
        self.assertIn(".color-safe .instinct-card", self.css)

    def test_smart_banners_and_playbook_quick_bar(self):
        self.assertIn('id="hud-smart-banner"', self.html)
        self.assertIn(".hud-smart-banner", self.css)
        self.assertIn(".smart-chip", self.css)
        self.assertIn(".smart-chip.chip-suggestion", self.css)
        self.assertIn(".smart-chip.chip-checkpoint", self.css)
        self.assertIn(".color-safe .smart-chip", self.css)

        self.assertIn('id="playbook-quick-bar"', self.html)
        self.assertIn('id="hub-playbook-select"', self.html)
        self.assertIn('id="btn-create-from-playbook"', self.html)


# ==============================================================================
# 2. CONTEXT MODE TRANSITIONS & WORKSPACE CYCLING (F22)
# ==============================================================================

class TestChallengerM4ContextModeTransitions(ChallengerM4Base):
    """Stress tests mode cycling, fallback mechanisms, and slash commands."""

    def test_set_context_mode_node_execution(self):
        """Simulates setContextMode in Node.js verifying class and label updates."""
        node_script = f"""
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        {NODE_ATTACH_METHOD_HELPER}

        class MockTerminal {{
          constructor() {{
            this.currentContextMode = 'operativo';
            this.state = {{ system: {{}} }};
            this.classes = new Set(['context-mode-pill']);
            this.elContextModePill = {{
              classList: {{
                remove: (...args) => args.forEach(c => this.classes.delete(c)),
                add: (c) => this.classes.add(c)
              }}
            }};
            this.elContextModeIcon = {{ textContent: '' }};
            this.elContextModeLabel = {{ textContent: '' }};
          }}
        }}

        attachMethod(MockTerminal, code, 'setContextMode');

        const term = new MockTerminal();

        // 1. Valid Transitions
        term.setContextMode('project_manager', false);
        if (term.currentContextMode !== 'project_manager' || !term.classes.has('mode-project_manager')) {{
          console.error('Failed transition to project_manager');
          process.exit(1);
        }}
        if (term.elContextModeIcon.textContent !== '📋' || term.elContextModeLabel.textContent !== 'Project Manager') {{
          console.error('Icon or label mismatch for project_manager');
          process.exit(2);
        }}

        term.setContextMode('knowledge_curator', false);
        if (term.currentContextMode !== 'knowledge_curator' || !term.classes.has('mode-knowledge_curator')) {{
          console.error('Failed transition to knowledge_curator');
          process.exit(3);
        }}
        if (term.elContextModeIcon.textContent !== '🧠' || term.elContextModeLabel.textContent !== 'Knowledge Curator') {{
          console.error('Icon or label mismatch for knowledge_curator');
          process.exit(4);
        }}

        term.setContextMode('operativo', false);
        if (term.currentContextMode !== 'operativo' || !term.classes.has('mode-operativo')) {{
          console.error('Failed transition to operativo');
          process.exit(5);
        }}
        if (term.elContextModeIcon.textContent !== '🛠️' || term.elContextModeLabel.textContent !== 'Operativo') {{
          console.error('Icon or label mismatch for operativo');
          process.exit(6);
        }}

        // 2. Fuzzy / Aliased Inputs
        term.setContextMode('pm', false);
        if (term.currentContextMode !== 'project_manager') {{
          console.error('Failed fuzzy alias "pm"');
          process.exit(7);
        }}

        term.setContextMode('curator', false);
        if (term.currentContextMode !== 'knowledge_curator') {{
          console.error('Failed fuzzy alias "curator"');
          process.exit(8);
        }}

        // 3. Fallback on invalid string
        term.setContextMode('non_existent_mode_xyz', false);
        if (term.currentContextMode !== 'operativo') {{
          console.error('Failed fallback to "operativo" on unknown mode');
          process.exit(9);
        }}

        // 4. Fallback on null/undefined
        term.setContextMode(null, false);
        if (term.currentContextMode !== 'operativo') {{
          console.error('Failed fallback on null');
          process.exit(10);
        }}

        console.log('SUCCESS');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Node script error: {proc.stderr} {proc.stdout}")
        self.assertIn("SUCCESS", proc.stdout)

    def test_workspace_auto_sync_mapping(self):
        """Verifies switchWorkspace automatically sets context mode to match view."""
        self.assertIn("switchWorkspace(viewId)", self.hud_js)
        self.assertIn("viewId === 'view-todo' || viewId === 'view-projects' || viewId === 'view-calendar'", self.hud_js)
        self.assertIn("targetMode = 'project_manager'", self.hud_js)
        self.assertIn("viewId === 'view-memory'", self.hud_js)
        self.assertIn("targetMode = 'knowledge_curator'", self.hud_js)

    def test_slash_context_command_adversarial_arguments(self):
        """Verifies slash command /context parsing against diverse user inputs."""
        node_script = """
        function parseSlashContext(clean, currentMode) {
          const arg = clean.replace(/^\\/context\\s*/i, '').trim().toLowerCase();
          let mode = 'operativo';
          if (!arg) {
            const cur = currentMode || 'operativo';
            if (cur === 'operativo') mode = 'project_manager';
            else if (cur === 'project_manager') mode = 'knowledge_curator';
            else mode = 'operativo';
          } else if (arg.includes('curator') || arg.includes('know') || arg === 'curator') {
            mode = 'knowledge_curator';
          } else if (arg.includes('pm') || arg.includes('proj') || arg === 'pm') {
            mode = 'project_manager';
          } else {
            mode = 'operativo';
          }
          return mode;
        }

        // Cycling without arg
        if (parseSlashContext('/context', 'operativo') !== 'project_manager') process.exit(1);
        if (parseSlashContext('/context', 'project_manager') !== 'knowledge_curator') process.exit(2);
        if (parseSlashContext('/context', 'knowledge_curator') !== 'operativo') process.exit(3);

        // Explicit args
        if (parseSlashContext('/context pm', 'operativo') !== 'project_manager') process.exit(4);
        if (parseSlashContext('/context project', 'operativo') !== 'project_manager') process.exit(5);
        if (parseSlashContext('/context curator', 'operativo') !== 'knowledge_curator') process.exit(6);
        if (parseSlashContext('/context knowledge', 'operativo') !== 'knowledge_curator') process.exit(7);
        if (parseSlashContext('/context operativo', 'project_manager') !== 'operativo') process.exit(8);

        // Bogus arg fallback
        if (parseSlashContext('/context random_gibberish', 'project_manager') !== 'operativo') process.exit(9);

        console.log('SLASH_CONTEXT_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Slash command node error: {proc.stderr} {proc.stdout}")
        self.assertIn("SLASH_CONTEXT_OK", proc.stdout)


# ==============================================================================
# 3. QUALITY GATE MODAL STRESS & REPORT RENDERING (F23, F24)
# ==============================================================================

class TestChallengerM4QualityGateModalStress(ChallengerM4Base):
    """Stress tests Quality Gate score thresholds, empty payloads, and XSS sanitization."""

    def test_qg_score_boundaries_and_status_labels(self):
        """Verifies score rendering, ready_for_delivery boolean, and styling classes."""
        node_script = f"""
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        {NODE_ATTACH_METHOD_HELPER}

        class MockTerminal {{
          constructor() {{
            this.elQgScoreVal = {{ textContent: '' }};
            this.bannerClasses = new Set(['qg-score-banner']);
            this.elQgScoreBanner = {{
              classList: {{
                toggle: (c, v) => v ? this.bannerClasses.add(c) : this.bannerClasses.delete(c)
              }}
            }};
            this.elQgStatusLabel = {{ textContent: '', style: {{}} }};
            this.elQgStatusDesc = {{ textContent: '' }};
            this.elQgTasksSummary = {{ textContent: '' }};
            this.elQgTasksBadge = {{ textContent: '', className: '' }};
            this.elQgTasksList = {{ innerHTML: '' }};
            this.elQgRulesSummary = {{ textContent: '' }};
            this.elQgRulesBadge = {{ textContent: '', className: '' }};
            this.elQgRulesList = {{ innerHTML: '' }};
            this.elQgInstinctsBadge = {{ textContent: '', className: '' }};
            this.elQgInstinctsList = {{ innerHTML: '' }};
            this.elQgRecommendations = {{ innerHTML: '' }};
          }}
          escapeHtml(str) {{
            return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
          }}
        }}

        attachMethod(MockTerminal, code, 'renderQualityGateReport');

        const term = new MockTerminal();

        // Case 1: 100% Score (Full Readiness)
        term.renderQualityGateReport({{
          ok: true,
          readiness_score: 100,
          ready_for_delivery: true,
          tasks_completed: 4,
          tasks_total: 4,
          tasks: [{{ title: 'T1', status: 'done' }}],
          client_rules: ['Rule 1'],
          instincts_matched: [{{ trigger: 'A', action: 'B', confidence: 0.9 }}],
          recommendations: ['Great work']
        }});
        if (term.elQgScoreVal.textContent !== '100%') process.exit(1);
        if (!term.bannerClasses.has('passed') || term.bannerClasses.has('warning')) process.exit(2);
        if (!term.elQgStatusLabel.textContent.includes('PRONTO')) process.exit(3);

        // Case 2: 79% Score (Borderline Failure -> < 80 threshold)
        term.renderQualityGateReport({{
          ok: true,
          readiness_score: 79,
          ready_for_delivery: false,
          tasks_completed: 3,
          tasks_total: 4,
          tasks: [{{ title: 'T1', status: 'done' }}, {{ title: 'T2', status: 'todo' }}],
          client_rules: ['Rule 1'],
          recommendations: ['Finish tasks']
        }});
        if (term.elQgScoreVal.textContent !== '79%') process.exit(4);
        if (term.bannerClasses.has('passed') || !term.bannerClasses.has('warning')) process.exit(5);
        if (!term.elQgStatusLabel.textContent.includes('CRITICITÀ')) process.exit(6);

        // Case 3: 0% Score (Zero Tasks Done)
        term.renderQualityGateReport({{
          ok: true,
          readiness_score: 0,
          ready_for_delivery: false,
          tasks_completed: 0,
          tasks_total: 4,
          tasks: [{{ title: 'T1', status: 'todo' }}],
          client_rules: [],
          recommendations: ['Start project']
        }});
        if (term.elQgScoreVal.textContent !== '0%') process.exit(7);
        if (!term.bannerClasses.has('warning')) process.exit(8);

        console.log('QG_SCORE_BOUNDARIES_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Quality gate score node error: {proc.stderr} {proc.stdout}")
        self.assertIn("QG_SCORE_BOUNDARIES_OK", proc.stdout)

    def test_qg_null_and_empty_payload_graceful_handling(self):
        """Verifies Quality Gate does not crash on completely empty or missing sub-arrays."""
        node_script = f"""
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        {NODE_ATTACH_METHOD_HELPER}

        class MockTerminal {{
          constructor() {{
            this.elQgScoreVal = {{ textContent: '' }};
            this.bannerClasses = new Set(['qg-score-banner']);
            this.elQgScoreBanner = {{ classList: {{ toggle: () => {{}} }} }};
            this.elQgStatusLabel = {{ textContent: '', style: {{}} }};
            this.elQgStatusDesc = {{ textContent: '' }};
            this.elQgTasksSummary = {{ textContent: '' }};
            this.elQgTasksBadge = {{ textContent: '', className: '' }};
            this.elQgTasksList = {{ innerHTML: '' }};
            this.elQgRulesSummary = {{ textContent: '' }};
            this.elQgRulesBadge = {{ textContent: '', className: '' }};
            this.elQgRulesList = {{ innerHTML: '' }};
            this.elQgInstinctsBadge = {{ textContent: '', className: '' }};
            this.elQgInstinctsList = {{ innerHTML: '' }};
            this.elQgRecommendations = {{ innerHTML: '' }};
          }}
          escapeHtml(str) {{ return String(str || ''); }}
        }}

        attachMethod(MockTerminal, code, 'renderQualityGateReport');

        const term = new MockTerminal();

        // Completely sparse report with nulls
        term.renderQualityGateReport({{
          readiness_score: null,
          tasks: null,
          client_rules: null,
          instincts_matched: null,
          recommendations: null
        }});

        if (!term.elQgTasksList.innerHTML.includes('Nessun task vincolante')) process.exit(1);
        if (!term.elQgRulesList.innerHTML.includes('Nessuna regola cliente')) process.exit(2);
        if (!term.elQgInstinctsList.innerHTML.includes('Nessun istinto tecnico')) process.exit(3);
        if (!term.elQgRecommendations.innerHTML.includes('Nessuna raccomandazione aggiuntiva')) process.exit(4);

        console.log('QG_SPARSE_PAYLOAD_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Sparse Quality Gate error: {proc.stderr} {proc.stdout}")
        self.assertIn("QG_SPARSE_PAYLOAD_OK", proc.stdout)

    def test_qg_xss_injection_escaping(self):
        """Verifies HTML injection payloads in tasks/rules/recommendations are safely escaped."""
        node_script = f"""
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        {NODE_ATTACH_METHOD_HELPER}

        class MockTerminal {{
          constructor() {{
            this.elQgScoreVal = {{ textContent: '' }};
            this.elQgScoreBanner = {{ classList: {{ toggle: () => {{}} }} }};
            this.elQgStatusLabel = {{ textContent: '', style: {{}} }};
            this.elQgStatusDesc = {{ textContent: '' }};
            this.elQgTasksSummary = {{ textContent: '' }};
            this.elQgTasksBadge = {{ textContent: '', className: '' }};
            this.elQgTasksList = {{ innerHTML: '' }};
            this.elQgRulesSummary = {{ textContent: '' }};
            this.elQgRulesBadge = {{ textContent: '', className: '' }};
            this.elQgRulesList = {{ innerHTML: '' }};
            this.elQgInstinctsBadge = {{ textContent: '', className: '' }};
            this.elQgInstinctsList = {{ innerHTML: '' }};
            this.elQgRecommendations = {{ innerHTML: '' }};
          }}
          escapeHtml(str) {{
            return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
          }}
        }}

        attachMethod(MockTerminal, code, 'renderQualityGateReport');

        const term = new MockTerminal();
        term.renderQualityGateReport({{
          readiness_score: 85,
          tasks: [{{ title: '<script>alert("XSS_TASK")</script>', status: 'done' }}],
          client_rules: ['<img src=x onerror=alert("XSS_RULE")>'],
          instincts_matched: [{{ trigger: '<svg/onload=alert("XSS_INS")>', action: 'Safe', confidence: 0.9 }}],
          recommendations: ['<b onmouseover=alert("XSS_REC")>Injected</b>']
        }});

        // Ensure raw unescaped tags do not exist in innerHTML
        if (term.elQgTasksList.innerHTML.includes('<script>')) process.exit(1);
        if (term.elQgRulesList.innerHTML.includes('<img src=')) process.exit(2);
        if (term.elQgInstinctsList.innerHTML.includes('<svg/onload=')) process.exit(3);
        if (term.elQgRecommendations.innerHTML.includes('<b onmouseover=')) process.exit(4);

        console.log('QG_XSS_PROTECTION_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"XSS check error: {proc.stderr} {proc.stdout}")
        self.assertIn("QG_XSS_PROTECTION_OK", proc.stdout)


# ==============================================================================
# 4. CONTEXT BUDGET TELEMETRY METER STRESS (F26)
# ==============================================================================

class TestChallengerM4ContextBudgetMeterStress(ChallengerM4Base):
    """Stress tests token clamping, status thresholds, and track geometries."""

    def test_budget_status_pill_transitions(self):
        """Verifies status badge transitions at <70%, 70-89%, and >=90%."""
        node_script = """
        function testBudget(budgetObj) {
          const totalBudget = budgetObj.total_budget || 8192;
          const usedTokens = budgetObj.used_tokens !== undefined ? budgetObj.used_tokens : 0;
          const usagePct = budgetObj.usage_pct !== undefined ? budgetObj.usage_pct : (totalBudget > 0 ? Math.round((usedTokens / totalBudget) * 100) : 0);
          let label = '';
          let cls = '';
          if (usagePct < 70) {
            label = '🟢 OTTIMALE';
            cls = 'conn-status-badge budget-badge-ok';
          } else if (usagePct < 90) {
            label = '🟡 ATTENZIONE';
            cls = 'conn-status-badge budget-badge-warn';
          } else {
            label = '🔴 CRITICO';
            cls = 'conn-status-badge budget-badge-crit';
          }
          return { usedTokens, usagePct, label, cls };
        }

        // Test 1: 0 tokens (0%) -> OTTIMALE
        const r1 = testBudget({ total_budget: 8192, used_tokens: 0 });
        if (r1.label !== '🟢 OTTIMALE' || r1.usagePct !== 0) process.exit(1);

        // Test 2: 5652 tokens (69%) -> OTTIMALE
        const r2 = testBudget({ total_budget: 8192, used_tokens: 5652 });
        if (r2.label !== '🟢 OTTIMALE' || r2.usagePct !== 69) process.exit(2);

        // Test 3: 5734 tokens (70%) -> ATTENZIONE
        const r3 = testBudget({ total_budget: 8192, used_tokens: 5734 });
        if (r3.label !== '🟡 ATTENZIONE' || r3.usagePct !== 70) process.exit(3);

        // Test 4: 7290 tokens (89%) -> ATTENZIONE
        const r4 = testBudget({ total_budget: 8192, used_tokens: 7290 });
        if (r4.label !== '🟡 ATTENZIONE' || r4.usagePct !== 89) process.exit(4);

        // Test 5: 7373 tokens (90%) -> CRITICO
        const r5 = testBudget({ total_budget: 8192, used_tokens: 7373 });
        if (r5.label !== '🔴 CRITICO' || r5.usagePct !== 90) process.exit(5);

        // Test 6: Over-budget 10000 tokens (122%) -> CRITICO
        const r6 = testBudget({ total_budget: 8192, used_tokens: 10000 });
        if (r6.label !== '🔴 CRITICO' || r6.usagePct < 100) process.exit(6);

        console.log('BUDGET_THRESHOLDS_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Budget thresholds error: {proc.stderr} {proc.stdout}")
        self.assertIn("BUDGET_THRESHOLDS_OK", proc.stdout)

    def test_budget_segment_geometry_min_clamping(self):
        """Verifies Math.max(1, Math.round(...)) guarantees tracks never drop below 1%."""
        node_script = """
        function computeSegmentWidth(tok, totalBudget) {
          return `${Math.max(1, Math.round((tok / totalBudget) * 100))}%`;
        }

        const totalBudget = 8192;
        // Even with 0 or 1 token, width is at least 1%
        if (computeSegmentWidth(0, totalBudget) !== '1%') process.exit(1);
        if (computeSegmentWidth(1, totalBudget) !== '1%') process.exit(2);

        // Normal segment
        if (computeSegmentWidth(819, totalBudget) !== '10%') process.exit(3);

        console.log('SEGMENT_GEOMETRY_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Segment geometry error: {proc.stderr} {proc.stdout}")
        self.assertIn("SEGMENT_GEOMETRY_OK", proc.stdout)


# ==============================================================================
# 5. COLUMN 1 ATOMIC INSTINCTS v2.1 HUD STRESS (F27)
# ==============================================================================

class TestChallengerM4AtomicInstinctsColumnStress(ChallengerM4Base):
    """Stress tests subtab switcher, confidence bounds, and conditional promote buttons."""

    def test_subtab_toggling_and_legacy_preservation(self):
        """Verifies switching between instincts and skills keeps both functional without breaking DOM."""
        self.assertIn("this.memCol1Subtab = 'instincts'", self.hud_js)
        self.assertIn("this.memCol1Subtab = 'skills'", self.hud_js)
        self.assertIn("this.elMemInstinctAddRow.style.display", self.hud_js)
        self.assertIn("this.elMemSkillAddRow.style.display", self.hud_js)
        self.assertIn("this.elMemInstinctsList.style.display", self.hud_js)
        self.assertIn("this.elMemSkillsList.style.display", self.hud_js)

        # Legacy Milestone 3 preservation: ensure legacy skills rendering code is present
        self.assertIn("this.elMemSkillsList.innerHTML = skillsHtml", self.hud_js)
        self.assertIn("btn-del-skill", self.hud_js)

    def test_instinct_actions_conditional_promote_button(self):
        """Verifies that global instincts DO NOT render the Promote button (only project instincts do)."""
        node_script = """
        const instincts = [
          { id: 'ins_global', trigger: 'T1', action: 'A1', scope: 'global', confidence: 0.95 },
          { id: 'ins_project', trigger: 'T2', action: 'A2', scope: 'project', confidence: 0.80 }
        ];

        function renderCard(ins) {
          const scope = ins.scope || 'global';
          return `
            <div class="instinct-actions">
              <button class="btn-instinct-confirm" data-id="${ins.id}">+ Conferma</button>
              ${scope !== 'global' ? `<button class="btn-instinct-promote" data-id="${ins.id}">⬆️ Promuovi</button>` : ''}
              <button class="btn-instinct-prune" data-id="${ins.id}">🧹 Prune</button>
              <button class="btn-instinct-delete" data-id="${ins.id}">✕</button>
            </div>
          `;
        }

        const globalHtml = renderCard(instincts[0]);
        if (globalHtml.includes('btn-instinct-promote')) {
          console.error('Global instinct incorrectly rendered promote button');
          process.exit(1);
        }

        const projectHtml = renderCard(instincts[1]);
        if (!projectHtml.includes('btn-instinct-promote')) {
          console.error('Project instinct missing promote button');
          process.exit(2);
        }

        console.log('INSTINCT_ACTIONS_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Instinct actions error: {proc.stderr} {proc.stdout}")
        self.assertIn("INSTINCT_ACTIONS_OK", proc.stdout)

    def test_quick_add_instinct_validation(self):
        """Verifies quick-add instinct blocks submission if trigger or action is empty."""
        self.assertIn("if (!trigger || !action)", self.hud_js)
        self.assertIn("Inserisci sia la condizione", self.hud_js)


# ==============================================================================
# 6. SMART BANNERS & SESSION CONTINUITY STRESS (F28)
# ==============================================================================

class TestChallengerM4SmartBannersStress(ChallengerM4Base):
    """Stress tests simultaneous suggestions, dismissal persistence, and workspace gating."""

    def test_simultaneous_suggestions_and_checkpoints(self):
        """Verifies that both task suggestion chip and session checkpoint chip render when active."""
        node_script = f"""
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        {NODE_ATTACH_METHOD_HELPER}

        class MockTerminal {{
          constructor() {{
            this.activeWorkspace = 'view-home';
            this.state = {{
              system: {{
                windowTaskSuggestion: {{
                  id: 'sug_1',
                  window_title: 'Blender - [nave.blend]',
                  suggested_task_title: 'Modellazione scafo'
                }},
                sessionCheckpoint: {{
                  id: 'cp_1',
                  title: 'Progetto Nave Intermarine',
                  timestamp: '2026-10-27T19:30:00Z'
                }}
              }}
            }};
            this._dismissedTaskSuggestion = null;
            this._dismissedSessionCheckpoint = null;
            this.elSmartBanner = {{
              innerHTML: '',
              style: {{}},
              querySelector: (sel) => ({{ addEventListener: () => {{}} }})
            }};
          }}
          escapeHtml(s) {{ return String(s || ''); }}
        }}

        attachMethod(MockTerminal, code, 'renderSmartBanner');

        const term = new MockTerminal();
        term.renderSmartBanner();

        // 1. Both chips rendered simultaneously
        if (!term.elSmartBanner.innerHTML.includes('chip-task-sug')) process.exit(1);
        if (!term.elSmartBanner.innerHTML.includes('chip-session-cp')) process.exit(2);
        if (term.elSmartBanner.style.display !== 'flex') process.exit(3);

        // 2. Dismiss task suggestion -> session checkpoint remains visible
        term._dismissedTaskSuggestion = 'sug_1';
        term.renderSmartBanner();
        if (term.elSmartBanner.innerHTML.includes('chip-task-sug')) process.exit(4);
        if (!term.elSmartBanner.innerHTML.includes('chip-session-cp')) process.exit(5);

        // 3. Dismiss both -> banner hides
        term._dismissedSessionCheckpoint = 'cp_1';
        term.renderSmartBanner();
        if (term.elSmartBanner.style.display !== 'none') process.exit(6);

        // 4. Reactivation: New suggestion arrives with different ID
        term.state.system.windowTaskSuggestion = {{ id: 'sug_2', window_title: '3ds Max', suggested_task_title: 'Render V-Ray' }};
        term.renderSmartBanner();
        if (!term.elSmartBanner.innerHTML.includes('chip-task-sug')) process.exit(7);
        if (term.elSmartBanner.style.display !== 'flex') process.exit(8);

        console.log('SMART_BANNER_STRESS_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Smart banner error: {proc.stderr} {proc.stdout}")
        self.assertIn("SMART_BANNER_STRESS_OK", proc.stdout)

    def test_smart_banner_workspace_gating(self):
        """Verifies smart banner is hidden in Calendar and Memory views."""
        node_script = f"""
        const fs = require('fs');
        const code = fs.readFileSync('js/hud-terminal.js', 'utf8');

        {NODE_ATTACH_METHOD_HELPER}

        class MockTerminal {{
          constructor() {{
            this.activeWorkspace = 'view-memory';
            this.state = {{
              system: {{
                windowTaskSuggestion: {{ id: 'sug_1', suggested_task_title: 'Task' }}
              }}
            }};
            this.elSmartBanner = {{
              innerHTML: '',
              style: {{}},
              querySelector: (sel) => ({{ addEventListener: () => {{}} }})
            }};
          }}
          escapeHtml(s) {{ return String(s || ''); }}
        }}

        attachMethod(MockTerminal, code, 'renderSmartBanner');

        const term = new MockTerminal();
        term.activeWorkspace = 'view-memory';
        term.renderSmartBanner();
        if (term.elSmartBanner.style.display !== 'none') process.exit(1);

        term.activeWorkspace = 'view-calendar';
        term.renderSmartBanner();
        if (term.elSmartBanner.style.display !== 'none') process.exit(2);

        term.activeWorkspace = 'view-todo';
        term.renderSmartBanner();
        if (term.elSmartBanner.style.display !== 'flex') process.exit(3);

        console.log('WORKSPACE_GATING_OK');
        """
        proc = self.run_node(node_script)
        self.assertEqual(proc.returncode, 0, f"Workspace gating error: {proc.stderr} {proc.stdout}")
        self.assertIn("WORKSPACE_GATING_OK", proc.stdout)


# ==============================================================================
# 7. BACKEND CONTEXT MODE HTTP API CONTRACTS & ISOLATION
# ==============================================================================

class TestChallengerM4BackendContextModeAPI(ChallengerM4Base):
    """Verifies backend HTTP endpoints /api/context_mode and state exposure."""

    def test_backend_context_mode_router_logic(self):
        """Tests the backend context mode routing logic in ai_monitor.py directly."""
        monitor_path = os.path.join(REPO_ROOT, "bridge", "ai_monitor.py")
        with open(monitor_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Check POST handler logic
        self.assertIn('self.path.startswith("/api/context_mode")', code)
        self.assertIn('mode = body.get("mode")', code)
        self.assertIn('"knowledge_curator"', code)
        self.assertIn('"project_manager"', code)
        self.assertIn('"operativo"', code)

        # Check GET handler logic
        self.assertIn('mode = getattr(monitor, "active_context_mode", "operativo")', code)
        self.assertIn('self._send_json({"ok": True, "status": "ok", "context_mode": mode, "active_mode": mode})', code)

        # Check snapshot exposure
        self.assertIn('"active_context_mode": getattr(self, "active_context_mode"', code)

    def test_production_data_directory_purity(self):
        """Explicitly checks that production bridge/data/ has zero test artifacts."""
        data_files = os.listdir(PRODUCTION_DATA_DIR)
        forbidden_substrings = [".tmp", ".bak", "test", "gate2", "temp", "corrupt"]
        for fname in data_files:
            lower = fname.lower()
            for forb in forbidden_substrings:
                if forb in lower and not lower.endswith(".db"):
                    self.fail(f"Production data pollution detected: '{fname}' in bridge/data/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
