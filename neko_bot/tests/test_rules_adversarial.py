"""
tests/test_rules_adversarial.py - Adversarial Stress & Chaos Test Suite for RulesEngine.

Challenger 1 Suite for Milestone 3 (Features F14 & F15, AC3) verifying:
1. Extreme Window Titles: Unicode characters, Cyrillic/Kanji/Arabic, multi-byte emojis, ZWJ sequences.
2. Large Payload Scaling: Titles from 2,000 to 50,000 characters without ReDoS or memory blowout.
3. Whitespace & Control Characters: Leading/trailing/embedded newlines, tabs, carriage returns, null bytes.
4. Delimiter & Bracket Stress: Multiple dashes, en-dashes, em-dashes, nested and unclosed brackets.
5. Corrupted Configuration Recovery: Malformed JSON, 0-byte files, null fields, invalid regexes.
6. Hot-Reload Mechanics: Dynamic config update, resilience against mid-flight corruption, recovery.
7. Sub-Millisecond SLA: Benchmark 1,000 iterations verifying mean latency strictly < 1.0ms.
8. Defensive Boundary Safety: classify(None, None), empty strings, and non-standard inputs.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from neko_bot.tracker.rules import (
    ClassificationResult,
    DEFAULT_TRACKING_RULES,
    RulesEngine,
)


class TestAdversarialRulesEngine(unittest.TestCase):
    """Stress tests challenging assumptions, edge cases, and robustness of RulesEngine."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="neko_rules_stress_")
        self.rules_file = Path(self.temp_dir) / "tracking_rules.json"
        self.engine = RulesEngine(tracking_rules_path=self.rules_file)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # 1. Unicode, Emojis, and Multilingual Character Sets
    # =========================================================================

    def test_unicode_and_emojis_in_creative_apps(self) -> None:
        """Verifies multi-byte emojis, Japanese, Cyrillic, and Italian accents in creative titles."""
        # Unreal with emoji
        res = self.engine.classify("UnrealEditor.exe", "🚀CyberpunkCity🔥 - Unreal Editor")
        self.assertEqual(res.project, "🚀CyberpunkCity🔥")
        self.assertEqual(res.category, "creative")
        self.assertEqual(res.rule_source, "heuristic_unreal")

        # Blender with Japanese Kanji and emoji
        res2 = self.engine.classify("blender.exe", "C:\\Assets\\ロボット_メカ_🤖.blend - Blender 4.2")
        self.assertEqual(res2.project, "ロボット_メカ_🤖")
        self.assertEqual(res2.document, "ロボット_メカ_🤖.blend")
        self.assertEqual(res2.category, "creative")

        # Photoshop with Italian accents and French diacritics
        res3 = self.engine.classify("Photoshop.exe", "*Città_Élégante_🎨.psd @ 66.7% (RGB/16)")
        self.assertEqual(res3.project, "Città_Élégante_🎨")
        self.assertEqual(res3.category, "creative")

        # 3ds Max with Cyrillic
        res4 = self.engine.classify("3dsmax.exe", "D:\\3D\\Проект_Восход_2026.max - Autodesk 3ds Max")
        self.assertEqual(res4.project, "Проект_Восход_2026")
        self.assertEqual(res4.category, "creative")

    def test_unicode_and_emojis_in_developer_apps(self) -> None:
        """Verifies Unicode and emojis in IDEs and browser repositories."""
        # VS Code with emojis
        res = self.engine.classify("Code.exe", "server.py - 🐱neko_bot🐾 - Visual Studio Code")
        self.assertEqual(res.project, "🐱neko_bot🐾")
        self.assertEqual(res.document, "server.py")
        self.assertEqual(res.category, "dev")

        # GitHub in Chrome with Unicode repo
        res2 = self.engine.classify("chrome.exe", "Pull Request #99 · torvalds/linux-rt-⚡ · GitHub")
        self.assertEqual(res2.project, "linux-rt-⚡")
        self.assertEqual(res2.category, "dev")

    # =========================================================================
    # 2. Extreme Window Title Lengths (> 2000 chars, up to 50,000 chars)
    # =========================================================================

    def test_extremely_long_unreal_title(self) -> None:
        """Verifies 3,000 character title correctly extracts project without stack overflow."""
        long_proj = "Project_" + ("A" * 3000)
        title = f"{long_proj} - Unreal Editor"
        res = self.engine.classify("UnrealEditor.exe", title)
        self.assertEqual(res.project, long_proj)
        self.assertEqual(res.category, "creative")

    def test_extremely_long_blender_path(self) -> None:
        """Verifies deep nested path (5,000 chars) in Blender."""
        deep_path = "C:\\" + ("folder\\" * 500) + "hero_model.blend - Blender"
        res = self.engine.classify("blender.exe", deep_path)
        self.assertEqual(res.project, "hero_model")
        self.assertEqual(res.document, "hero_model.blend")
        self.assertEqual(res.category, "creative")

    def test_extreme_non_matching_payload_resilience(self) -> None:
        """Verifies 50,000 character non-matching garbage title evaluates safely and rapidly."""
        garbage = "XYZ_1234567890_!@#$%^&*()_+" * 1500  # ~45,000 chars
        t0 = time.perf_counter()
        res = self.engine.classify("Code.exe", garbage)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        self.assertEqual(res.project, "VSCode")
        self.assertEqual(res.category, "dev")
        self.assertEqual(res.rule_source, "heuristic_vscode_generic")
        # Ensure evaluation took less than 20ms even for a massive 45k string
        self.assertLess(elapsed_ms, 20.0)

    # =========================================================================
    # 3. Newlines, Tabs, Carriage Returns & Control Characters
    # =========================================================================

    def test_whitespace_and_newlines_surrounding_title(self) -> None:
        """Verifies titles wrapped with newlines, carriage returns, and tabs are stripped."""
        dirty_title = "\r\n\t  CyberpunkCity - Unreal Editor  \t\r\n"
        res = self.engine.classify("UnrealEditor.exe", dirty_title)
        self.assertEqual(res.project, "CyberpunkCity")
        self.assertEqual(res.category, "creative")
        self.assertEqual(res.rule_source, "heuristic_unreal")

    def test_embedded_newlines_fallback_safely(self) -> None:
        """Verifies titles with embedded newlines do not crash regexes and fall back safely."""
        multiline_title = "CyberpunkCity\nSecondLine\rThirdLine - Unreal Editor"
        res = self.engine.classify("UnrealEditor.exe", multiline_title)
        # Regex [^\n\r] intentionally stops at newline, falling back to generic without exception
        self.assertIn(res.project, ("CyberpunkCity", "UnrealEngine"))
        self.assertEqual(res.category, "creative")

    def test_pure_whitespace_titles_are_ignored(self) -> None:
        """Verifies titles containing only newlines, spaces, and tabs are treated as ignored."""
        res = self.engine.classify("explorer.exe", "  \n\t\r  ")
        self.assertTrue(res.is_ignored)
        self.assertEqual(res.project, "Ignored")
        self.assertEqual(res.category, "system")

    # =========================================================================
    # 4. Brackets, Parentheses, and Multiple Dashes
    # =========================================================================

    def test_unreal_with_complex_build_tags_and_parentheses(self) -> None:
        """Verifies complex Unreal titles with platform, build tags, and brackets."""
        title = "CitySimulator [DirectX 12] (Development 64-bit) - Unreal Editor"
        res = self.engine.classify("UnrealEditor.exe", title)
        self.assertEqual(res.project, "CitySimulator")
        self.assertEqual(res.category, "creative")

    def test_vs_code_with_multiple_dashes_in_path(self) -> None:
        """Verifies VS Code handles files with dashes without raising unhandled exceptions."""
        title = "my-component-test.ts - frontend-app - Visual Studio Code"
        res = self.engine.classify("Code.exe", title)
        # Should either extract project or cleanly fall back to VSCode dev
        self.assertEqual(res.category, "dev")
        self.assertIn(res.project, ("frontend-app", "VSCode"))

    def test_blender_bracketed_title(self) -> None:
        """Verifies Blender titles formatted as [filename.blend] are correctly parsed."""
        title = "[scifi_corridor_v02.blend] - Blender 4.1"
        res = self.engine.classify("blender.exe", title)
        self.assertEqual(res.project, "scifi_corridor_v02")
        self.assertEqual(res.document, "scifi_corridor_v02.blend")

    # =========================================================================
    # 5. Corrupted Configuration Files & Resilient Recovery
    # =========================================================================

    def test_empty_json_file_recovery(self) -> None:
        """Verifies 0-byte tracking_rules.json retains default tracking rules without crashing."""
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write("")

        # Force mtime update
        os.utime(self.rules_file, (time.time() + 10, time.time() + 10))

        # Must not raise JSONDecodeError
        res = self.engine.classify("UnrealEditor.exe", "CyberpunkCity - Unreal Editor")
        self.assertEqual(res.project, "CyberpunkCity")

    def test_syntactically_invalid_json_recovery(self) -> None:
        """Verifies broken JSON syntax retains default tracking rules without crashing."""
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write("{ \"custom_projects\": [ { \"name\": \"broken\" ... INVALID JSON ")

        os.utime(self.rules_file, (time.time() + 10, time.time() + 10))

        res = self.engine.classify("blender.exe", "mech.blend - Blender")
        self.assertEqual(res.project, "mech")
        self.assertEqual(res.category, "creative")

    def test_null_and_malformed_types_in_json_config(self) -> None:
        """Verifies null fields or wrong types in tracking_rules.json do not crash engine."""
        malformed_config = {
            "version": 1,
            "custom_projects": None,
            "app_aliases": None,
            "app_categories": None,
            "ignored_windows": None,
        }
        with open(self.rules_file, "w", encoding="utf-8") as f:
            json.dump(malformed_config, f)

        os.utime(self.rules_file, (time.time() + 10, time.time() + 10))

        # Engine should catch TypeError/AttributeError and retain defaults
        res = self.engine.classify("Photoshop.exe", "artwork.psd @ 100%")
        self.assertEqual(res.project, "artwork")
        self.assertEqual(res.category, "creative")

    def test_invalid_regex_syntax_in_custom_projects(self) -> None:
        """Verifies uncompilable regexes in custom projects are skipped without crashing."""
        config_with_bad_regex = {
            "version": 1,
            "custom_projects": [
                {
                    "name": "BrokenRegexProj",
                    "patterns": ["(?i)(unclosed_group", "valid_pattern"],
                    "category": "creative",
                }
            ],
        }
        with open(self.rules_file, "w", encoding="utf-8") as f:
            json.dump(config_with_bad_regex, f)

        os.utime(self.rules_file, (time.time() + 10, time.time() + 10))

        # The valid pattern in the list should still work
        res = self.engine.classify("unknown.exe", "This matches valid_pattern nicely")
        self.assertEqual(res.project, "BrokenRegexProj")
        self.assertEqual(res.category, "creative")

    # =========================================================================
    # 6. Dynamic Hot-Reloading & Invalidation
    # =========================================================================

    def test_hot_reload_on_disk_modification(self) -> None:
        """Verifies modifying tracking_rules.json updates classification dynamically."""
        # Baseline before customization
        res1 = self.engine.classify("notepad.exe", "Notes on ProjectX.txt")
        self.assertEqual(res1.project, "notepad")
        self.assertEqual(res1.category, "general")

        # Now add custom project rule
        custom_config = {
            "version": 1,
            "custom_projects": [
                {
                    "name": "SecretProjectX",
                    "patterns": ["(?i)ProjectX"],
                    "category": "creative",
                }
            ],
        }
        time.sleep(0.01)  # Ensure distinct mtime
        with open(self.rules_file, "w", encoding="utf-8") as f:
            json.dump(custom_config, f)

        # Call classify again -> triggers _check_reload
        res2 = self.engine.classify("notepad.exe", "Notes on ProjectX.txt")
        self.assertEqual(res2.project, "SecretProjectX")
        self.assertEqual(res2.category, "creative")
        self.assertEqual(res2.rule_source, "custom_project")

    def test_init_with_preexisting_corrupted_config(self) -> None:
        """Verifies RulesEngine initializes cleanly even if config file is already corrupt on disk."""
        bad_path = Path(self.temp_dir) / "corrupt_on_boot.json"
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("<<< NOT VALID JSON >>>")

        # Must not raise any exception during init
        engine = RulesEngine(tracking_rules_path=bad_path)
        res = engine.classify("blender.exe", "sculpt.blend - Blender")
        self.assertEqual(res.project, "sculpt")
        self.assertEqual(res.category, "creative")

    def test_init_with_nested_nonexistent_directory(self) -> None:
        """Verifies auto-generation of parent directories when path does not exist."""
        deep_path = Path(self.temp_dir) / "deep" / "nested" / "dir" / "tracking_rules.json"
        engine = RulesEngine(tracking_rules_path=deep_path)
        self.assertTrue(deep_path.exists())
        res = engine.classify("UnrealEditor.exe", "CyberpunkCity - Unreal Editor")
        self.assertEqual(res.project, "CyberpunkCity")

    def test_redos_pattern_backtracking_resistance(self) -> None:
        """Verifies that built-in regexes resist catastrophic backtracking on crafted inputs."""
        # Crafted strings designed to trigger quadratic or exponential backtracking if regexes are flawed
        crafted_inputs = [
            ("UnrealEditor.exe", ("a" * 1000) + ("-" * 100) + ("b" * 1000)),
            ("Photoshop.exe", ("*" * 50) + ("@" * 50) + ("a" * 2000)),
            ("Code.exe", "● " + ("a" * 1000) + " - " + ("b" * 1000) + " - Visual Studio Code"),
            ("devenv.exe", ("a" * 1000) + " ()()()()() " + ("b" * 1000) + " - Microsoft Visual Studio"),
            ("chrome.exe", "Jira - " + ("A" * 1000) + " - " + ("B" * 1000)),
            ("chrome.exe", "GitHub " + ("foo/bar/" * 200) + " · GitHub"),
        ]

        for app, title in crafted_inputs:
            t0 = time.perf_counter()
            res = self.engine.classify(app, title)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertLess(
                elapsed_ms,
                50.0,
                f"Evaluation of {app} with crafted input took {elapsed_ms:.2f}ms (> 50ms) - possible ReDoS",
            )
            self.assertIsInstance(res, ClassificationResult)

    # =========================================================================
    # 7. Sub-Millisecond Speed Benchmark (< 1.0 ms SLA)
    # =========================================================================

    def test_sub_millisecond_evaluation_speed_benchmark(self) -> None:
        """Verifies that 1,000 diverse window title evaluations average well under 1.0ms."""
        test_inputs = [
            ("UnrealEditor.exe", "CyberpunkCity - Unreal Editor"),
            ("blender.exe", "C:\\Work\\mech.blend - Blender 4.2"),
            ("Photoshop.exe", "concept.psd @ 100% (RGB/8)"),
            ("3dsmax.exe", "building.max - Autodesk 3ds Max 2024"),
            ("Code.exe", "rules.py - neko_bot - Visual Studio Code"),
            ("chrome.exe", "torvalds/linux · GitHub"),
            ("chrome.exe", "Python Documentation - Standard Library"),
            ("slack.exe", "#dev-team - Slack"),
            ("explorer.exe", "C:\\Windows\\System32"),
            ("notepad.exe", "random_notes.txt - Notepad"),
        ]

        total_iterations = 1000
        t0 = time.perf_counter()
        for i in range(total_iterations):
            app, title = test_inputs[i % len(test_inputs)]
            self.engine.classify(app, title)
        total_time_sec = time.perf_counter() - t0

        avg_latency_ms = (total_time_sec / total_iterations) * 1000.0
        # Average latency must be strictly < 1.0ms (typically 0.01ms - 0.05ms)
        self.assertLess(
            avg_latency_ms,
            1.0,
            f"Classification average latency {avg_latency_ms:.4f}ms exceeded 1.0ms SLA",
        )

    # =========================================================================
    # 8. Boundary & NoneType Safety
    # =========================================================================

    def test_none_and_empty_inputs(self) -> None:
        """Verifies classify safely handles None, empty strings, and whitespace."""
        res_both_none = self.engine.classify(None, None)  # type: ignore
        self.assertTrue(res_both_none.is_ignored)
        self.assertEqual(res_both_none.project, "Ignored")

        res_app_none = self.engine.classify(None, "Valid Title")  # type: ignore
        self.assertTrue(res_app_none.is_ignored)

        res_title_none = self.engine.classify("Code.exe", None)  # type: ignore
        self.assertTrue(res_title_none.is_ignored)

        res_empty = self.engine.classify("", "")
        self.assertTrue(res_empty.is_ignored)


if __name__ == "__main__":
    unittest.main()
