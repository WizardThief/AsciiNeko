# Project: AsciiNeko Golden Architecture (Phases 1, 2, 3)

## Architecture
AsciiNeko is a local-first Windows desktop AI assistant built with `pywebview`, local Ollama (`qwen2.5:7b`), and a 60 FPS interactive ASCII cat companion.
The architecture consists of:
1. **Core Backend (`bridge/`)**:
   - `ai_monitor.py`: Orchestrates HTTP server (`127.0.0.1:8765`), SSE streaming (`/stream`), Ollama client, SQLite-WAL database (`neko_storage.db`), Atomic JSON engine, BM25 retrieval, Token Budget Manager, Win32 hardware telemetry (`nvml.dll`, `user32.GetLastInputInfo`, foreground window tracker), and Task Engine FSM.
   - `app_window.py`: Launches `pywebview` window, system tray icon, and global hotkeys (`Ctrl+Alt+N` / `Alt+N`) with DCC hotkey collision guard.
   - `data/`: Active storage directory holding `neko_storage.db`, atomic JSON files (`profile.json`, `projects.json`, `archive.json`, `chat_history.json`), and declarative skills in `data/skills/`.
2. **Frontend HUD (`web/` / root)**:
   - `index.html`: Main HUD layout, Top Bar 4-channel telemetry, Color-Safe toggle, unified 360px Slide-out Drawer, Command Bar, Tutorial modal, and ASCII canvas.
   - `css/terminal.css`: Cascadia Code / Consolas monospace typography ($\ge 13$px), Color-Safe styling (`.color-safe`), drawer layout, ASCII dock styling.
   - `js/hud-terminal.js`: HUD controller, slide-out drawer tabs, slash-command autocomplete, multiline input, Tutorial escape hatch, Profile Inspector (`/profilo`), and ASCII Task Step Visualizer dock.
   - `js/system-bridge.js`: SSE consumer and REST client for `127.0.0.1:8765`.
   - `js/companion-face.js` & `js/ascii-renderer.js`: 60 FPS ASCII canvas renderer with 5 reactive states (`IDLE`, `THINKING`, `RUNNING`, `ALERT`, `SLEEPING`).

## Feature Inventory
Every feature from the survey is inventoried below with its assigned milestone:

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F01 | SQLite-WAL Storage Engine | Primary ACID relational & FTS5 store (`neko_storage.db`) with WAL, normal sync, 5s busy timeout | M1 | Survey F01 |
| F02 | Transactional Atomic JSON Writer | Thread/process-safe JSON persistence with RLock, uuid4 tmp, msvcrt lock, backoff replace | M1 | Survey F02 |
| F03 | Safe Atomic JSON Reader with `.corrupt.bak` | Reader handling file locks safely and creating `.corrupt.bak` on decode failures without wipeout | M1 | Survey F03 |
| F04 | Non-Destructive Data Migration | Auto-migration at startup from `bridge/data/*.json` to `neko_storage.db` with 100% data preservation | M1 | Survey F04 |
| F05 | FTS5 Lexical Retrieval with Time-Decay | SQLite FTS5 BM25 search combined with exponential time-decay ($0.50 \cdot BM25 + 0.35 \cdot Imp \cdot e^{-\lambda \Delta t} + 0.15 \cdot Proj$) | M1 | Survey F05 |
| F06 | Pinned Rule Exemption ($\lambda = 0$) | Pinned memories (`pinned=1`) have $\lambda = 0$, guaranteeing they never decay over time | M1 | Survey F06 |
| F07 | Hybrid Deduplication Engine | Prevents duplicate memories using Levenshtein and Token-Set Jaccard similarity ($\ge 0.85$) | M1 | Survey F07 |
| F08 | Deterministic Token Budget Manager | Explicitly passes `num_ctx: 8192` to Ollama and guarantees $\ge 5742$ tokens headroom; preserves pinned traits | M1 | Survey F08 |
| F09 | Dynamic VRAM Monitor & Ollama Unload | Monitors GPU/VRAM (`nvml.dll`), unloads Ollama (`keep_alive: 0`) if VRAM $> 85\%$ or DCC 3D active | M1 | Survey F09 |
| F10 | Monospace Typography Overhaul | Font stack `'Cascadia Code', 'Consolas', 'JetBrains Mono', monospace` with min size $\ge 13$px | M2 | Survey F10 |
| F11 | Color-Safe Mode Toggle | Disables CRT vignette radial gradient and scanlines via Top Bar button `🎨` and `/colorsafe` command | M2 | Survey F11 |
| F12 | 4-Channel Hardware Telemetry | Real-time Top Bar monitoring of CPU %, RAM %, GPU %, VRAM used/total GB with color thresholds | M2 | Survey F12 |
| F13 | Active Window Project Time-Tracking | Win32 foreground window inspection extracting active app, document title, and project time | M2 | Survey F13 |
| F14 | 5-Minute AFK Inactivity Detector | Win32 `GetLastInputInfo` idle detection, pauses project time tracking after 300s of inactivity | M2 | Survey F14 |
| F15 | Unified Slide-Out Task & Project Drawer | 360px wide collapsible drawer on right side replacing 3 redundant To-Do views, ASCII cat visible | M2 | Survey F15 |
| F16 | Slash Command Autocomplete (`/`) | Interactive autocomplete popover in terminal command dock filtering skills/commands on `/` | M2 | Survey F16 |
| F17 | Multiline Command Input (`Shift+Enter`) | Command input supporting line breaks on `Shift+Enter` and submission on `Enter` | M2 | Survey F17 |
| F18 | DCC Hotkey Guard & Collision Blacklist | Protects DCC shortcuts (`Alt+Q`, `Alt+Z`, `Ctrl+Space`); preserves `Alt+N` / `Ctrl+Alt+N` | M2 | Survey F18 |
| F19 | Non-Hostage 5-Step Tutorial | Onboarding flow with 5 questions, explicit `"⏱ Rimanda a dopo"` button, and offline Ollama fallback | M2 | Survey F19 |
| F20 | Anti-CJK Sanitizer & JSON Profiling | Enforces `"format": "json"` and strips CJK tokens (`[\u4e00-\u9fff]`) from profiling responses | M2 | Survey F20 |
| F21 | Interactive Profile Inspector (`/profilo`) | Transparent management table with 1-click `[📌 Pinna]`, `[✏ Modifica]`, `[🗑 Elimina]` | M2 | Survey F21 |
| F22 | Deterministic 6-State Task Engine | FSM tracking `QUEUED`, `RUNNING`, `WAITING_INPUT`, `RETRY`, `COMPLETED`, `FAILED`/`ABORTED` in SQLite-WAL | M3 | Survey F22 |
| F23 | ASCII Task Step Visualizer | Dock indicator `[■■■■■■□□] % • Step X/Y • Elapsed • [✕ Annulla / Esc]` with instant kill switch | M3 | Survey F23 |
| F24 | Human-in-the-Loop Rollback Protocol | Recovery prompt on failure/abort preventing accidental deletion of rendered assets | M3 | Survey F24 |
| F25 | Declarative Skill Registry (YAML/JSON) | Standardized skill definitions in `bridge/data/skills/` with triggers, parameters, contracts | M3 | Survey F25 |
| F26 | 3-Tier Safety Permissions Model | Formal classification of skills into `READ_ONLY`, `CONFIRM_ONCE`, `DESTRUCTIVE_REQUIRE_AUTH` | M3 | Survey F26 |
| F27 | AST & Command Safety Validation Engine | Python AST parser and regex validator blocking dangerous system calls and shell escapes | M3 | Survey F27 |
| F28 | Autonomous Skill Synthesis | Detects repeated user workflows ($\ge 3$ times in 7 days) and offers 1-click `[✨ Crea Skill]` | M3 | Survey F28 |
| F29 | 5-State Reactive ASCII Desktop Pet | Expressive ASCII cat state engine mapping system events to `IDLE`, `THINKING`, `RUNNING`, `ALERT`, `SLEEPING` | M3 | Survey F29 |
| F30 | Remediation of VULN-01..07 | Comprehensive fix and test verification for all 7 documented vulnerabilities | M1, M2, M3, M4 | Survey F30 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M-E2E | E2E Testing Suite Track | Opaque-box 4-tier test runner and tests covering F01-F30, VULN-01..07 | none | DONE |
| M1 | Storage Core & Retrieval (Phase 1) | SQLite-WAL, Atomic JSON Store, FTS5 BM25 time-decay, Deduplication, Token Budget, VRAM Guard (F01-F09, F30) | none | DONE |
| M2 | HUD Ergonomics, Profiler & Drawer (Phase 2) | Typography, Color-Safe, 4-ch Telemetry, Window/AFK tracking, 360px Drawer, Autocomplete, Hotkey Guard, Tutorial, Anti-CJK, Profile Inspector (F10-F21) | M1 contracts | DONE |
| M3 | Task Engine & Reactive System (Phase 3) | 6-State Task Engine, ASCII Step Visualizer, HITL Rollback, Skill Registry, 3 Safety Tiers, AST Validation, Autonomous Synthesis, 5-State Cat (F22-F29) | M1, M2 | DONE |
| M4 | Final Acceptance & Hardening | Phase 1: 100% E2E test pass; Phase 2: Adversarial Coverage Hardening; Forensic Integrity Audit; Clean App Startup; Git commit & push | M-E2E, M1, M2, M3 | IN_PROGRESS |

## Interface Contracts
### Storage & Memory (`bridge/storage_manager.py` ↔ `bridge/ai_monitor.py`)
- `StorageManager(db_path: str, data_dir: str)`:
  - `atomic_save_json(path: str, data: Any) -> bool`: Thread/process-safe atomic write using RLock, uuid4 temp file, msvcrt locking, exponential backoff os.replace.
  - `atomic_load_json(path: str, default: Any = None) -> Any`: Safe read; handles file locks; on corrupt JSON creates `.corrupt.bak` and raises `StorageCorruptError` or returns safe state.
  - `migrate_legacy_data() -> Dict[str, int]`: Migrates `memory.json`, `todo.json`, `projects.json`, `archive.json`, `chat_history.json` into SQLite without data loss.
  - `search_memories(query: str, project_id: Optional[str] = None, limit: int = 12) -> List[Dict]`: FTS5 BM25 combined with time-decay scoring.
  - `upsert_memory(text: str, category: str, project_id: Optional[str], importance: float, pinned: bool) -> str`: Hybrid deduplication ($\ge 0.85$), inserts or updates.
  - `pin_memory(memory_id: str, pinned: bool) -> bool`: Sets `pinned = 1` ($\lambda = 0$).
  - `delete_memory(memory_id: str) -> bool`: Removes memory and updates FTS index.
  - `build_context_prompt(query: str, active_project: Optional[str], active_dcc: Optional[str]) -> Tuple[str, int]`: Returns formatted prompt within `num_ctx: 8192` budget.

### Telemetry & VRAM Guard (`bridge/telemetry.py` ↔ `bridge/ai_monitor.py`)
- `get_hardware_telemetry() -> Dict[str, Any]`: Returns `{"cpu_percent": float, "ram_percent": float, "gpu_percent": float, "vram_used_gb": float, "vram_total_gb": float, "vram_percent": float}`.
- `get_active_window_info() -> Dict[str, Any]`: Returns `{"app_name": str, "window_title": str, "project_name": str, "is_dcc": bool}`.
- `get_afk_idle_seconds() -> float`: Returns seconds since last user input via `user32.GetLastInputInfo`.
- `check_vram_and_unload_ollama(threshold_percent: float = 85.0) -> bool`: Unloads Ollama VRAM (`keep_alive: 0`) if threshold exceeded or heavy DCC rendering active.

### Task Engine & Skill Registry (`bridge/task_engine.py`, `bridge/skill_registry.py` ↔ `bridge/ai_monitor.py`)
- `TaskEngine`:
  - `create_task(title: str, steps: List[Dict], skill_id: Optional[str]) -> str`: Returns `task_id`.
  - `get_task(task_id: str) -> Dict`: Returns task state and step execution logs.
  - `abort_task(task_id: str) -> bool`: Transitions task to `ABORTED`.
  - `execute_next_step(task_id: str) -> Dict`: Advances FSM (`QUEUED -> RUNNING -> ...`).
- `SkillRegistry`:
  - `load_skills() -> List[Dict]`: Loads skills from `bridge/data/skills/` and built-ins.
  - `validate_skill(skill_data: Dict) -> Tuple[bool, str]`: AST and command safety check.
  - `execute_skill(skill_id: str, params: Dict, confirmed: bool = False) -> Dict`: Respects 3 safety tiers.

### HTTP / SSE Endpoints (`bridge/ai_monitor.py` ↔ `js/system-bridge.js`)
- `GET /stream`: SSE stream broadcasting telemetry (CPU, RAM, GPU, VRAM), active window/project, AFK state, active task step progress, and ASCII cat reactive state.
- `GET /status`: JSON fallback returning telemetry and active state.
- `POST /api/task/abort`: Aborts current running task.
- `POST /api/profile/pin`, `/api/profile/edit`, `/api/profile/delete`: Profile Inspector operations.
- `GET /api/skills`: Returns list of available skills for `/` autocomplete.
- `POST /api/colorsafe`: Toggles Color-Safe mode state.

## Code Layout
- `bridge/storage_manager.py`: SQLite-WAL and Atomic JSON Store implementation.
- `bridge/retrieval.py`: FTS5 BM25, time-decay scoring, deduplication, and Token Budget Manager.
- `bridge/telemetry.py`: NVML GPU/VRAM monitoring, Win32 window tracking, and AFK detector.
- `bridge/task_engine.py`: Deterministic 6-state Task Engine with SQLite persistence.
- `bridge/skill_registry.py`: Declarative Skill Registry, safety tiers, and AST validator.
- `bridge/ai_monitor.py`: Central integration and HTTP server routing.
- `bridge/app_window.py`: Pywebview window, hotkey collision guard, tray icon.
- `index.html`: HUD template with Drawer, Telemetry widgets, Color-Safe toggle.
- `css/terminal.css`: Cascadia Code monospace styles, Color-Safe styles, Drawer styles.
- `js/hud-terminal.js`: Unified drawer, autocomplete, tutorial snooze, task visualizer.
- `js/system-bridge.js`: SSE consumer, 4-channel telemetry ingest, API calls.
- `js/companion-face.js`: 5 reactive ASCII cat animation states.
- `tests/`: Automated unit and E2E test suites executed via `python -m unittest`.
