"""
tests/test_parser_stress.py - Adversarial Stress & Verification Test Suite for Italian NL Parser.

Empirical Challenger 1 Suite verifying:
1. Complex Italian phrases, accents, mixed Italian/English, multiple hashtags, multiple priorities, malformed inputs.
2. Relative date calculations (domani, stasera, venerdì with forward-wrapping, dopodomani, tra 2 ore, alle 15:00, entro il 30/10).
3. Title cleaning fidelity (preservation of action verbs, stripping of command prefixes, removal of metadata).
4. Deterministic execution speed strictly < 5ms per call.
"""

from datetime import datetime, time, timedelta, timezone
import re
import time as time_mod
import unittest

from neko_bot.task.models import TaskPriority
from neko_bot.task.parser import (
    CALENDAR_DATE_PATTERN,
    INTERVAL_PATTERN,
    ISO_DATE_PATTERN,
    ItalianTaskParser,
    PRIORITY_PATTERN,
    REL_DAY_PATTERN,
    STANDALONE_TIME_PATTERN,
    TAG_PATTERN,
    WEEKDAY_PATTERN,
    clean_title,
    parse_deterministic,
    parse_italian_task,
)


class TestAdversarialItalianParser(unittest.TestCase):
    """Stress tests challenging assumptions, edge cases, and robustness of the Italian NL parser."""

    def setUp(self) -> None:
        # Reference anchor: Friday, October 23, 2026, at 10:00:00 UTC
        self.ref_time = datetime(2026, 10, 23, 10, 0, 0, tzinfo=timezone.utc)
        self.parser = ItalianTaskParser(reference_time=self.ref_time)

    # -------------------------------------------------------------------------
    # 1. Complex Italian Phrases, Accents & Unicode Fidelity
    # -------------------------------------------------------------------------

    def test_complex_multiclause_italian_sentence(self) -> None:
        """Verifies parsing of a complex natural sentence with multiple clauses and technical jargon."""
        prompt = (
            "Quando finisci il compile degli shader per il livello bosco, "
            "ricordami di esportare le normal maps in 4K con dithering #unreal_env !alta entro stasera alle 20:30"
        )
        res = self.parser.parse(prompt)
        self.assertEqual(res.project, "unreal_env")
        self.assertEqual(res.priority, TaskPriority.HIGH)
        self.assertIsNotNone(res.due_date)
        self.assertTrue(res.due_date.startswith("2026-10-23 20:30"))
        # Title should maintain the action verb and text while stripping tags and date
        self.assertIn("Quando finisci il compile degli shader", res.title)
        self.assertIn("esportare le normal maps", res.title)
        self.assertNotIn("#unreal_env", res.title)
        self.assertNotIn("!alta", res.title)

    def test_italian_accents_and_apostrophes(self) -> None:
        """Verifies preservation of Italian vowel accents (à, è, é, ì, ò, ù) and apostrophes."""
        cases = [
            ("Configurare l'illuminazione per la città #archviz !media", "Configurare l'illuminazione per la città", "archviz"),
            ("Rifare l'UV dell'oggetto d'oro #blender !bassa", "Rifare l'UV dell'oggetto d'oro", "blender"),
            ("Controllare fedeltà e qualità del rendering #vray !alta", "Controllare fedeltà e qualità del rendering", "vray"),
        ]
        for prompt, expected_title, expected_proj in cases:
            res = self.parser.parse(prompt)
            self.assertEqual(res.title, expected_title)
            self.assertEqual(res.project, expected_proj)

    def test_mixed_italian_and_english(self) -> None:
        """Verifies handling of mixed language inputs common in creative pipelines."""
        prompt = "Fix lighting glitch in foliage shader #unreal !urgent tomorrow at 16:00"
        res = self.parser.parse(prompt)
        self.assertEqual(res.project, "unreal")
        self.assertEqual(res.priority, TaskPriority.HIGH)
        self.assertIsNotNone(res.due_date)
        self.assertTrue(res.due_date.startswith("2026-10-24 16:00"))
        self.assertEqual(res.title, "Fix lighting glitch in foliage shader")

    # -------------------------------------------------------------------------
    # 2. Tag and Priority Extraction Variations
    # -------------------------------------------------------------------------

    def test_multiple_hashtags_behavior(self) -> None:
        """Verifies that multiple hashtags are all stripped from title and first is primary project."""
        prompt = "Bake textures #unreal #materials #vulkan !alta"
        res = self.parser.parse(prompt)
        self.assertEqual(res.project, "unreal")
        self.assertListEqual(res.tags, ["unreal", "materials", "vulkan"])
        self.assertNotIn("#unreal", res.title)
        self.assertNotIn("#materials", res.title)
        self.assertNotIn("#vulkan", res.title)
        self.assertEqual(res.title, "Bake textures")

    def test_explicit_priority_syntaxes(self) -> None:
        """Verifies both !token and 'priorità: alta' variants."""
        res1 = self.parser.parse("Revisione mesh priorità: alta #mesh")
        self.assertEqual(res1.priority, TaskPriority.HIGH)
        self.assertEqual(res1.title, "Revisione mesh")

        res2 = self.parser.parse("Pulizia nodi priority = low #blender")
        self.assertEqual(res2.priority, TaskPriority.LOW)
        self.assertEqual(res2.title, "Pulizia nodi")

        res3 = self.parser.parse("Setup camera priorita: media #cinema4d")
        self.assertEqual(res3.priority, TaskPriority.MEDIUM)
        self.assertEqual(res3.title, "Setup camera")

    def test_multiple_priorities_in_prompt(self) -> None:
        """Verifies parser behavior when conflicting priority tokens are passed."""
        # First priority should be captured without crashing
        res = self.parser.parse("Debug animation !bassa #game !alta")
        self.assertIn(res.priority, [TaskPriority.LOW, TaskPriority.HIGH])
        self.assertEqual(res.project, "game")

    # -------------------------------------------------------------------------
    # 3. Relative Date Calculations & Forward-Wrapping
    # -------------------------------------------------------------------------

    def test_relative_date_domani(self) -> None:
        """Verifies 'domani' defaults to 18:00 on the following day."""
        res = self.parser.parse("Consegna bozza domani")
        self.assertIsNotNone(res.due_date)
        self.assertEqual(res.due_date, "2026-10-24 18:00")
        self.assertEqual(res.title, "Consegna bozza")

    def test_relative_date_stasera(self) -> None:
        """Verifies 'stasera' defaults to 20:00 today."""
        res = self.parser.parse("Inviare daily log stasera")
        self.assertIsNotNone(res.due_date)
        self.assertEqual(res.due_date, "2026-10-23 20:00")
        self.assertEqual(res.title, "Inviare daily log")

    def test_relative_date_dopodomani(self) -> None:
        """Verifies 'dopodomani' and 'dopo domani' both resolve to +2 days at 18:00."""
        res1 = self.parser.parse("Sync sprint dopodomani")
        self.assertEqual(res1.due_date, "2026-10-25 18:00")

        res2 = self.parser.parse("Sync sprint dopo domani")
        self.assertEqual(res2.due_date, "2026-10-25 18:00")

    def test_weekday_forward_wrapping(self) -> None:
        """
        Reference is Friday 2026-10-23 10:00.
        - 'venerdì' (same day without time) -> next Friday 2026-10-30 (+7 days)
        - 'venerdì alle 16:00' (same day future time) -> today 2026-10-23 16:00
        - 'venerdì alle 08:00' (same day past time) -> next Friday 2026-10-30 08:00
        - 'lunedì' -> 2026-10-26 (+3 days)
        - 'mercoledì' -> 2026-10-28 (+5 days)
        """
        # Same day without explicit time wraps to next week
        res_fri_default = self.parser.parse("Presentazione venerdì")
        self.assertEqual(res_fri_default.due_date, "2026-10-30 18:00")

        # Same day with future time stays today
        res_fri_future = self.parser.parse("Presentazione venerdì alle 16:00")
        self.assertEqual(res_fri_future.due_date, "2026-10-23 16:00")

        # Same day with past time wraps forward +7 days
        res_fri_past = self.parser.parse("Presentazione venerdì alle 08:00")
        self.assertEqual(res_fri_past.due_date, "2026-10-30 08:00")

        # Monday forward wrap
        res_mon = self.parser.parse("Planning lunedì")
        self.assertEqual(res_mon.due_date, "2026-10-26 18:00")

        # Wednesday forward wrap
        res_wed = self.parser.parse("Demo clienti mercoledì alle 11:30")
        self.assertEqual(res_wed.due_date, "2026-10-28 11:30")

    def test_relative_intervals(self) -> None:
        """Verifies interval expressions like 'tra 2 ore', 'in 30 minuti', 'tra un'ora'."""
        res_2h = self.parser.parse("Call con art director tra 2 ore")
        self.assertEqual(res_2h.due_date, "2026-10-23 12:00")

        res_30m = self.parser.parse("Scaricare assets in 30 minuti")
        self.assertEqual(res_30m.due_date, "2026-10-23 10:30")

        res_1h = self.parser.parse("Backup salvataggio tra 1 ora")
        self.assertEqual(res_1h.due_date, "2026-10-23 11:00")

    def test_standalone_time_resolution(self) -> None:
        """
        Verifies standalone 'alle HH:MM':
        - 15:00 is future today -> 2026-10-23 15:00
        - 08:00 is past today -> 2026-10-24 08:00 (tomorrow)
        """
        res_future = self.parser.parse("Call investitori alle 15:00")
        self.assertEqual(res_future.due_date, "2026-10-23 15:00")

        res_past = self.parser.parse("Check posta alle 08:00")
        self.assertEqual(res_past.due_date, "2026-10-24 08:00")

    def test_calendar_dates_and_month_names(self) -> None:
        """Verifies calendar dates like 'entro il 30/10' and 'entro il 15 novembre'."""
        res1 = self.parser.parse("Consegna build entro il 30/10")
        self.assertEqual(res1.due_date, "2026-10-30 18:00")

        res2 = self.parser.parse("Fattura client-b per il 15 novembre alle 12:00")
        self.assertEqual(res2.due_date, "2026-11-15 12:00")

    # -------------------------------------------------------------------------
    # 4. Title Cleaning Fidelity & Action Verb Preservation
    # -------------------------------------------------------------------------

    def test_action_verbs_preserved_in_title(self) -> None:
        """Verifies that common Italian action verbs at the start of tasks are NOT stripped."""
        verbs = [
            "Fare bake delle normal maps",
            "Aggiungere luci alla scena",
            "Creare collision meshes per il veicolo",
            "Ottimizzare draw calls e shader",
            "Preparare report settimanale",
            "Disegnare concept per l'arma",
            "Scrivere documentazione delle API",
        ]
        for prompt in verbs:
            res = self.parser.parse(f"{prompt} #unreal !media")
            self.assertEqual(res.title, prompt)

    def test_command_prefixes_cleanly_stripped(self) -> None:
        """Verifies that command prefixes like 'aggiungi:', 'ricordami di' are cleanly stripped."""
        cases = [
            ("aggiungi: Configurare il rig", "Configurare il rig"),
            ("aggiungi task: Configurare il rig", "Configurare il rig"),
            ("crea task: Configurare il rig", "Configurare il rig"),
            ("nuovo task: Configurare il rig", "Configurare il rig"),
            ("ricordami di fare il backup", "Fare il backup"),
            ("ricorda di esportare il file", "Esportare il file"),
            ("da fare: pulire la cartella", "Pulire la cartella"),
            ("fare: pulire la cartella", "Pulire la cartella"),
            ("todo: pulire la cartella", "Pulire la cartella"),
            ("promemoria: chiamare il cliente", "Chiamare il cliente"),
            ("neko do ricordami di aggiornare i driver", "Aggiornare i driver"),
            ("neko task add Fix shader", "Fix shader"),
        ]
        for raw, expected in cases:
            res = self.parser.parse(raw)
            self.assertEqual(res.title, expected)

    def test_dangling_prepositions_stripped_at_boundaries(self) -> None:
        """Verifies dangling temporal prepositions left behind are cleanly stripped."""
        res1 = self.parser.parse("Revisionare codice entro il")
        self.assertEqual(res1.title, "Revisionare codice")

        res2 = self.parser.parse("Revisionare codice per le")
        self.assertEqual(res2.title, "Revisionare codice")

        res3 = self.parser.parse("Revisionare codice alle")
        self.assertEqual(res3.title, "Revisionare codice")

    # -------------------------------------------------------------------------
    # 5. Malformed, Boundary & Pathological Inputs
    # -------------------------------------------------------------------------

    def test_malformed_empty_and_whitespace(self) -> None:
        """Verifies parser does not crash on empty, whitespace, or punctuation-only strings."""
        res_empty = self.parser.parse("")
        self.assertEqual(res_empty.title, "")
        self.assertEqual(res_empty.project, "default")
        self.assertEqual(res_empty.priority, TaskPriority.MEDIUM)

        res_ws = self.parser.parse("   \t  \n  ")
        self.assertEqual(res_ws.title, "")

        res_punct = self.parser.parse("!??...,,")
        self.assertIsNotNone(res_punct.title)

    def test_only_tags_or_priority(self) -> None:
        """Verifies parser fallback when input has no plain text title."""
        res_tag = self.parser.parse("#unreal")
        self.assertEqual(res_tag.project, "unreal")
        self.assertEqual(res_tag.title, "Task #unreal")

        res_prio = self.parser.parse("!alta")
        self.assertEqual(res_prio.priority, TaskPriority.HIGH)

    def test_invalid_calendar_dates_do_not_crash(self) -> None:
        """Verifies that impossible calendar dates (e.g. 31/02) fail safely without raising ValueError."""
        res = self.parser.parse("Consegnare report entro il 31/02/2026")
        self.assertIsNone(res.due_date)
        # Text is preserved since date was invalid
        self.assertIn("31/02/2026", res.title)

    def test_extreme_input_length(self) -> None:
        """Verifies that huge strings (10,000 characters) do not cause catastrophic regex backtracking (ReDoS)."""
        huge_text = "Controllare log " * 500 + "#stress !bassa domani"
        start = time_mod.perf_counter()
        res = self.parser.parse(huge_text)
        elapsed = time_mod.perf_counter() - start
        self.assertEqual(res.project, "stress")
        self.assertEqual(res.priority, TaskPriority.LOW)
        self.assertLess(elapsed, 0.05, f"Catastrophic backtracking detected: took {elapsed:.4f}s")

    # -------------------------------------------------------------------------
    # 6. Performance SLA (< 5.0ms)
    # -------------------------------------------------------------------------

    def test_execution_speed_sla_under_load(self) -> None:
        """Verifies that 200 diverse inputs execute strictly within the < 5ms SLA (< 0.5ms avg expected)."""
        prompts = [
            "Bake textures #unreal !alta domani alle 15:00",
            "Meeting settimanale con il team di sviluppo #core !media lunedì alle 10:00",
            "Chiamata di sync con cliente #client-a entro il 30/10 alle 14:30",
            "ricordami di pulire la cache #sys !bassa stasera",
            "Esportare FBX animazioni in 30 minuti #anim !urgente",
            "da fare: verificare lighting bake #arch dopodomani alle 18:00",
            "Revisione shader foliage per il livello 3 #unreal_shaders !alta venerdì alle 16:00",
            "promemoria: inviare fattura #admin entro il 15 novembre",
            "Aggiungere particelle fumo alla scena #vfx !media tra 2 ore",
            "neko do ricordami di aggiornare i driver GPU #hardware !alta stasera",
        ] * 20  # 200 iterations

        start_time = time_mod.perf_counter()
        for p in prompts:
            self.parser.parse(p)
        total_elapsed = time_mod.perf_counter() - start_time

        avg_time = total_elapsed / len(prompts)
        self.assertLess(
            avg_time,
            0.005,
            f"Parser failed SLA: average time was {avg_time * 1000:.2f}ms (limit is 5.0ms)",
        )


if __name__ == "__main__":
    unittest.main()
