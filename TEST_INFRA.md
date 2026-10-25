# E2E Test Infra: Cloud-First Personal Work-Life Assistant Ecosystem

## Test Philosophy
- **Opaque-box, requirement-driven**: Tests derive strictly from user requirements in `ORIGINAL_REQUEST.md`.
- **Zero Paid Dependencies**: Runs completely locally with ephemeral SQLite, local loopback sockets, temporary workspaces, and zero paid external API requirements.
- **Methodology**: 4-Tier Progressive Verification + Tier 5 Adversarial Coverage Hardening:
  - Category-Partition Method
  - Boundary Value Analysis (BVA)
  - Pairwise Combinatorial Testing
  - Real-World Workload Scenarios

## Feature Inventory
| # | Feature | Source (Requirement) | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---------|---------------------|:------:|:------:|:------:|:------:|
| 1 | R1 Architectural Blueprint | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ | ✓ |
| 2 | R2.1 Semantic & Fact Memory | ORIGINAL_REQUEST §R2.1 | 5 | 5 | ✓ | ✓ |
| 3 | R2.2 Project & Deadline Memory | ORIGINAL_REQUEST §R2.2 | 5 | 5 | ✓ | ✓ |
| 4 | R2.3 Episodic History Memory | ORIGINAL_REQUEST §R2.3 | 5 | 5 | ✓ | ✓ |
| 5 | R2.4 Memory CLI & Status Export | ORIGINAL_REQUEST §R2.4 | 5 | 5 | ✓ | ✓ |
| 6 | R3.1 Hybrid Skill Representation | ORIGINAL_REQUEST §R3.1 | 5 | 5 | ✓ | ✓ |
| 7 | R3.2 Verification & Sandbox | ORIGINAL_REQUEST §R3.2 | 5 | 5 | ✓ | ✓ |
| 8 | R3.3 Autonomous Discovery & Exec | ORIGINAL_REQUEST §R3.3 | 5 | 5 | ✓ | ✓ |
| 9 | R4.1 Cloud REST Server & Storage | ORIGINAL_REQUEST §R4.1 | 5 | 5 | ✓ | ✓ |
| 10 | R4.2 Unified Entity Sync | ORIGINAL_REQUEST §R4.2 | 5 | 5 | ✓ | ✓ |
| 11 | R4.3 Offline Mutation Queue | ORIGINAL_REQUEST §R4.3 | 5 | 5 | ✓ | ✓ |
| 12 | R5.1 Bridge Telemetry Ingestion | ORIGINAL_REQUEST §R5.1 | 5 | 5 | ✓ | ✓ |
| 13 | R5.2 HUD Ecosystem Indicators | ORIGINAL_REQUEST §R5.2 | 5 | 5 | ✓ | ✓ |
| 14 | R-Baseline 0 Regressions on 271 Tests | ORIGINAL_REQUEST §AC.5 | 5 | 5 | ✓ | ✓ |

## Test Architecture
- **Runner**: Python `unittest` framework.
- **Execution Command**:
  ```powershell
  python -m unittest discover -s tests -p "test_e2e_*.py" -v
  ```
- **Hermetic Isolation**:
  - Ephemeral ports (`tests.conftest.get_free_port()`)
  - Isolated temporary workspaces (`tempfile.TemporaryDirectory()`)
  - Sterile subprocess environments (`PYTHONPATH`, `SYSTEMROOT`)

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Multi-Workstation Workday Sync | R2 (Memories), R3 (Skills), R4 (Cloud Sync, Offline Reconnect) | High |
| 2 | Autonomous Skill Discovery from Prompt | R3 (AST Sandbox, Auto-Synthesis, Self-Tests), R2 (Episodic Log) | High |
| 3 | Project Deadline Crunch & Risk Escalation | R2 (Deadlines), R5 (HUD & Bridge Telemetry), neko briefing | Medium |
| 4 | Offline Commute with Conflict Resolution | R4 (Offline Queue, CRDT LWW tie-break), R2, TaskStore | High |
| 5 | Companion Live HUD Telemetry Streaming | R5 (Bridge Telemetry, Status SSE, 60 FPS Canvas), R2, R4 | Medium |

## Coverage Thresholds
- Tier 1: ≥5 test cases per feature (14 features × 5 = ≥70 tests)
- Tier 2: ≥5 boundary & corner cases per feature (14 features × 5 = ≥70 tests)
- Tier 3: ≥14 cross-feature interaction tests (pairwise)
- Tier 4: ≥5 realistic end-to-end application scenarios
- Tier 5: Adversarial edge cases and stress testing
