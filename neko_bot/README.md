# neko_bot

Local-first AI personal productivity bot, CLI harness, and background daemon on Windows powered by local Ollama models.

## Features

- **Dynamic Resource Adaptation**: Automatically detects heavy creative applications (`UnrealEditor.exe`, `blender.exe`, `Photoshop.exe`, `3dsmax.exe`, `afterfx.exe`, `premiere.exe`, `resolve.exe`, `vray.exe`, `corona.exe`) and high CPU/RAM load. Switches to lightweight models with immediate VRAM unloading (`keep_alive: 0`) or deterministic zero-VRAM parsing to ensure 3D viewport and rendering performance is never impacted.
- **Fast Deterministic Fallback**: Strictly guarantees < 2.0s SLA response time even when Ollama is offline or unpulled.
- **Background Daemon**: Detached Windows daemon process (`DETACHED_PROCESS | CREATE_NO_WINDOW`) with loopback IPC HTTP server (`127.0.0.1:8766`) and atomic `.neko/status.json` caching for companion applications (such as `AsciiNeko`).
- **Resilient CLI**: `neko status --json`, `neko daemon start/stop/status/restart`, and `neko do "<prompt>"` with instant in-process fallback when daemon is stopped.

## CLI Usage

```cmd
:: Check system status in human-readable format
neko status

:: Query JSON status for external companion applications
neko status --json

:: Manage daemon lifecycle
neko daemon start
neko daemon status
neko daemon stop
neko daemon restart

:: Execute a natural language task or prompt
neko do "Modellare prop sedia #cyberpunk !alta"
```

## Running Tests

```powershell
python -m unittest tests/test_cli_daemon.py
python -m unittest discover -s tests -p "test_*.py"
```
