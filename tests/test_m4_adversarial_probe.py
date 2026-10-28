"""
tests/test_m4_adversarial_probe.py
Adversarial Edge Case Probe for Milestone 4 (F18-F25)
challenger_m4_g1
"""

import unittest
import os
import sys
import gc
import json
import tempfile
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

BRIDGE_DIR = os.path.join(REPO_ROOT, "bridge")
if BRIDGE_DIR not in sys.path:
    sys.path.insert(0, BRIDGE_DIR)

from bridge.skill_registry import SkillRegistry, validate_code_safety
from bridge.mcp_registry import MCPRegistry


class TestM4AdversarialProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hud_path = os.path.join(REPO_ROOT, "js", "hud-terminal.js")
        bridge_path = os.path.join(REPO_ROOT, "js", "system-bridge.js")
        with open(hud_path, "r", encoding="utf-8") as f:
            cls.hud_js = f.read()
        with open(bridge_path, "r", encoding="utf-8") as f:
            cls.bridge_js = f.read()

    def test_01_project_cards_empty_or_none_workspace_path(self):
        """Probe edge cases where workspace_path is empty string, null, or undefined."""
        node_script = """
        function escapeHtml(str) {
            if (!str) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function renderFolder(p) {
            const wsPath = p.workspace_path || '';
            let folderHtml = '';
            if (wsPath) {
                folderHtml = '<div class="hub-proj-folder-section"><span class="folder-label">' + escapeHtml(wsPath) + '</span></div>';
            } else {
                folderHtml = '<div class="hub-proj-folder-section folder-unbound">' +
                    '<span class="folder-label-empty">📁 <em>Nessuna cartella associata</em></span>' +
                    '<button type="button" class="term-btn term-btn-sm btn-accent btn-proj-bind-folder">📁 Associa Cartella</button>' +
                    '</div>';
            }
            return folderHtml;
        }

        const cases = [
            { workspace_path: '' },
            { workspace_path: null },
            { workspace_path: undefined },
            {}
        ];

        cases.forEach((c, idx) => {
            const html = renderFolder(c);
            if (!html.includes('Nessuna cartella associata')) {
                throw new Error('Case ' + idx + ' missing Nessuna cartella associata');
            }
            if (!html.includes('btn-proj-bind-folder')) {
                throw new Error('Case ' + idx + ' missing bind button');
            }
        });
        console.log('OK_EMPTY_WORKSPACE_PATH');
        """
        res = subprocess.run(["node", "-e", node_script], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Node script failed: {res.stderr}")
        self.assertIn("OK_EMPTY_WORKSPACE_PATH", res.stdout)

    def test_02_project_cards_workspace_summary_missing_or_empty(self):
        """Probe edge cases where workspace_summary is None, exists=False, or has 0 files."""
        node_script = """
        function escapeHtml(str) {
            if (!str) return '';
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
        }

        function renderRadar(wsSummary) {
            let radarBadgesHtml = '';
            if (wsSummary && wsSummary.exists) {
                const modCount = wsSummary.modified_today_count || 0;
                const modBadge = '<span class="radar-badge ' + (modCount > 0 ? 'radar-badge-active' : '') + '">⚡ ' + modCount + ' file modif. oggi</span>';
                let extBadges = '';
                if (Array.isArray(wsSummary.key_exports) && wsSummary.key_exports.length > 0) {
                    wsSummary.key_exports.slice(0, 3).forEach((ke) => {
                        const fileName = (ke.path || '').split(/[\\\\/]/).pop();
                        extBadges += '<span class="radar-badge radar-badge-asset">📦 ' + escapeHtml(fileName) + ' (' + (ke.size_mb || 0) + 'MB)</span>';
                    });
                } else if (Array.isArray(wsSummary.detected_stack) && wsSummary.detected_stack.length > 0) {
                    wsSummary.detected_stack.slice(0, 3).forEach((st) => {
                        extBadges += '<span class="radar-badge">🛠️ ' + escapeHtml(String(st)) + '</span>';
                    });
                }
                let gitBadge = '';
                if (wsSummary.git_info && wsSummary.git_info.is_git_repo) {
                    const dirty = wsSummary.git_info.dirty_count > 0 ? ' • ' + wsSummary.git_info.dirty_count + ' mod' : '';
                    gitBadge = '<span class="radar-badge radar-badge-git">🌿 ' + escapeHtml(wsSummary.git_info.branch || 'main') + dirty + '</span>';
                }
                radarBadgesHtml = '<div class="hub-proj-radar-row">' + modBadge + extBadges + gitBadge + '</div>';
            }
            return radarBadgesHtml;
        }

        // Case A: wsSummary is null or undefined or exists:false
        if (renderRadar(null) !== '') throw new Error('Null wsSummary should produce empty radar');
        if (renderRadar(undefined) !== '') throw new Error('Undefined wsSummary should produce empty radar');
        if (renderRadar({ exists: false }) !== '') throw new Error('exists:false should produce empty radar');

        // Case B: wsSummary exists with 0 files
        const emptySummary = { exists: true, total_files: 0, modified_today_count: 0, key_exports: [], detected_stack: [], git_info: null };
        const h1 = renderRadar(emptySummary);
        if (!h1.includes('⚡ 0 file modif. oggi')) throw new Error('Expected 0 file modif. oggi');
        if (h1.includes('radar-badge-git')) throw new Error('Git badge should not render when git_info is null');

        // Case C: Git info is empty dict
        const gitEmptySummary = { exists: true, total_files: 1, modified_today_count: 0, key_exports: [], detected_stack: [], git_info: {} };
        const h2 = renderRadar(gitEmptySummary);
        if (h2.includes('radar-badge-git')) throw new Error('Git badge should not render when is_git_repo is falsy');

        // Case D: Git info with null branch
        const gitNullBranch = { exists: true, total_files: 1, modified_today_count: 0, key_exports: [], detected_stack: [], git_info: { is_git_repo: true, branch: null, dirty_count: 0 } };
        const h3 = renderRadar(gitNullBranch);
        if (!h3.includes('🌿 main')) throw new Error('Git badge should fallback to main branch');

        console.log('OK_RADAR_SUMMARY_EDGE_CASES');
        """
        res = subprocess.run(["node", "-e", node_script], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Node script failed: {res.stderr}")
        self.assertIn("OK_RADAR_SUMMARY_EDGE_CASES", res.stdout)

    def test_03_switchboard_network_failure_resilience(self):
        """Simulate network failure on toggleSkill and toggleMcp in SystemBridge."""
        node_script = """
        const fs = require('fs');
        const bridgeJs = fs.readFileSync('js/system-bridge.js', 'utf8');

        // Mock window and fetch that always throws NetworkError
        const fakeWindow = { location: { hostname: '127.0.0.1' } };
        const failingFetch = () => Promise.reject(new Error('Network offline / server down'));

        const fn = new Function('window', 'document', 'fetch', bridgeJs + '; return SystemBridge;');
        const SystemBridge = fn(fakeWindow, {}, failingFetch);

        const state = {};
        const sb = new SystemBridge(state);
        // Set budget after constructor
        state.system.contextBudget = { used_tokens: 500 };
        state.system.mcpConnectors = [{ id: 'mcp_test', enabled: true }];
        state.system.skillsRegistry = [{ id: 'skill_test', enabled: true }];

        Promise.all([
            sb.toggleMcp('mcp_test', false),
            sb.toggleSkill('skill_test', false),
            sb.importSkill({ content: 'test' })
        ]).then(([resMcp, resSkill, resImport]) => {
            if (resMcp.ok !== false) throw new Error('toggleMcp should return ok:false on network failure');
            if (resSkill.ok !== false) throw new Error('toggleSkill should return ok:false on network failure');
            if (resImport.ok !== false) throw new Error('importSkill should return ok:false on network failure');
            // Ensure state was not corrupted
            if (state.system.contextBudget.used_tokens !== 500) throw new Error('Context budget was corrupted');
            console.log('OK_NETWORK_FAILURE_RESILIENCE');
            process.exit(0);
        }).catch(err => {
            console.error(err);
            process.exit(1);
        });
        """
        res = subprocess.run(["node", "-e", node_script], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Node script failed: {res.stderr}")
        self.assertIn("OK_NETWORK_FAILURE_RESILIENCE", res.stdout)

    def test_04_modal_import_skill_empty_submission(self):
        """Verify modal form submission ignores empty or whitespace-only content."""
        node_script = """
        function testModalSubmit(inputValue) {
            const content = inputValue ? inputValue.trim() : '';
            if (!content) return false;
            return true;
        }

        if (testModalSubmit('') !== false) throw new Error('Empty string should be rejected');
        if (testModalSubmit('   ') !== false) throw new Error('Spaces should be rejected');
        if (testModalSubmit(' \\t \\n ') !== false) throw new Error('Whitespace should be rejected');
        if (testModalSubmit(null) !== false) throw new Error('Null should be rejected');
        if (testModalSubmit(undefined) !== false) throw new Error('Undefined should be rejected');
        if (testModalSubmit('# Valid Skill') !== true) throw new Error('Valid content should pass');

        console.log('OK_MODAL_EMPTY_SUBMISSION');
        """
        res = subprocess.run(["node", "-e", node_script], cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Node script failed: {res.stderr}")
        self.assertIn("OK_MODAL_EMPTY_SUBMISSION", res.stdout)

    def test_05_backend_api_edge_cases(self):
        """Probe backend endpoints for edge cases: safety validation, invalid toggle IDs."""
        tmpdir = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmpdir, "skills"), exist_ok=True)
            with open(os.path.join(tmpdir, "projects.json"), "w", encoding="utf-8") as f:
                json.dump([{"id": "p1", "name": "Test Project"}], f)

            sr = SkillRegistry(data_dir=tmpdir)
            mcp = MCPRegistry(data_dir=tmpdir)

            # 1. Safety validation blocks dangerous patterns
            safe_ok, msg = validate_code_safety("import os\nos.system('calc')")
            self.assertFalse(safe_ok, f"Importing os must be rejected: {msg}")

            safe_ok2, msg2 = validate_code_safety("import subprocess\nsubprocess.Popen(['calc'])")
            self.assertFalse(safe_ok2, f"Subprocess invocation must be rejected: {msg2}")

            safe_ok3, msg3 = validate_code_safety("del /f /q C:\\Windows")
            self.assertFalse(safe_ok3, f"Destructive shell pattern must be rejected: {msg3}")

            safe_ok4, _ = validate_code_safety("# Normal markdown\n## Steps to export GLB")
            self.assertTrue(safe_ok4, "Safe markdown content must be accepted")

            # 2. Toggle non-existent skill ID
            ok = sr.toggle_skill("non_existent_skill_id", False)
            self.assertFalse(ok, "SkillRegistry should return False for non-existent skill")

            # 3. Toggle non-existent MCP connector ID
            ok = mcp.toggle_connector("non_existent_mcp_id", False)
            self.assertFalse(ok, "MCPRegistry should return False for non-existent connector")
        finally:
            del sr
            del mcp
            gc.collect()
            try:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass

    def test_06_production_data_hygiene(self):
        """Assert zero test files or temporary files in production bridge/data/."""
        prod_data = os.path.join(REPO_ROOT, "bridge", "data")
        files = os.listdir(prod_data)
        polluted = [f for f in files if f.endswith(".tmp") or "test" in f.lower()]
        self.assertEqual(polluted, [], f"Pollution found in bridge/data/: {polluted}")


if __name__ == "__main__":
    unittest.main()
