"""
neko_bot.tracker.rules - Universal Window-Title Heuristics & Customizable Tracking Rules.

Provides:
- ClassificationResult: Data model capturing classified project, category, and document.
- RulesEngine: 5-tier evaluation engine for window title and application classification:
  Tier 1: Ignored windows (Lock screen, screen saver, desktop shell, blank titles)
  Tier 2: User custom project rules from workspace/tracking_rules.json
  Tier 3: App-specific heuristics (Unreal, Blender, Photoshop, 3ds Max, Premiere, After Effects, IDEs, Browsers, Comms, System)
  Tier 4: User app aliases and category overrides
  Tier 5: Generic fallback (app executable stem)
- Hot-reload on config file modification and recovery from corrupt JSON.
"""

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Pattern, Union

logger = logging.getLogger("neko_bot.tracker.rules")

VALID_CATEGORIES = {"creative", "dev", "comms", "browsing", "system", "general"}


@dataclass(frozen=True)
class ClassificationResult:
    """Immutable result of evaluating a window title and application."""
    project: str
    category: str
    document: Optional[str] = None
    is_ignored: bool = False
    rule_source: str = "fallback"
    raw_app: str = ""
    raw_title: str = ""


@dataclass
class CustomProjectRule:
    name: str
    patterns: List[Pattern[str]]
    apps: List[str] = field(default_factory=list)
    category: str = "general"


@dataclass
class IgnoredWindowRule:
    app_pattern: Optional[Pattern[str]] = None
    title_pattern: Optional[Pattern[str]] = None


DEFAULT_TRACKING_RULES: Dict[str, Any] = {
    "version": 1,
    "custom_projects": [],
    "app_aliases": {
        "ue5editor.exe": "UnrealEditor.exe",
        "photoshop_beta.exe": "Photoshop.exe",
        "cursor.exe": "Code.exe",
        "windsurf.exe": "Code.exe",
    },
    "app_categories": {
        "sublime_text.exe": "dev",
        "notepad++.exe": "dev",
        "vlc.exe": "general",
        "spotify.exe": "general",
    },
    "ignored_windows": [
        {"app": "^LockApp\\.exe$"},
        {"app": "^LogonUI\\.exe$"},
        {"title": "^Program Manager$"},
        {"title": "(?i)screen saver"},
        {"app": "^explorer\\.exe$", "title": "^$"},
        {"app": "^ApplicationFrameHost\\.exe$", "title": "^$"},
    ],
}


class RulesEngine:
    """
    Evaluates window titles and application names through a 5-tier classification pipeline.
    """

    def __init__(self, tracking_rules_path: Optional[Union[str, Path]] = None) -> None:
        self.rules_path: Optional[Path] = Path(tracking_rules_path) if tracking_rules_path else None
        self._last_mtime: float = 0.0

        # Compiled rule structures
        self._custom_projects: List[CustomProjectRule] = []
        self._app_aliases: Dict[str, str] = {}
        self._app_categories: Dict[str, str] = {}
        self._ignored_rules: List[IgnoredWindowRule] = []

        # Load built-in defaults first
        self._load_from_dict(DEFAULT_TRACKING_RULES)

        # If rules file exists or is specified, attempt load / auto-create
        if self.rules_path:
            self._init_rules_file()

    def _init_rules_file(self) -> None:
        if self.rules_path is None:
            return

        if not self.rules_path.exists():
            try:
                self.rules_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.rules_path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_TRACKING_RULES, f, indent=2)
                logger.info("Generated default tracking_rules.json at %s", self.rules_path)
            except Exception as e:
                logger.debug("Could not create default tracking_rules.json: %s", e)

        self._check_reload()

    def _check_reload(self) -> None:
        if not self.rules_path or not self.rules_path.exists():
            return

        try:
            mtime = self.rules_path.stat().st_mtime
            if mtime > self._last_mtime:
                self._last_mtime = mtime
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._load_from_dict(data)
                    logger.debug("Reloaded tracking rules from %s", self.rules_path)
        except Exception as e:
            logger.warning("Failed to load tracking rules from %s (retaining defaults): %s", self.rules_path, e)

    def _load_from_dict(self, data: Dict[str, Any]) -> None:
        """Parses configuration dictionary and safely compiles all regexes."""
        # 1. Custom Projects
        custom_projects: List[CustomProjectRule] = []
        for pdata in data.get("custom_projects", []):
            if not isinstance(pdata, dict):
                continue
            name = str(pdata.get("name", "")).strip()
            if not name:
                continue
            cat = str(pdata.get("category", "general"))
            if cat not in VALID_CATEGORIES:
                cat = "general"
            apps = [str(a).strip().lower() for a in pdata.get("apps", []) if a]
            patterns: List[Pattern[str]] = []
            for pat_str in pdata.get("patterns", []):
                try:
                    patterns.append(re.compile(pat_str))
                except re.error as e:
                    logger.warning("Invalid regex '%s' in project rule '%s': %s", pat_str, name, e)
            if patterns:
                custom_projects.append(CustomProjectRule(name=name, patterns=patterns, apps=apps, category=cat))
        self._custom_projects = custom_projects

        # 2. App Aliases
        aliases = {}
        for k, v in data.get("app_aliases", {}).items():
            if isinstance(k, str) and isinstance(v, str):
                aliases[k.strip().lower()] = v.strip()
        self._app_aliases = aliases

        # 3. App Categories
        categories = {}
        for k, v in data.get("app_categories", {}).items():
            if isinstance(k, str) and isinstance(v, str):
                cat = v.strip().lower()
                categories[k.strip().lower()] = cat if cat in VALID_CATEGORIES else "general"
        self._app_categories = categories

        # 4. Ignored Windows
        ignored: List[IgnoredWindowRule] = []
        for idata in data.get("ignored_windows", []):
            if not isinstance(idata, dict):
                continue
            app_p = None
            title_p = None
            if "app" in idata and idata["app"]:
                try:
                    app_p = re.compile(idata["app"], re.IGNORECASE)
                except re.error as e:
                    logger.warning("Invalid app regex '%s' in ignored rule: %s", idata["app"], e)
            if "title" in idata and idata["title"]:
                try:
                    title_p = re.compile(idata["title"], re.IGNORECASE)
                except re.error as e:
                    logger.warning("Invalid title regex '%s' in ignored rule: %s", idata["title"], e)
            if app_p or title_p:
                ignored.append(IgnoredWindowRule(app_pattern=app_p, title_pattern=title_p))
        self._ignored_rules = ignored

    def classify(self, app: str, title: str) -> ClassificationResult:
        """
        Classifies an application and window title through the 5-tier pipeline.
        """
        self._check_reload()

        raw_app = app or ""
        raw_title = title or ""
        norm_app = raw_app.strip()
        norm_title = raw_title.strip()
        app_lower = norm_app.lower()

        # Resolve alias if present
        if app_lower in self._app_aliases:
            norm_app = self._app_aliases[app_lower]
            app_lower = norm_app.lower()

        # -------------------------------------------------------------
        # Tier 1: Ignored Windows
        # -------------------------------------------------------------
        # Built-in hard filters for blank/lock screen/screensaver
        if not norm_title or not norm_app:
            return ClassificationResult(
                project="Ignored",
                category="system",
                is_ignored=True,
                rule_source="ignored_empty",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        for rule in self._ignored_rules:
            app_match = True if rule.app_pattern is None else bool(rule.app_pattern.search(norm_app))
            title_match = True if rule.title_pattern is None else bool(rule.title_pattern.search(norm_title))
            if app_match and title_match:
                return ClassificationResult(
                    project="Ignored",
                    category="system",
                    is_ignored=True,
                    rule_source="ignored_rule",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )

        if app_lower in ("lockapp.exe", "logonui.exe") or "screen saver" in norm_title.lower() or norm_title == "Program Manager":
            return ClassificationResult(
                project="Ignored",
                category="system",
                is_ignored=True,
                rule_source="ignored_builtin",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # -------------------------------------------------------------
        # Tier 2: User Custom Project Rules
        # -------------------------------------------------------------
        for cp in self._custom_projects:
            if cp.apps and app_lower not in cp.apps:
                continue
            for pat in cp.patterns:
                if pat.search(norm_title):
                    return ClassificationResult(
                        project=cp.name,
                        category=cp.category,
                        rule_source="custom_project",
                        raw_app=raw_app,
                        raw_title=raw_title,
                    )

        # -------------------------------------------------------------
        # Tier 3: Built-In Application Heuristics
        # -------------------------------------------------------------

        # 3.1 Unreal Engine
        if app_lower in ("unrealeditor.exe", "ue4editor.exe"):
            m = re.match(r"^(?P<project>[^-\(\[\]\n\r]+?)(?:\s+[\(\[][^\)\]]+[\)\]])*\s*-\s*Unreal Editor", norm_title)
            if m:
                proj = m.group("project").strip()
                return ClassificationResult(
                    project=proj,
                    category="creative",
                    rule_source="heuristic_unreal",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            m_fallback = re.match(r"^(?P<project>[^-\(\n\r]+?)\s+\(Unreal Editor", norm_title)
            if m_fallback:
                return ClassificationResult(
                    project=m_fallback.group("project").strip(),
                    category="creative",
                    rule_source="heuristic_unreal",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="UnrealEngine",
                category="creative",
                rule_source="heuristic_unreal_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.2 Blender
        if app_lower == "blender.exe":
            m_blend = re.search(r"(?:[\\/\[]|^)(?P<doc>[^\\/\[\]\n\r]+\.blend)", norm_title, re.IGNORECASE)
            if m_blend:
                doc = m_blend.group("doc").strip()
                stem = doc[:-6] if doc.lower().endswith(".blend") else doc
                return ClassificationResult(
                    project=stem,
                    category="creative",
                    document=doc,
                    rule_source="heuristic_blender",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="Blender",
                category="creative",
                rule_source="heuristic_blender_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.3 Adobe Photoshop
        if app_lower == "photoshop.exe":
            m_psd = re.match(r"^(?:\*\s*)?(?P<doc>[^@\n\r]+?\.(?:psd|psb|tif|tiff|png|jpg|jpeg))\s*@", norm_title, re.IGNORECASE)
            if m_psd:
                doc = m_psd.group("doc").strip()
                stem = os.path.splitext(doc)[0]
                return ClassificationResult(
                    project=stem,
                    category="creative",
                    document=doc,
                    rule_source="heuristic_photoshop",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            m_generic = re.match(r"^(?:\*\s*)?(?P<doc>[^@\n\r]+?)\s*@", norm_title)
            if m_generic:
                doc = m_generic.group("doc").strip()
                stem = os.path.splitext(doc)[0]
                return ClassificationResult(
                    project=stem,
                    category="creative",
                    document=doc,
                    rule_source="heuristic_photoshop",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="Photoshop",
                category="creative",
                rule_source="heuristic_photoshop_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.4 Autodesk 3ds Max
        if app_lower == "3dsmax.exe":
            m_max = re.search(r"(?:[\\/\[]|^)(?P<doc>[^\\/\[\]\n\r]+\.[mM][aA][xX])", norm_title)
            if m_max:
                doc = m_max.group("doc").strip()
                stem = doc[:-4] if doc.lower().endswith(".max") else doc
                return ClassificationResult(
                    project=stem,
                    category="creative",
                    document=doc,
                    rule_source="heuristic_3dsmax",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="3dsMax",
                category="creative",
                rule_source="heuristic_3dsmax_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.5 Premiere Pro & After Effects
        if "premiere" in app_lower:
            m_pr = re.search(r"(?:[\\/\[]|^)(?P<doc>[^\\/\[\]\n\r]+\.prproj)", norm_title, re.IGNORECASE)
            if m_pr:
                doc = m_pr.group("doc").strip()
                stem = doc[:-7] if doc.lower().endswith(".prproj") else doc
                return ClassificationResult(
                    project=stem,
                    category="creative",
                    document=doc,
                    rule_source="heuristic_premiere",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="PremierePro",
                category="creative",
                rule_source="heuristic_premiere_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        if app_lower == "afterfx.exe" or "after effects" in norm_title.lower():
            m_ae = re.search(r"(?:[\\/\[]|^)(?P<doc>[^\\/\[\]\n\r]+\.aep)", norm_title, re.IGNORECASE)
            if m_ae:
                doc = m_ae.group("doc").strip()
                stem = doc[:-4] if doc.lower().endswith(".aep") else doc
                return ClassificationResult(
                    project=stem,
                    category="creative",
                    document=doc,
                    rule_source="heuristic_aftereffects",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="AfterEffects",
                category="creative",
                rule_source="heuristic_aftereffects_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.6 IDEs: VS Code
        if app_lower in ("code.exe", "code - insiders.exe"):
            m_code = re.match(
                r"^(?:●\s*)?(?:(?P<file>[^–\-\[\]\n\r]+?)\s+[-–]\s+)?(?P<project>[^–\-\[\]\n\r]+?)\s+[-–]\s+Visual Studio Code",
                norm_title,
            )
            if m_code:
                doc = m_code.group("file").strip() if m_code.group("file") else None
                proj = m_code.group("project").strip()
                return ClassificationResult(
                    project=proj,
                    category="dev",
                    document=doc,
                    rule_source="heuristic_vscode",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="VSCode",
                category="dev",
                rule_source="heuristic_vscode_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.7 Visual Studio
        if app_lower == "devenv.exe":
            m_vs = re.match(
                r"^(?:(?P<file>[^–\-\(\[\n\r]+?)\s+[-–]\s+)?(?P<project>[^–\-\(\[\n\r]+?)(?:\s*\([^\)]+\))*\s+[-–]\s+Microsoft Visual Studio",
                norm_title,
            )
            if m_vs:
                doc = m_vs.group("file").strip() if m_vs.group("file") else None
                proj = m_vs.group("project").strip()
                return ClassificationResult(
                    project=proj,
                    category="dev",
                    document=doc,
                    rule_source="heuristic_visual_studio",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            return ClassificationResult(
                project="VisualStudio",
                category="dev",
                rule_source="heuristic_visual_studio_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.8 JetBrains IDEs
        if any(jb in app_lower for jb in ("pycharm", "idea", "clion", "rider", "webstorm")):
            m_jb = re.match(r"^(?P<project>[^–\-\[\n\r]+?)\s*[–\-]\s*(?:(?P<file>[^\[\n\r]+?)\s*)?(?:\[(?P<root>[^\]]+)\])?", norm_title)
            if m_jb:
                proj = m_jb.group("project").strip()
                doc = m_jb.group("file").strip() if m_jb.group("file") else None
                return ClassificationResult(
                    project=proj,
                    category="dev",
                    document=doc,
                    rule_source="heuristic_jetbrains",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )
            stem = os.path.splitext(norm_app)[0]
            return ClassificationResult(
                project=stem,
                category="dev",
                rule_source="heuristic_jetbrains_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.9 Web Browsers
        if app_lower in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"):
            # Check GitHub: pull repo name e.g. "Pull Request #42 · org/repo · GitHub" -> "repo"
            if "github" in norm_title.lower():
                m_gh = re.search(r"(?:[·\s]|^)[^\s/·]+[/](?P<repo>[^\s/·]+)(?:[·\s]|$)", norm_title)
                if m_gh:
                    return ClassificationResult(
                        project=m_gh.group("repo"),
                        category="dev",
                        rule_source="heuristic_browser_github",
                        raw_app=raw_app,
                        raw_title=raw_title,
                    )
                return ClassificationResult(
                    project="GitHub",
                    category="dev",
                    rule_source="heuristic_browser_github",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )

            # Jira
            m_jira = re.search(r"\[(?P<ticket>[A-Z0-9]+-\d+)\]|Jira.*?[-\s]+(?P<proj>[A-Z0-9]+-\d+)", norm_title)
            if m_jira:
                t = m_jira.group("ticket") or m_jira.group("proj")
                return ClassificationResult(
                    project=t,
                    category="dev",
                    rule_source="heuristic_browser_jira",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )

            # Documentation
            if "documentation" in norm_title.lower() or "docs" in norm_title.lower():
                return ClassificationResult(
                    project="Documentation",
                    category="dev",
                    rule_source="heuristic_browser_docs",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )

            # YouTube / General media
            if "youtube" in norm_title.lower():
                return ClassificationResult(
                    project="YouTube",
                    category="browsing",
                    rule_source="heuristic_browser_youtube",
                    raw_app=raw_app,
                    raw_title=raw_title,
                )

            # Generic browser
            stem = os.path.splitext(norm_app)[0]
            return ClassificationResult(
                project=stem,
                category="browsing",
                rule_source="heuristic_browser_generic",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.10 Comms
        if app_lower in ("slack.exe", "discord.exe", "ms-teams.exe", "telegram.exe", "outlook.exe"):
            stem = os.path.splitext(norm_app)[0]
            return ClassificationResult(
                project=stem,
                category="comms",
                rule_source="heuristic_comms",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # 3.11 System
        if app_lower in ("taskmgr.exe", "explorer.exe", "regedit.exe", "cmd.exe", "powershell.exe", "windowsterminal.exe"):
            stem = os.path.splitext(norm_app)[0]
            return ClassificationResult(
                project=stem,
                category="system",
                rule_source="heuristic_system",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # -------------------------------------------------------------
        # Tier 4: User App Category Overrides
        # -------------------------------------------------------------
        if app_lower in self._app_categories:
            stem = os.path.splitext(norm_app)[0]
            return ClassificationResult(
                project=stem,
                category=self._app_categories[app_lower],
                rule_source="custom_app_category",
                raw_app=raw_app,
                raw_title=raw_title,
            )

        # -------------------------------------------------------------
        # Tier 5: Generic Fallback
        # -------------------------------------------------------------
        stem = os.path.splitext(norm_app)[0] if norm_app else "General"
        return ClassificationResult(
            project=stem,
            category="general",
            rule_source="fallback",
            raw_app=raw_app,
            raw_title=raw_title,
        )
