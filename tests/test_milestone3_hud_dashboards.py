"""
tests/test_milestone3_hud_dashboards.py
Comprehensive verification suite for Milestone 3 (Requirement R3):
- Clean 🐾 Neko Home + 4 dedicated Asana/Trello-inspired AI Dashboards
- Top bar 5-tab workspace switcher (🐾 Neko, 📋 To-Do, 📌 Progetti, 📅 Calendario, 🧠 Memoria + ⚙️)
- Subtle next-task chip for Home view
- Full-view workspace container (#hud-workspace-container) and 4 dashboard views
- CSS retro-terminal typography, Kanban, Project Hub, Calendar grid, Memory columns, and color-safe overrides
- JS SystemBridge API actions (todosAction, skillsLearnedAction, calendarAction) and applyBridgeData ingestion
- JS HudTerminal workspace switcher, normalizer, and 4 dedicated renderers
- JS main.js Alt+1..5 workspace shortcuts
"""

import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestMilestone3IndexHtmlContract(unittest.TestCase):
    """Verifies that index.html satisfies all Milestone 3 DOM requirements and backward compatibility."""

    def setUp(self):
        index_path = os.path.join(REPO_ROOT, "index.html")
        with open(index_path, "r", encoding="utf-8") as f:
            self.html = f.read()

    def test_workspace_nav_and_tabs(self):
        # 5-Tab workspace navigation exists
        self.assertIn('id="hud-workspace-nav"', self.html)
        self.assertIn('id="tab-nav-home"', self.html)
        self.assertIn('id="tab-nav-todo"', self.html)
        self.assertIn('id="tab-nav-projects"', self.html)
        self.assertIn('id="tab-nav-calendar"', self.html)
        self.assertIn('id="tab-nav-memory"', self.html)
        self.assertIn('id="btn-open-settings"', self.html)

        # Tab badges exist
        self.assertIn('id="tab-todo-badge"', self.html)
        self.assertIn('id="tab-proj-badge"', self.html)
        self.assertIn('id="tab-cal-badge"', self.html)
        self.assertIn('id="tab-mem-badge"', self.html)

    def test_home_next_task_chip(self):
        # Subtle next-task chip exists in markup
        self.assertIn('id="home-next-task-chip"', self.html)
        self.assertIn('id="home-next-task-title"', self.html)
        self.assertIn('id="home-next-task-date"', self.html)

    def test_hud_workspace_container_and_views(self):
        # Main full-view workspace container exists
        self.assertIn('id="hud-workspace-container"', self.html)

        # 4 full-view dashboards exist
        self.assertIn('id="workspace-view-todo"', self.html)
        self.assertIn('id="workspace-view-projects"', self.html)
        self.assertIn('id="workspace-view-calendar"', self.html)
        self.assertIn('id="workspace-view-memory"', self.html)

        # View 1: To-Do Kanban elements
        self.assertIn('id="kanban-project-filters"', self.html)
        self.assertIn('id="kanban-quick-add-form"', self.html)
        self.assertIn('id="kanban-add-title"', self.html)
        self.assertIn('id="kanban-add-project"', self.html)
        self.assertIn('id="kanban-add-due"', self.html)
        self.assertIn('id="kanban-add-priority"', self.html)
        self.assertIn('id="btn-kanban-ai-plan"', self.html)
        self.assertIn('id="count-kanban-todo"', self.html)
        self.assertIn('id="count-kanban-doing"', self.html)
        self.assertIn('id="count-kanban-done"', self.html)
        self.assertIn('id="kanban-col-todo"', self.html)
        self.assertIn('id="kanban-col-doing"', self.html)
        self.assertIn('id="kanban-col-done"', self.html)

        # View 2: Project Hub elements
        self.assertIn('id="hub-tab-proj-active"', self.html)
        self.assertIn('id="hub-tab-proj-archive"', self.html)
        self.assertIn('id="hub-proj-active-count"', self.html)
        self.assertIn('id="hub-proj-arch-count"', self.html)
        self.assertIn('id="hub-proj-new-name"', self.html)
        self.assertIn('id="hub-proj-new-client"', self.html)
        self.assertIn('id="hub-proj-new-note"', self.html)
        self.assertIn('id="hub-proj-new-stack"', self.html)
        self.assertIn('id="btn-hub-create-proj"', self.html)
        self.assertIn('id="hub-projects-grid"', self.html)
        self.assertIn('id="hub-projects-archive-list"', self.html)

        # View 3: Calendar elements
        self.assertIn('id="btn-cal-view-weekly"', self.html)
        self.assertIn('id="btn-cal-view-monthly"', self.html)
        self.assertIn('id="btn-cal-view-agenda"', self.html)
        self.assertIn('id="btn-cal-sync-now"', self.html)
        self.assertIn('id="btn-cal-prev"', self.html)
        self.assertIn('id="btn-cal-today"', self.html)
        self.assertIn('id="btn-cal-next"', self.html)
        self.assertIn('id="cal-view-range-label"', self.html)
        self.assertIn('id="cal-sync-status-badge"', self.html)
        self.assertIn('id="cal-quick-add-form"', self.html)
        self.assertIn('id="cal-add-date"', self.html)
        self.assertIn('id="cal-add-time"', self.html)
        self.assertIn('id="cal-add-title"', self.html)
        self.assertIn('id="cal-view-weekly"', self.html)
        self.assertIn('id="cal-view-monthly"', self.html)
        self.assertIn('id="cal-view-agenda"', self.html)

        # View 4: Memory Dashboard elements
        self.assertIn('id="mem-skills-count"', self.html)
        self.assertIn('id="mem-new-skill-name"', self.html)
        self.assertIn('id="mem-new-skill-cat"', self.html)
        self.assertIn('id="btn-add-skill"', self.html)
        self.assertIn('id="mem-skills-list"', self.html)
        self.assertIn('id="mem-rules-count"', self.html)
        self.assertIn('id="mem-rules-search"', self.html)
        self.assertIn('id="mem-new-rule-text"', self.html)
        self.assertIn('id="mem-new-rule-proj"', self.html)
        self.assertIn('id="btn-add-rule"', self.html)
        self.assertIn('id="mem-rules-list"', self.html)
        self.assertIn('id="mem-profile-count"', self.html)
        self.assertIn('id="mem-profiling-prompt-card"', self.html)
        self.assertIn('id="mem-profiling-q-text"', self.html)
        self.assertIn('id="mem-profiling-options"', self.html)
        self.assertIn('id="mem-profiling-input"', self.html)
        self.assertIn('id="btn-mem-profiling-save"', self.html)
        self.assertIn('id="btn-mem-profiling-next"', self.html)
        self.assertIn('id="mem-profile-traits-list"', self.html)

    def test_backward_compatibility_elements_preserved(self):
        # All test contract elements from prior milestones remain in index.html
        self.assertIn('id="btn-color-safe"', self.html)
        self.assertIn('crt-overlay', self.html)
        self.assertIn('id="hud-slide-drawer"', self.html)
        self.assertIn('id="hud-drawer-backdrop"', self.html)
        self.assertIn('id="hud-todo-popover"', self.html)
        self.assertIn('id="neko-modal-overlay"', self.html)
        self.assertIn('id="neko-profile-inspector-overlay"', self.html)
        self.assertIn('id="agenda-pill"', self.html)
        self.assertIn('id="todo-pill"', self.html)
        self.assertIn('id="projects-pill"', self.html)
        self.assertIn('id="mem-total-badge"', self.html)


class TestMilestone3TerminalCss(unittest.TestCase):
    """Verifies that css/terminal.css contains all Milestone 3 styles and view-home hiding."""

    def setUp(self):
        css_path = os.path.join(REPO_ROOT, "css", "terminal.css")
        with open(css_path, "r", encoding="utf-8") as f:
            self.css = f.read()

    def test_workspace_nav_styling(self):
        self.assertIn(".hud-workspace-nav", self.css)
        self.assertIn(".workspace-tab-btn", self.css)
        self.assertIn(".tab-badge", self.css)

    def test_home_next_task_chip_styling(self):
        self.assertIn(".home-next-task-chip", self.css)
        self.assertIn(".chip-task-title", self.css)
        self.assertIn(".chip-task-date", self.css)

    def test_workspace_container_and_unobstructed_home_view(self):
        self.assertIn(".hud-workspace-container", self.css)
        # Ensure that in view-home, workspace container is hidden so cat is unobstructed
        self.assertTrue(
            "body.view-home #hud-workspace-container" in self.css
            and "display: none !important" in self.css
        )

    def test_dashboard_components_styling(self):
        # Kanban
        self.assertIn(".kanban-board-grid", self.css)
        self.assertIn(".header-todo", self.css)
        self.assertIn(".header-doing", self.css)
        self.assertIn(".header-done", self.css)
        self.assertIn(".kanban-card", self.css)
        self.assertIn(".btn-ai-breakdown", self.css)

        # Projects Hub
        self.assertIn(".hub-projects-grid", self.css)
        self.assertIn(".hub-project-card", self.css)
        self.assertIn(".hub-progress-bar", self.css)
        self.assertIn(".hub-tech-tag", self.css)

        # Calendar
        self.assertIn(".cal-weekly-grid", self.css)
        self.assertIn(".cal-monthly-grid", self.css)
        self.assertIn(".cal-agenda-list", self.css)
        self.assertIn(".pill-source-google", self.css)
        self.assertIn(".pill-source-local", self.css)
        self.assertIn(".pill-source-todo", self.css)

        # Memory
        self.assertIn(".memory-dashboard-grid", self.css)
        self.assertIn(".mem-skill-card", self.css)
        self.assertIn(".mem-rule-card", self.css)
        self.assertIn(".mem-profiling-prompt-card", self.css)

    def test_color_safe_and_mini_widget_mode(self):
        self.assertIn(".color-safe .hud-workspace-container", self.css)
        self.assertTrue("body.mini-widget-mode .hud-workspace-container" in self.css)


class TestMilestone3JavaScriptModules(unittest.TestCase):
    """Verifies that JavaScript files contain the required methods, state ingestions, and handlers."""

    def test_system_bridge_api_methods(self):
        bridge_path = os.path.join(REPO_ROOT, "js", "system-bridge.js")
        with open(bridge_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Methods exist
        self.assertIn("async todosAction(payload)", js)
        self.assertIn("async skillsLearnedAction(payload)", js)
        self.assertIn("async calendarAction(payload)", js)

        # Endpoints are correct
        self.assertIn("/api/todos/action", js)
        self.assertIn("/api/skills_learned/action", js)
        self.assertIn("/api/calendar/action", js)

        # Data ingestion in applyBridgeData
        self.assertIn("calendar_events", js)
        self.assertIn("todo_deadlines", js)
        self.assertIn("calendar_sync_status", js)
        self.assertIn("skills_learned", js)

    def test_hud_terminal_workspace_methods(self):
        hud_path = os.path.join(REPO_ROOT, "js", "hud-terminal.js")
        with open(hud_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Methods exist on HudTerminal
        self.assertIn("initMilestone3Workspace()", js)
        self.assertIn("switchWorkspace(viewId)", js)
        self.assertIn("normalizeTodo(item", js)
        self.assertIn("renderHomeNextTaskChip()", js)
        self.assertIn("renderKanbanBoard()", js)
        self.assertIn("renderProjectHub()", js)
        self.assertIn("renderCalendarDashboard()", js)
        self.assertIn("renderMemoryDashboard()", js)
        self.assertIn("updateWorkspaceDirtyChecking()", js)

        # Check that initMilestone3Workspace is called in constructor
        self.assertIn("this.initMilestone3Workspace();", js)

        # Check that updateWorkspaceDirtyChecking is called in update loop
        self.assertIn("this.updateWorkspaceDirtyChecking();", js)

    def test_main_js_alt_shortcuts(self):
        main_path = os.path.join(REPO_ROOT, "js", "main.js")
        with open(main_path, "r", encoding="utf-8") as f:
            js = f.read()

        # Alt+1..5 wired to switchWorkspace
        self.assertTrue("hud.switchWorkspace('view-home')" in js or 'switchWorkspace("view-home")' in js)
        self.assertTrue("hud.switchWorkspace('view-todo')" in js or 'switchWorkspace("view-todo")' in js)
        self.assertTrue("hud.switchWorkspace('view-projects')" in js or 'switchWorkspace("view-projects")' in js)
        self.assertTrue("hud.switchWorkspace('view-calendar')" in js or 'switchWorkspace("view-calendar")' in js)
        self.assertTrue("hud.switchWorkspace('view-memory')" in js or 'switchWorkspace("view-memory")' in js)

        # Bridge actions passed into HudTerminal
        self.assertIn("todosAction: (payload) => systemBridge.todosAction(payload)", js)
        self.assertIn("skillsLearnedAction: (payload) => systemBridge.skillsLearnedAction(payload)", js)
        self.assertIn("calendarAction: (payload) => systemBridge.calendarAction(payload)", js)


if __name__ == "__main__":
    unittest.main()
