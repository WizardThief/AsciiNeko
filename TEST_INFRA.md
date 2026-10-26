# E2E Test Infra: AsciiNeko Golden Architecture (Phases 1, 2, 3)

## Test Philosophy
- Opaque-box, requirement-driven. Derived from `ORIGINAL_REQUEST.md` (header `## 2026-10-26T20:24:41Z`) and `ANALISI_E_PROPOSTE_NEKO.md`.
- No dependency on implementation internals.
- Verification mechanism: Standard Python library `unittest` (`python -m unittest discover tests/ -p "test_*.py"`).
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Combinatorial + Real-World Workload Testing.

## Feature Inventory Coverage
| # | Feature | Source | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---------|--------|:------:|:------:|:------:|:------:|
| F01 | SQLite-WAL Storage Engine | R1.1 | 5 | 5 | ✓ | ✓ |
| F02 | Transactional Atomic JSON Writer | R1.1 | 5 | 5 | ✓ | ✓ |
| F03 | Safe Atomic JSON Reader & .corrupt.bak | R1.1 | 5 | 5 | ✓ | ✓ |
| F04 | Non-Destructive Data Migration | R1.1 | 5 | 5 | ✓ | ✓ |
| F05 | FTS5 Lexical Retrieval with Time-Decay | R1.2 | 5 | 5 | ✓ | ✓ |
| F06 | Pinned Rule Exemption ($\lambda = 0$) | R1.2 | 5 | 5 | ✓ | ✓ |
| F07 | Hybrid Deduplication Engine | R1.2 | 5 | 5 | ✓ | ✓ |
| F08 | Deterministic Token Budget Manager | R1.3 | 5 | 5 | ✓ | ✓ |
| F09 | Dynamic VRAM Monitor & Ollama Unload | R1.3 | 5 | 5 | ✓ | ✓ |
| F10 | Monospace Typography Overhaul | R2.1 | 5 | 5 | ✓ | ✓ |
| F11 | Color-Safe Mode Toggle | R2.1 | 5 | 5 | ✓ | ✓ |
| F12 | 4-Channel Hardware Telemetry | R2.1 | 5 | 5 | ✓ | ✓ |
| F13 | Active Window Project Time-Tracking | R2.1 | 5 | 5 | ✓ | ✓ |
| F14 | 5-Minute AFK Inactivity Detector | R2.1 | 5 | 5 | ✓ | ✓ |
| F15 | Unified Slide-Out Task & Project Drawer | R2.2 | 5 | 5 | ✓ | ✓ |
| F16 | Slash Command Autocomplete (`/`) | R2.2 | 5 | 5 | ✓ | ✓ |
| F17 | Multiline Command Input (`Shift+Enter`) | R2.2 | 5 | 5 | ✓ | ✓ |
| F18 | DCC Hotkey Guard & Collision Blacklist | R2.2 | 5 | 5 | ✓ | ✓ |
| F19 | Non-Hostage 5-Step Tutorial | R2.3 | 5 | 5 | ✓ | ✓ |
| F20 | Anti-CJK Sanitizer & JSON Profiling | R2.3 | 5 | 5 | ✓ | ✓ |
| F21 | Interactive Profile Inspector (`/profilo`) | R2.3 | 5 | 5 | ✓ | ✓ |
| F22 | Deterministic 6-State Task Engine | R3.1 | 5 | 5 | ✓ | ✓ |
| F23 | ASCII Task Step Visualizer | R3.1 | 5 | 5 | ✓ | ✓ |
| F24 | Human-in-the-Loop Rollback Protocol | R3.1 | 5 | 5 | ✓ | ✓ |
| F25 | Declarative Skill Registry (YAML/JSON) | R3.2 | 5 | 5 | ✓ | ✓ |
| F26 | 3-Tier Safety Permissions Model | R3.2 | 5 | 5 | ✓ | ✓ |
| F27 | AST & Command Safety Validation Engine | R3.2 | 5 | 5 | ✓ | ✓ |
| F28 | Autonomous Skill Synthesis | R3.2 | 5 | 5 | ✓ | ✓ |
| F29 | 5-State Reactive ASCII Desktop Pet | R3.3 | 5 | 5 | ✓ | ✓ |
| F30 | Remediation of VULN-01..07 | AC 1-4 | 5 | 5 | ✓ | ✓ |

## Test Architecture
- **Test Runner Location:** `tests/test_e2e_runner.py` (and test modules under `tests/`).
- **Invocation Command:** `python -m unittest discover tests/ -p "test_*.py" -v`
- **Pass/Fail Semantics:** Exit code 0 indicates all tests pass without errors/failures.
- **Directory Layout:**
  ```
  tests/
  ├── __init__.py
  ├── test_e2e_runner.py          # Master test suite aggregator
  ├── test_tier1_features.py       # Tier 1: Feature Coverage (happy path)
  ├── test_tier2_boundaries.py     # Tier 2: Boundary, Concurrency & Corner Cases (VULN-01..07)
  ├── test_tier3_combinations.py   # Tier 3: Cross-Feature Interactions & Pairwise
  └── test_tier4_workloads.py      # Tier 4: Real-World Workload Scenarios
  ```

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | 3D Artist Heavy Render Session | F01, F08, F09, F12, F13, F29 | High |
| 2 | High-Concurrency Multi-Threaded Sync | F01, F02, F03, F04, F30 (VULN-01/02) | High |
| 3 | Long-Term Project Memory Retrieval | F05, F06, F07, F08, F30 (VULN-03/04/07) | High |
| 4 | First-Time User Onboarding & Snooze | F10, F11, F15, F19, F20, F30 (VULN-05/06) | Medium |
| 5 | Multi-Step Asset Export Task & HITL Abort | F22, F23, F24, F25, F26, F27, F29 | High |

## Coverage Thresholds
- **Tier 1:** $\ge 5$ test assertions per feature ($\ge 150$ checks).
- **Tier 2:** $\ge 5$ boundary/stress assertions per feature, strictly including 20-thread concurrency test (0% lost updates), locked/corrupted file read (0% wipeout + `.corrupt.bak`), token budget ceiling (8192), CJK filtering (`[\u4e00-\u9fff]`), and deduplication ($\ge 0.85$).
- **Tier 3:** Pairwise feature interaction tests across storage, telemetry, HUD, and task execution.
- **Tier 4:** 5 realistic end-to-end workload application scenarios.
