# Project: Cloud-First Personal Work-Life Assistant Ecosystem

## Architecture
The Cloud-First Personal Work-Life Assistant Ecosystem extends `neko_bot` and `AsciiNeko` into a multi-workstation, cloud-synchronized, persistent assistant with local-first resilience, autonomous skill creation, and real-time desktop HUD telemetry.

### Topology
- **Cloud Service (`cloud/`)**: Self-hostable HTTP/REST API server backed by a pluggable storage adapter (`SQLiteStorageAdapter` default for zero-config local testing; `PostgresStorageAdapter` for PostgreSQL 13+/Supabase). Exposes endpoints for device authentication, entity CRUD, delta pulls, and atomic bidirectional reconciliation.
- **Client Workstation (`neko_bot/`)**: Local Python CLI and background daemon (`127.0.0.1:8766`). Manages local state in `.neko/` and `workspace/`, including CRDT tasks, multi-level memory, hybrid skill registry, and an offline mutation queue (`.neko/cloud_mutations.json`).
- **Desktop Companion (`AsciiNeko`)**: HTML5 Canvas / JS frontend rendering at 60 FPS, connected to `bridge/ai_monitor.py` (`127.0.0.1:8765`). Receives real-time telemetry (Cloud Sync state, memory counts, active skills, upcoming deadlines) and renders non-intrusive HUD indicators while preserving desktop icons and Lively Wallpaper compatibility.

```
[AsciiNeko Frontend (60 FPS HUD)] <--- SSE /status --- [Bridge Server (ai_monitor.py :8765)]
                                                                  |
                                                               (reads .neko/status.json / HTTP :8766)
                                                                  v
[neko CLI / neko.bat] <--- HTTP IPC / in-process ---> [neko_bot Daemon (:8766)]
                                                                  |
                                              +-------------------+-------------------+
                                              |                   |                   |
                                              v                   v                   v
                                     [MemoryStore]         [SkillEngine]       [CloudSyncManager]
                                     workspace/memory/     workspace/skills/   .neko/cloud_mutations.json
                                                                                      |
                                                                           (HTTP REST /reconcile)
                                                                                      v
                                                                           [Cloud Server (:8767)]
                                                                           Pluggable SQLite / Postgres
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | R1.1 Architectural Audit | Deep technical audit of existing `neko_bot` and `AsciiNeko` modules, strengths, bottlenecks, and test baseline | M1 | ORIGINAL_REQUEST §R1.1 |
| 2 | R1.2 Cloud Ecosystem Blueprint | Full technical specification of cloud topology, pluggable SQL schema, security boundaries, CRDT sync protocol, and LLM fallback | M1 | ORIGINAL_REQUEST §R1.2 |
| 3 | R1.3 Operational Deployment Guide | Step-by-step runbook for cloud server deployment (SQLite local / PostgreSQL prod), multi-client pairing, and daily memory/skill usage | M1 | ORIGINAL_REQUEST §R1.3 |
| 4 | R2.1 Semantic & Fact Memory | Long-term facts, user preferences, client/colleague rules, auto-extraction and prompt injection | M2 | ORIGINAL_REQUEST §R2.1 |
| 5 | R2.2 Project & Deadline Memory | Active projects, milestones, deadlines, dependencies, risk levels, and briefing warning integration | M2 | ORIGINAL_REQUEST §R2.2 |
| 6 | R2.3 Episodic History Memory | Chronological session/decision/task history with BM25/TF-IDF token scoring and exponential recency decay | M2 | ORIGINAL_REQUEST §R2.3 |
| 7 | R2.4 Memory CLI & Status | CLI commands (`neko memory add/search/list/get/delete/stats`) and `.neko/status.json` export | M2 | ORIGINAL_REQUEST §R2.4 |
| 8 | R3.1 Hybrid Skill Representation | Declarative JSON/YAML workflows + Executable Python tools with `run(inputs, context)` interface | M3 | ORIGINAL_REQUEST §R3.1 |
| 9 | R3.2 Verification & Sandbox | Static AST security guardrails (blocking dangerous imports/dunders), subprocess isolation (sterile env, 5s timeout), self-test verification harness | M3 | ORIGINAL_REQUEST §R3.2 |
| 10 | R3.3 Autonomous Discovery & Exec | Pattern-matching trigger, auto-synthesis on novel requests in `neko do`, versioning, execution telemetry, `neko skill ...` CLI | M3 | ORIGINAL_REQUEST §R3.3 |
| 11 | R4.1 Cloud REST Server & Storage | Standalone HTTP REST server (`cloud/server.py`), pluggable storage layer (`SQLiteStorageAdapter` default, `PostgresStorageAdapter`), device auth | M4 | ORIGINAL_REQUEST §R4.1 |
| 12 | R4.2 Unified Entity Sync | CRDT LWW-Element-Set + Version Vectors sync for tasks, time logs, memories, and skills | M4 | ORIGINAL_REQUEST §R4.2 |
| 13 | R4.3 Offline Mutation Queue | Offline persistence in `.neko/cloud_mutations.json`, deterministic reconciliation upon reconnect, `neko cloud ...` CLI | M4 | ORIGINAL_REQUEST §R4.3 |
| 14 | R5.1 Bridge Telemetry Ingestion | `bridge/ai_monitor.py` telemetry aggregator reading `.neko/status.json` and `:8766`, streaming `cloud_sync`, `memory`, `skills`, `deadlines` | M5 | ORIGINAL_REQUEST §R5.1 |
| 15 | R5.2 HUD Ecosystem Indicators | Terminal HUD indicators (sync status badge, memory/skills pills, quick chips) at 60 FPS without layout breakage or desktop icon overlap | M5 | ORIGINAL_REQUEST §R5.2 |
| 16 | R-Baseline 0 Regressions | All 271 existing tests in `neko_bot/tests/` continue to pass (0 failures, 0 errors, 0 skips) | M6 | ORIGINAL_REQUEST §AC.5 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Architectural Blueprint | Author `ARCHITECTURE_CLOUD_ECOSYSTEM.md` in Italian covering audit, cloud architecture, schemas, and runbook | none | DONE (ARCHITECTURE_CLOUD_ECOSYSTEM.md verified) |
| M2 | Multi-Level Persistent Memory | Implement `neko_bot/memory/`, BM25/TF-IDF search, CLI `neko memory`, router prompt injection, status.json export | none | DONE (33 memory tests, 23 challenger search tests, 14 adversarial store tests pass) |
| M3 | Autonomous Hybrid Skills | Implement `neko_bot/skills/`, AST sandbox, subprocess runner, self-test harness, CLI `neko skill`, `neko do` auto-creation | M2 (for episodic memory logging) | DONE (30/30 sandbox, 17/17 registry, 39/39 skills pass; AST sandbox, cross-process lock, ReDoS rejection) |
| M4 | Cloud Backend & Offline Sync | Implement `cloud/` REST server, pluggable SQLite/PostgreSQL storage, `neko_bot/cloud/` sync client, offline mutation queue, CRDT LWW engine | M2, M3 | DONE (24/24 sync, 22/22 security, 18/18 concurrency, 10/10 lifecycle pass; genuine postgres & sqlite WAL, auth 401) |
| M5 | Bridge & Desktop HUD | Extend `bridge/ai_monitor.py` telemetry aggregator, `.neko/status.json` schema, `js/` HUD indicators at 60 FPS | M2, M3, M4 | DONE (37/37 integration, 7/7 stress, 69/69 combined pass; zero-I/O snapshot 0.00039ms, 60 FPS HUD dirty-checking, desktop icon clear) |
| M6 | E2E Integration & Final Hardening | Phase 1: 100% pass across E2E test suite (Tiers 1-4) + 0 regressions on all 271 existing tests. Phase 2: Tier 5 Adversarial Coverage Hardening | M1, M2, M3, M4, M5 | DONE (186/186 E2E tests across Tiers 1-5 pass, 0 regressions on baseline, 0 remaining gaps) |

## Interface Contracts

### 1. Memory Subsystem Contract (`neko_bot/memory/`)
```python
class MemoryStore:
    def __init__(self, workspace_dir: Path): ...
    def add_fact(self, content: str, category: str = "fact", tags: list[str] = None, importance: int = 3) -> dict: ...
    def add_project(self, project_name: str, milestone: str, deadline_utc: str = None, risk_level: str = "medium", dependencies: list[str] = None) -> dict: ...
    def add_episode(self, session_id: str, event_type: str, summary: str, full_text: str = "", context_tags: list[str] = None) -> dict: ...
    def search(self, query: str, level: str = "all", limit: int = 5) -> list[dict]: ...
    def list_entries(self, level: str = "semantic", project: str = None) -> list[dict]: ...
    def get_entry(self, entry_id: str) -> Optional[dict]: ...
    def delete_entry(self, entry_id: str, hard: bool = False) -> bool: ...
    def get_stats(self) -> dict: ... # {"total_facts": int, "active_projects": int, "recent_episodes": int}
    def export_crdt_entities(self) -> list[dict]: ...
    def apply_crdt_mutation(self, mutation: dict) -> bool: ...
```

### 2. Autonomous Hybrid Skills Contract (`neko_bot/skills/`)
```python
class SkillSandbox:
    def validate_ast(self, code_str: str) -> tuple[bool, str]: ...
    def run_tool_in_sandbox(self, code_str: str, inputs: dict, context: dict, timeout_sec: float = 5.0) -> dict: ...
    def verify_skill(self, skill_def: dict) -> tuple[bool, str]: ...

class SkillRegistry:
    def __init__(self, workspace_dir: Path): ...
    def register_skill(self, skill_def: dict, code_str: Optional[str] = None) -> tuple[bool, str]: ...
    def get_skill(self, skill_id: str) -> Optional[dict]: ...
    def list_skills(self) -> list[dict]: ...
    def match_skill(self, prompt: str) -> Optional[dict]: ...
    def execute_skill(self, skill_id: str, inputs: dict, context: dict) -> dict: ...
    def export_crdt_entities(self) -> list[dict]: ...
    def apply_crdt_mutation(self, mutation: dict) -> bool: ...
```

### 3. Cloud Server & Storage Adapter Contract (`cloud/`)
```python
class BaseStorageAdapter(ABC):
    @abstractmethod
    def initialize(self): ...
    @abstractmethod
    def register_device(self, device_id: str, device_name: str, client_version: str, token_hash: str) -> bool: ...
    @abstractmethod
    def authenticate_device(self, device_id: str, token: str) -> bool: ...
    @abstractmethod
    def apply_mutation(self, mutation: dict) -> tuple[bool, int]: ... # (applied, server_sequence)
    @abstractmethod
    def pull_mutations(self, since_sequence: int, exclude_device: Optional[str] = None) -> tuple[list[dict], int]: ...
    @abstractmethod
    def get_status(self) -> dict: ...

# REST Endpoints:
# GET  /api/v1/health
# POST /api/v1/auth/register
# GET  /api/v1/sync/status
# POST /api/v1/sync/push
# POST /api/v1/sync/pull
# POST /api/v1/sync/reconcile
```

### 4. Bridge & HUD Telemetry Contract
```json
{
  "cloud_sync": {
    "status": "synced",         // "synced" | "syncing" | "offline" | "error"
    "connected": true,
    "last_sync_time": "2026-10-24T00:15:00Z",
    "pending_mutations": 0,
    "cloud_server_url": "http://127.0.0.1:8767"
  },
  "memory": {
    "total_facts": 12,
    "active_projects": 3,
    "recent_episodes": 45,
    "upcoming_deadlines": [
      {"project": "CyberpunkCity", "milestone": "Beta", "due": "2026-10-28", "risk": "medium"}
    ]
  },
  "skills": {
    "total_skills": 5,
    "active_skills": ["daily_standup_pipeline", "markdown_table_formatter"],
    "last_executed_skill": "markdown_table_formatter"
  }
}
```

## Code Layout
- `ARCHITECTURE_CLOUD_ECOSYSTEM.md`: Comprehensive blueprint in Italian in repository root.
- `cloud/`:
  - `server.py`: Standalone HTTP REST server
  - `storage/`: `base.py`, `sqlite_adapter.py`, `postgres_adapter.py`
  - `models.py`: Sync schemas, DDL scripts, entity models
- `neko_bot/`:
  - `memory/`: `models.py`, `store.py`, `search.py`, `router_context.py`
  - `skills/`: `models.py`, `sandbox.py`, `registry.py`, `synthesizer.py`, `executor.py`
  - `cloud/`: `client.py`, `sync_manager.py`, `mutation_queue.py`
  - `cli.py`: Extended with `memory`, `skill`, `cloud` subparsers
  - `core/`: `daemon.py`, `router.py`, `config.py` extended additively
- `bridge/`:
  - `ai_monitor.py`: Extended with `NekoBotTelemetryAggregator`
- `js/`:
  - `hud-terminal.js`: Extended with ecosystem metrics and HUD widgets
  - `system-bridge.js`: Extended with telemetry handlers
- `tests/`:
  - `test_e2e_tier1_features.py`: Tier 1 Feature coverage tests
  - `test_e2e_tier2_boundaries.py`: Tier 2 Boundary & Corner case tests
  - `test_e2e_tier3_cross_features.py`: Tier 3 Cross-feature pairwise tests
  - `test_e2e_tier4_real_world.py`: Tier 4 Real-world application scenarios
  - `test_e2e_tier5_adversarial.py`: Tier 5 Adversarial coverage hardening
