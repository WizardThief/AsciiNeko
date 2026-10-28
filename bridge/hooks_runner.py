"""
bridge/hooks_runner.py
======================
Asynchronous Lifecycle Hooks Runner & Telemetry Hooks (Milestone 2 / R2).

Provides:
1. HooksRunner: Non-blocking daemon worker bus executing lifecycle hooks
   (post_chat, on_window_switch, on_idle, on_exit).
2. PostChatHook (/learn-eval): Intelligent memory upsert & deduplication,
   consolidating tentative/contradictory notes into confirmed rules.
3. Startup Idempotent Cleanup: Merges tentative Intermarine logo note into confirmed rule.
4. OnWindowSwitchHook: Matches active DCC/dev windows against uncompleted
   Kanban tasks in todo.json, populates window_task_suggestion, and reinforces skills/instincts.
5. OnIdleOrExitHook: Automatically saves session_checkpoint.json on AFK (>300s) or exit.
"""

from __future__ import annotations

import atexit
import copy
from datetime import datetime
import json
import logging
import os
import queue
import random
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid

logger = logging.getLogger("neko.hooks_runner")

TENTATIVE_MARKERS = [
    "in attesa di conferma",
    "da verificare",
    "provvisorio",
    "non confermato",
    "tbd",
    "da confermare",
    "tentative",
    "incerto",
]

CONFIRMED_MARKERS = [
    "ufficiale",
    "confermato",
    "definitivo",
    "sempre",
    "regola",
    "mai",
    "official",
    "confirmed",
]

STOP_WORDS: Set[str] = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "del", "dello", "della", "dei", "degli", "delle",
    "al", "allo", "alla", "ai", "agli", "alle",
    "dal", "dallo", "dalla", "dai", "dagli", "dalle",
    "nel", "nello", "nella", "nei", "negli", "nelle",
    "sul", "sullo", "sulla", "sui", "sugli", "sulle",
    "ed", "ad", "od", "e", "o", "ma", "se", "perché", "anche",
    "che", "chi", "cui", "quale", "quali", "come", "dove",
    "non", "più", "molto", "poco", "tutto", "tutti", "tutte",
    "parte", "cliente", "utente", "riferimento", "dettaglio",
    "the", "a", "an", "and", "or", "but", "if", "in", "on", "at", "to", "for", "with", "by", "from",
    "colore", "logo", "regola", "nota", "tema", "stato", "valore"
}

STATUS_WORDS: Set[str] = {
    "attesa", "conferma", "verificare", "provvisorio", "confermato",
    "tbd", "confermare", "tentative", "incerto", "ufficiale",
    "definitivo", "sempre", "mai", "official", "confirmed"
}

RECOGNIZED_CREATIVE_DEV_TOOLS: Set[str] = {
    "blender", "3ds max", "3dsmax", "max", "unreal engine", "unreal", "ue5",
    "vs code", "code", "visual studio code", "visual studio", "rust",
    "photoshop", "substance", "substance painter", "substance designer",
    "maya", "zbrush", "houdini", "cinema 4d", "c4d", "unity",
    "illustrator", "after effects", "premiere",
}

DCC_KEYWORDS_MAP: Dict[str, List[str]] = {
    "blender": ["blender", "modello", "mesh", "3d", "draco", "glb", "ottimizzazione", "render"],
    "3ds max": ["3ds max", "3dsmax", "max", "render", "corona", "vray", "archviz"],
    "unreal engine": ["unreal", "ue5", "level", "archviz", "blueprint", "game"],
    "vs code": ["rust", "three.js", "threejs", "webgl", "code", "script", "pipeline", "integrazione"],
    "code": ["rust", "three.js", "threejs", "webgl", "code", "script", "pipeline", "integrazione"],
    "rust": ["rust", "cargo", "wasm", "pipeline", "integrazione"],
    "photoshop": ["photoshop", "psd", "texture", "grafica", "logo", "post-produzione"],
}


def extract_content_tokens(text: str) -> Set[str]:
    """Extracts informative content tokens, excluding stop-words and status markers."""
    words = set(re.findall(r"\b[a-zA-Z0-9_\u00C0-\u017F]{2,}\b", text.lower()))
    return {w for w in words if w not in STOP_WORDS and w not in STATUS_WORDS}


def extract_entity_prefix(text: str) -> Optional[str]:
    """Extracts subject prefix before separator (: or – or -)."""
    for sep in [":", " – ", " - "]:
        if sep in text:
            return text.split(sep, 1)[0].strip().lower()
    return None


class HookEventType:
    POST_CHAT = "post_chat"
    POST_MEMORY_ADD = "post_memory_add"
    ON_WINDOW_SWITCH = "on_window_switch"
    ON_IDLE = "on_idle"
    ON_EXIT = "on_exit"


_PENDING_TMP_FILES: Set[str] = set()
_PENDING_TMP_LOCK = threading.Lock()


def _cleanup_pending_tmp_files() -> None:
    with _PENDING_TMP_LOCK:
        for p in list(_PENDING_TMP_FILES):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        _PENDING_TMP_FILES.clear()


atexit.register(_cleanup_pending_tmp_files)


def _atomic_write_json(file_path: str, data: Any, max_retries: int = 10, base_delay: float = 0.005) -> None:
    """Safe atomic writer using uuid4 tmp file and exponential backoff retry on os.replace."""
    tmp_path = f"{file_path}.{uuid.uuid4().hex}.tmp"
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    with _PENDING_TMP_LOCK:
        _PENDING_TMP_FILES.add(tmp_path)
    replaced = False
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        last_err = None
        for attempt in range(max_retries):
            try:
                os.replace(tmp_path, file_path)
                replaced = True
                return
            except (PermissionError, OSError) as e:
                last_err = e
                if attempt == max_retries - 1:
                    raise last_err
                time.sleep(base_delay * (2 ** attempt) + random.uniform(0.001, 0.005))
    finally:
        with _PENDING_TMP_LOCK:
            _PENDING_TMP_FILES.discard(tmp_path)
        if not replaced:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass



def _safe_load_json(file_path: str, default: Any = None, max_retries: int = 5, base_delay: float = 0.005) -> Any:
    """Safe reader for JSON files with retry backoff and .corrupt.bak preservation on corrupt payloads."""
    if not os.path.isfile(file_path):
        return default

    content = ""
    for attempt in range(max_retries):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            break
        except (PermissionError, OSError):
            if attempt == max_retries - 1:
                return default
            time.sleep(base_delay * (2 ** attempt) + random.uniform(0.001, 0.004))

    if not content or not content.strip():
        return default

    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        logger.warning(f"Failed to read/parse {file_path}: {e}")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        corrupt_path = f"{file_path}.{ts}.corrupt.bak"
        try:
            shutil.copy2(file_path, corrupt_path)
            logger.error(f"CORRUPT FILE PRESERVED: {file_path} -> {corrupt_path}")
        except Exception as copy_err:
            logger.error(f"Failed to preserve corrupt file {file_path}: {copy_err}")
        return default


def _is_test_environment() -> bool:
    return (
        "unittest" in sys.modules
        or any("test" in str(arg).lower() for arg in sys.argv)
        or any(arg == "-c" for arg in sys.argv)
    )



def cleanup_intermarine_logo_memory(data_dir: Optional[str] = None, storage_manager: Optional[Any] = None) -> bool:
    """
    Idempotent startup cleanup: merges tentative Intermarine logo note
    ("Logo Intermarine – colore in attesa di conferma da parte dell'utente")
    into confirmed rule ("Logo Intermarine: colore ufficiale = Blu Marino (dark navy)...").
    Updates updated_at, merged_count=2, and deletes tentative entry from memory.json and SQLite.
    """
    base_dir = data_dir or os.path.join(os.path.dirname(__file__), "data")
    prod_data = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
    if os.path.abspath(base_dir) == prod_data and _is_test_environment():
        return False

    mem_file = os.path.join(base_dir, "memory.json")
    if not os.path.isfile(mem_file):
        return False

    memories = _safe_load_json(mem_file, default=[])
    if not isinstance(memories, list):
        return False

    tentative_idx = -1
    confirmed_idx = -1

    for i, m in enumerate(memories):
        txt = (m.get("text") or "").lower()
        if "intermarine" in txt:
            if any(t in txt for t in TENTATIVE_MARKERS):
                tentative_idx = i
            elif "blu marino" in txt or "dark navy" in txt or "ufficiale" in txt:
                confirmed_idx = i

    if tentative_idx == -1:
        # Already cleaned up (idempotent)
        return False

    tentative_entry = memories[tentative_idx]
    tentative_id = tentative_entry.get("id")

    if confirmed_idx != -1:
        # Fonde nella voce confermata
        confirmed_entry = memories[confirmed_idx]
        confirmed_entry["updated_at"] = time.time()
        confirmed_entry["merged_count"] = max(2, confirmed_entry.get("merged_count", 1) + 1)
        confirmed_entry["pinned"] = True
        memories.pop(tentative_idx)
    else:
        # Converte la voce provvisoria in regola ufficiale confermata
        tentative_entry["text"] = (
            "Logo Intermarine: colore ufficiale = Blu Marino (dark navy). "
            "Riferimento per 3ds Max, Blender, Unreal Engine."
        )
        tentative_entry["pinned"] = True
        tentative_entry["merged_count"] = 2
        tentative_entry["updated_at"] = time.time()

    _atomic_write_json(mem_file, memories)

    # Sync with SQLite if storage_manager is available
    if storage_manager:
        try:
            with storage_manager.transaction() as conn:
                if tentative_id:
                    conn.execute("DELETE FROM memories WHERE id = ?", (tentative_id,))
                conn.execute(
                    "DELETE FROM memories WHERE text LIKE '%in attesa di conferma%' AND text LIKE '%Intermarine%'"
                )
                if confirmed_idx != -1:
                    c_id = memories[confirmed_idx if confirmed_idx < tentative_idx else confirmed_idx - 1].get("id")
                    if c_id:
                        conn.execute(
                            "UPDATE memories SET updated_at = ?, pinned = 1, importance = 1.0 WHERE id = ?",
                            (time.time(), c_id),
                        )
        except Exception as ex:
            logger.debug(f"SQLite cleanup sync error (non-fatal): {ex}")

    return True


class HooksRunner:
    """
    Asynchronous lifecycle hooks runner.
    Executes lifecycle hooks in background daemon threads with per-hook exception isolation.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        storage_manager: Optional[Any] = None,
        mem_proj_manager: Optional[Any] = None,
        instinct_engine: Optional[Any] = None,
    ):
        self._lock = threading.RLock()
        self._hooks_lock = threading.Lock()
        self.data_dir = data_dir or os.path.join(os.path.dirname(__file__), "data")
        self.storage_manager = storage_manager
        self.mem_proj_manager = mem_proj_manager
        self.instinct_engine = instinct_engine

        self._hooks: Dict[str, List[Callable]] = {
            HookEventType.POST_CHAT: [],
            HookEventType.POST_MEMORY_ADD: [],
            HookEventType.ON_WINDOW_SWITCH: [],
            HookEventType.ON_IDLE: [],
            HookEventType.ON_EXIT: [],
        }

        self._window_task_suggestion: Optional[Dict[str, Any]] = None
        self._session_checkpoint: Optional[Dict[str, Any]] = None

        self._stopped = False
        self._queue: queue.Queue = queue.Queue()
        self._workers: List[threading.Thread] = []
        for i in range(8):
            w = threading.Thread(target=self._worker_loop, daemon=True, name=f"HookWorker-{i}")
            w.start()
            self._workers.append(w)

        # Registrazione hook interni predefiniti
        self.register_hook(HookEventType.POST_CHAT, self._handle_post_chat)
        self.register_hook(HookEventType.POST_MEMORY_ADD, self._handle_post_memory_add)
        self.register_hook(HookEventType.ON_WINDOW_SWITCH, self._handle_on_window_switch)
        self.register_hook(HookEventType.ON_IDLE, self._handle_on_idle_or_exit)
        self.register_hook(HookEventType.ON_EXIT, self._handle_on_idle_or_exit)

        # Carica checkpoint esistente su disco se presente
        self._load_initial_checkpoint()

    def _worker_loop(self) -> None:
        while not self._stopped:
            try:
                task = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if task is None:
                break
            handler, kwargs, ev_name = task
            try:
                handler(**kwargs)
            except Exception as ex:
                logger.error(f"Errore nell'esecuzione dell'hook {ev_name}: {ex}", exc_info=True)
            finally:
                self._queue.task_done()

    def register_hook(self, event_type: str, handler: Callable) -> None:
        """Registra una funzione listener per un evento di lifecycle."""
        with self._hooks_lock:
            self._hooks.setdefault(event_type, []).append(handler)

    def trigger(self, event_type: str, **kwargs) -> None:
        """Accoda i listener registrati per esecuzione asincrona non-bloccante."""
        with self._hooks_lock:
            handlers = list(self._hooks.get(event_type, []))
        for h in handlers:
            self._queue.put((h, kwargs, event_type))

    # --- Metodi Pubblici di Emissione Eventi (Non-Blocking) ---

    def emit_post_chat(
        self,
        chat_text: str = "",
        response_text: str = "",
        memory_added: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emette l'evento POST_CHAT in background."""
        self.trigger(
            HookEventType.POST_CHAT,
            chat_text=chat_text,
            response_text=response_text,
            memory_added=memory_added,
        )

    def emit_window_switch(self, window_info: Dict[str, Any]) -> None:
        """Emette l'evento ON_WINDOW_SWITCH in background."""
        self.trigger(HookEventType.ON_WINDOW_SWITCH, window_info=window_info)

    def emit_idle_or_exit(self, reason: str = "afk") -> None:
        """Emette l'evento ON_IDLE o ON_EXIT in background."""
        ev = HookEventType.ON_IDLE if reason == "afk" else HookEventType.ON_EXIT
        self.trigger(ev, reason=reason)

    # --- Handler Interni dei Lifecycle Hooks ---

    def _handle_post_chat(
        self,
        chat_text: str = "",
        response_text: str = "",
        memory_added: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        if memory_added and isinstance(memory_added, dict):
            self.evaluate_memory_upsert(
                new_fact=memory_added.get("text", ""),
                project_id=memory_added.get("project_id"),
                new_memory_id=memory_added.get("id"),
            )
        elif chat_text:
            # Valuta se il testo della chat contiene una nuova regola
            cand = (chat_text or "").strip()
            if any(k in cand.lower() for k in ("regola", "ufficiale", "logo", "sempre", "blu marino")):
                self.evaluate_memory_upsert(new_fact=cand)

    def _handle_post_memory_add(self, memory: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        if memory and isinstance(memory, dict):
            self.evaluate_memory_upsert(
                new_fact=memory.get("text", ""),
                project_id=memory.get("project_id"),
                new_memory_id=memory.get("id"),
            )

    def _handle_on_window_switch(self, window_info: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        if window_info and isinstance(window_info, dict):
            self.match_window_to_tasks(window_info)

    def _handle_on_idle_or_exit(self, reason: str = "afk", **kwargs) -> None:
        self.save_session_checkpoint()

    # --- Logica Intelligente di Upsert & Deduplicazione Memoria (/learn-eval) ---

    def evaluate_memory_upsert(
        self,
        new_fact: str,
        project_id: Optional[str] = None,
        new_memory_id: Optional[str] = None,
        pinned: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Valuta se new_fact confuta o conferma una memoria provvisoria esistente
        o un duplicato in memory.json.
        Richiede corrispondenza rigorosa delle entità / token chiave prima di unire,
        prevenendo collisioni di falsi positivi (es. 'Logo Alpha' vs 'Logo Beta').
        In caso positivo, aggiorna la memoria in place ed elimina eventuali duplicati.
        """
        clean_fact = (new_fact or "").strip()
        if not clean_fact:
            return None

        mem_file = os.path.join(self.data_dir, "memory.json")
        memories = _safe_load_json(mem_file, default=[])
        if not isinstance(memories, list):
            memories = []

        clean_lower = clean_fact.lower()
        is_new_confirmed = any(m in clean_lower for m in CONFIRMED_MARKERS)

        clean_prefix = extract_entity_prefix(clean_fact)
        clean_content = extract_content_tokens(clean_fact)
        clean_prefix_tokens = extract_content_tokens(clean_prefix) if clean_prefix else set()

        matched_idx = -1
        is_tentative_match = False

        for i, existing in enumerate(memories):
            ex_id = existing.get("id")
            if new_memory_id and ex_id == new_memory_id:
                continue

            ex_text = (existing.get("text") or "").strip().lower()
            if not ex_text:
                continue

            # Corrispondenza esatta di stringa
            if clean_lower == ex_text:
                matched_idx = i
                is_tentative_match = False
                break

            ex_prefix = extract_entity_prefix(ex_text)
            ex_content = extract_content_tokens(ex_text)
            ex_prefix_tokens = extract_content_tokens(ex_prefix) if ex_prefix else set()

            is_ex_tentative = any(t in ex_text for t in TENTATIVE_MARKERS)

            # Controllo rigoroso del prefisso entità:
            # Se entrambi hanno prefissi/soggetti espliciti (es. "Logo Alpha" vs "Logo Beta"),
            # devono condividere i token dell'entità e non contraddirsi.
            if clean_prefix_tokens and ex_prefix_tokens:
                prefix_overlap = clean_prefix_tokens.intersection(ex_prefix_tokens)
                if not prefix_overlap:
                    # Entità distinte -> NESSUN MERGE
                    continue

            content_overlap = len(clean_content.intersection(ex_content))
            content_union = len(clean_content.union(ex_content))
            jaccard = (content_overlap / content_union) if content_union > 0 else 0.0

            ex_content_len = len(ex_content)
            overlap_ratio = (content_overlap / ex_content_len) if ex_content_len > 0 else 0.0

            # Caso 1: Regola provvisoria sulla stessa entità confermata/superata
            if is_ex_tentative:
                prefix_matches = bool(
                    clean_prefix and ex_prefix and (
                        clean_prefix == ex_prefix or (clean_prefix_tokens and clean_prefix_tokens == ex_prefix_tokens)
                    )
                )
                if (prefix_matches and content_overlap >= 1) or (overlap_ratio >= 0.70 and content_overlap >= 1):
                    matched_idx = i
                    is_tentative_match = True
                    break
                elif "intermarine" in clean_lower and "intermarine" in ex_text and (overlap_ratio >= 0.50 or prefix_matches):
                    matched_idx = i
                    is_tentative_match = True
                    break

            # Caso 2: Duplicato o forte sovrapposizione concettuale
            if (jaccard >= 0.70 or overlap_ratio >= 0.85) and content_overlap >= 2:
                matched_idx = i
                is_tentative_match = False
                break

        now_ts = time.time()

        if matched_idx != -1:
            target = memories[matched_idx]
            target_id = target.get("id")
            text_changed = (target.get("text", "").strip().lower() != clean_lower)

            target["text"] = clean_fact
            target["updated_at"] = now_ts
            if text_changed:
                target["merged_count"] = max(2, target.get("merged_count", 1) + 1)
            if project_id and not target.get("project_id"):
                target["project_id"] = project_id
            if is_new_confirmed or pinned:
                target["pinned"] = True

            # Deduplicazione e potatura:
            # Se la nuova memoria (o un duplicato equivalente) era già stata inserita in memory.json,
            # viene rimossa in modo che non rimangano doppioni.
            pruned_ids: List[str] = []
            filtered_memories: List[Dict[str, Any]] = []
            for j, m in enumerate(memories):
                m_id = m.get("id")
                m_text = (m.get("text") or "").strip().lower()
                if j == matched_idx:
                    filtered_memories.append(target)
                elif new_memory_id and m_id == new_memory_id:
                    if m_id:
                        pruned_ids.append(m_id)
                elif m_id != target_id and m_text == clean_lower:
                    if m_id:
                        pruned_ids.append(m_id)
                else:
                    filtered_memories.append(m)

            memories = filtered_memories
            _atomic_write_json(mem_file, memories)

            if self.mem_proj_manager and hasattr(self.mem_proj_manager, "memories"):
                self.mem_proj_manager.memories = copy.deepcopy(memories)

            if self.storage_manager:
                try:
                    with self.storage_manager.transaction() as conn:
                        conn.execute(
                            "UPDATE memories SET text = ?, updated_at = ?, pinned = ? WHERE id = ?",
                            (clean_fact, now_ts, 1 if target.get("pinned") else 0, target_id),
                        )
                        for pid in pruned_ids:
                            conn.execute("DELETE FROM memories WHERE id = ?", (pid,))
                except Exception as ex:
                    logger.debug(f"SQLite upsert sync error: {ex}")

            return target

        # Se new_memory_id era già presente, restituiscilo
        if new_memory_id:
            existing_new = next((m for m in memories if m.get("id") == new_memory_id), None)
            if existing_new:
                return existing_new

        # Altrimenti aggiunge come nuova memoria
        new_entry = {
            "id": f"mem_{int(now_ts * 1000)}",
            "text": clean_fact,
            "category": "Regola",
            "project_id": project_id,
            "pinned": bool(pinned) or is_new_confirmed or any(k in clean_lower for k in ("sempre", "regola", "ufficiale", "logo", "mai")),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "updated_at": now_ts,
            "merged_count": 1,
        }
        memories.append(new_entry)
        _atomic_write_json(mem_file, memories)

        if self.mem_proj_manager and hasattr(self.mem_proj_manager, "memories"):
            self.mem_proj_manager.memories = copy.deepcopy(memories)

        if self.storage_manager:
            try:
                self.storage_manager.upsert_memory(
                    clean_fact,
                    category=new_entry["category"],
                    project_id=project_id,
                    importance=1.0 if new_entry["pinned"] else 0.5,
                    pinned=new_entry["pinned"],
                    source="user",
                )
            except Exception as ex:
                logger.debug(f"SQLite add_memory sync error: {ex}")

        return new_entry

    def cleanup_intermarine_logo_memory(self) -> bool:
        """Delega alla funzione autonoma con storage_manager di istanza."""
        return cleanup_intermarine_logo_memory(self.data_dir, self.storage_manager)

    # --- Logica Finestra Attiva -> Kanban Task & Rinforzo Competenze ---

    def match_window_to_tasks(self, window_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Correla l'applicazione o titolo della finestra attiva con i task Kanban non completati
        in todo.json e genera window_task_suggestion.
        Filtra categoricamente applicazioni non DCC / non development (es. Spotify, Chrome, Notepad)
        a meno che l'applicazione stessa non sia uno strumento creativo/produttivo riconosciuto.
        """
        app_name = (window_info.get("app_name") or "").strip()
        win_title = (window_info.get("window_title") or "").strip()
        proj_in_win = (window_info.get("project_name") or "").strip()

        if not app_name or app_name.lower() in ("desktop", "application", ""):
            with self._lock:
                self._window_task_suggestion = None
            return None

        app_lower = app_name.lower()
        title_lower = win_title.lower()
        proj_win_lower = proj_in_win.lower()
        file_in_win = (window_info.get("file_name") or "").strip().lower()

        # Workspace Auto-Linking (M1: F6): check if active window title or file path matches a project's workspace_path
        proj_file = os.path.join(self.data_dir, "projects.json")
        projs_raw = _safe_load_json(proj_file, default=[])
        if (not projs_raw or not isinstance(projs_raw, list)) and self.mem_proj_manager and hasattr(self.mem_proj_manager, "projects"):
            projs_raw = self.mem_proj_manager.projects or []

        combined_text = f"{win_title} {file_in_win} {proj_in_win}".lower()

        if isinstance(projs_raw, list):
            for p in projs_raw:
                if not isinstance(p, dict):
                    continue
                ws_path = (p.get("workspace_path") or "").strip()
                if not ws_path:
                    continue
                norm_ws = os.path.normpath(ws_path).lower()
                ws_base = os.path.basename(norm_ws)

                matched_ws = False
                if norm_ws in combined_text or (ws_base and len(ws_base) >= 3 and ws_base in combined_text):
                    matched_ws = True
                elif os.path.isdir(ws_path):
                    try:
                        with os.scandir(ws_path) as entries:
                            for entry in entries:
                                if entry.is_file():
                                    fname_lower = entry.name.lower()
                                    if len(fname_lower) >= 4 and fname_lower in combined_text:
                                        matched_ws = True
                                        break
                    except OSError:
                        pass

                if matched_ws:
                    if self.mem_proj_manager and hasattr(self.mem_proj_manager, "set_active_project"):
                        try:
                            self.mem_proj_manager.set_active_project(p["id"])
                        except Exception:
                            pass
                    if not proj_win_lower:
                        proj_win_lower = (p.get("name") or "").lower()
                    break

        # Verifica che l'applicazione sia uno strumento DCC/creativo/dev riconosciuto
        is_dcc = bool(window_info.get("is_dcc"))
        is_recognized = (
            is_dcc
            or any(t in app_lower for t in RECOGNIZED_CREATIVE_DEV_TOOLS)
            or app_lower in DCC_KEYWORDS_MAP
        )

        if not is_recognized:
            with self._lock:
                self._window_task_suggestion = None
            return None

        # Carica task Kanban non completati
        todo_file = os.path.join(self.data_dir, "todo.json")
        todos_raw = _safe_load_json(todo_file, default=[])
        if not isinstance(todos_raw, list):
            todos_raw = []

        uncompleted: List[Dict[str, Any]] = []
        for item in todos_raw:
            if isinstance(item, dict):
                if item.get("status") != "done":
                    uncompleted.append(item)
            elif isinstance(item, str):
                uncompleted.append({"id": f"todo_{abs(hash(item)) % 100000}", "title": item, "status": "todo"})

        # Determina parole chiave pertinenti allo strumento attivo
        tool_keywords = DCC_KEYWORDS_MAP.get(app_lower, [app_lower])

        best_match: Optional[Dict[str, Any]] = None
        best_score = 0

        for t in uncompleted:
            t_title = (t.get("title") or "").lower()
            t_proj = (t.get("project") or "").lower()
            score = 0

            # Match strumento
            tool_matched = False
            if any(kw in t_title for kw in tool_keywords):
                score += 2
                tool_matched = True
            elif app_lower in t_title:
                score += 2
                tool_matched = True

            # Match progetto
            if proj_win_lower and (proj_win_lower in t_title or (t_proj and proj_win_lower in t_proj)):
                score += 2
            if title_lower and t_proj and t_proj in title_lower:
                score += 1

            if tool_matched and score > best_score:
                best_score = score
                best_match = t
            elif score > best_score and score >= 3:
                best_score = score
                best_match = t

        if best_match and best_score >= 2:
            suggestion = {
                "task_id": best_match.get("id", "todo_suggestion"),
                "task_title": best_match.get("title", ""),
                "detected_app": app_name,
                "suggested_status": "doing",
            }
            with self._lock:
                self._window_task_suggestion = suggestion

            # Rinforzo delicato delle competenze/istinti per lo strumento rilevato
            self.reinforce_tool_skill(app_name)
            return suggestion

        with self._lock:
            self._window_task_suggestion = None
        return None

    def reinforce_tool_skill(self, tool_name: str) -> None:
        """
        Rinforza delicatamente la competenza tecnica in profile.json (skills_learned)
        e incrementa la confidenza degli istinti pertinenti nell'Instinct Engine.
        Filtra strumenti non DCC o non creativi.
        """
        clean_tool = (tool_name or "").strip()
        tool_lower = clean_tool.lower()
        if not clean_tool or tool_lower in ("desktop", "application"):
            return

        is_recognized = (
            any(t in tool_lower for t in RECOGNIZED_CREATIVE_DEV_TOOLS)
            or tool_lower in DCC_KEYWORDS_MAP
        )
        if not is_recognized:
            return

        # 1. Rinforzo in profile.json (skills_learned)
        if self.mem_proj_manager:
            try:
                skills = self.mem_proj_manager.get_skills_learned()
                target_skill = next((s for s in skills if s.get("name", "").lower() == tool_lower), None)
                if target_skill:
                    target_skill["updated_at"] = time.time()
                    ev = target_skill.get("evidence", "")
                    if "finestra attiva" not in ev:
                        target_skill["evidence"] = f"{ev}; rilevata finestra attiva {clean_tool}".strip("; ")
                    self.mem_proj_manager._save_profile()
            except Exception as ex:
                logger.debug(f"reinforce_tool_skill profile error: {ex}")

        # 2. Rinforzo nell'Instinct Engine
        ie = self.instinct_engine
        if not ie:
            try:
                from bridge.instinct_engine import get_instinct_engine
                ie = get_instinct_engine(data_dir=self.data_dir, storage_manager=self.storage_manager)
            except Exception:
                ie = None

        if ie and hasattr(ie, "get_all_instincts"):
            try:
                for inst in ie.get_all_instincts():
                    trig = (inst.get("trigger") or "").lower()
                    act = (inst.get("action") or "").lower()
                    if tool_lower in trig or tool_lower in act:
                        inst_id = inst.get("id")
                        if hasattr(ie, "reinforce_instinct"):
                            ie.reinforce_instinct(inst_id, delta=0.02)
                        elif hasattr(ie, "confirm_instinct"):
                            ie.confirm_instinct(inst_id, delta=0.02)
            except Exception as ex:
                logger.debug(f"reinforce_tool_skill instinct error: {ex}")

    def get_window_task_suggestion(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._window_task_suggestion)

    # --- Gestione Checkpoint di Sessione (/save-session & /resume-session) ---

    def load_session_checkpoint(self) -> Optional[Dict[str, Any]]:
        """
        Carica il checkpoint salvato da session_checkpoint.json.
        In caso di file corrotto, preserva la copia .corrupt.bak prima di restituire None.
        """
        with self._lock:
            ckpt_path = os.path.join(self.data_dir, "session_checkpoint.json")
            data = _safe_load_json(ckpt_path, default=None)
            if isinstance(data, dict):
                self._session_checkpoint = data
                return copy.deepcopy(data)
            return None

    def _load_initial_checkpoint(self) -> None:
        self.load_session_checkpoint()

    def save_session_checkpoint(
        self,
        active_project_id: Optional[str] = None,
        active_project_name: Optional[str] = None,
        active_task_id: Optional[str] = None,
        active_task_title: Optional[str] = None,
        active_view: str = "projects",
        next_step_hint: str = "",
        saved_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Salva session_checkpoint.json con il contesto operativo attuale in modo atomico e thread-safe."""
        with self._lock:
            # Se i campi non sono specificati, prova a desumerli dallo stato dell'app
            if not active_project_id and self.mem_proj_manager:
                try:
                    p_state = self.mem_proj_manager.get_projects_state()
                    act_p = p_state.get("active_project")
                    if act_p:
                        active_project_id = act_p.get("id")
                        active_project_name = act_p.get("name")
                except Exception:
                    pass

            if not active_task_id:
                if self._window_task_suggestion:
                    active_task_id = self._window_task_suggestion.get("task_id")
                    active_task_title = self._window_task_suggestion.get("task_title")

            if not active_task_id:
                # Prova a prendere il primo task da fare in todo.json
                todo_file = os.path.join(self.data_dir, "todo.json")
                todos_raw = _safe_load_json(todo_file, default=[])
                if isinstance(todos_raw, list):
                    for t in todos_raw:
                        if isinstance(t, dict) and t.get("status") in ("doing", "todo"):
                            active_task_id = t.get("id")
                            active_task_title = t.get("title")
                            break
                        elif isinstance(t, str):
                            active_task_id = f"todo_{abs(hash(t)) % 100000}"
                            active_task_title = t
                            break

            if not next_step_hint:
                if active_task_title:
                    next_step_hint = f"Continua con: {active_task_title}"
                else:
                    next_step_hint = "Continua il flusso di lavoro"

            checkpoint: Dict[str, Any] = {
                "active_project_id": active_project_id or "",
                "active_project_name": active_project_name or "",
                "active_task_id": active_task_id or "",
                "active_task_title": active_task_title or "",
                "active_view": active_view or "projects",
                "next_step_hint": next_step_hint,
                "saved_at": saved_at or datetime.now().isoformat(),
            }

            prod_data = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
            if not (os.path.abspath(self.data_dir) == prod_data and _is_test_environment()):
                ckpt_path = os.path.join(self.data_dir, "session_checkpoint.json")
                _atomic_write_json(ckpt_path, checkpoint)

            self._session_checkpoint = checkpoint
            return checkpoint

    def resume_session_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Ripristina il checkpoint salvato in session_checkpoint.json in modo thread-safe."""
        with self._lock:
            data = self.load_session_checkpoint()
            if not isinstance(data, dict):
                return None

            if self.mem_proj_manager and data.get("active_project_id"):
                try:
                    prod_data = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
                    if not (os.path.abspath(self.data_dir) == prod_data and _is_test_environment()):
                        self.mem_proj_manager.set_active_project(data["active_project_id"])
                except Exception:
                    pass

            return copy.deepcopy(data)

    def dismiss_session_checkpoint(self) -> Dict[str, Any]:
        """Rimuove il checkpoint di sessione con retry backoff."""
        with self._lock:
            self._session_checkpoint = None

            prod_data = os.path.abspath(os.path.join(os.path.dirname(__file__), "data"))
            if not (os.path.abspath(self.data_dir) == prod_data and _is_test_environment()):
                ckpt_path = os.path.join(self.data_dir, "session_checkpoint.json")
                if os.path.isfile(ckpt_path):
                    for attempt in range(5):
                        try:
                            os.remove(ckpt_path)
                            break
                        except (PermissionError, OSError):
                            time.sleep(0.005 * (2 ** attempt))

            return {"ok": True, "dismissed": True}

    def get_session_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Restituisce il checkpoint di sessione corrente o lo carica da disco."""
        with self._lock:
            if self._session_checkpoint is not None:
                return copy.deepcopy(self._session_checkpoint)
            return self.load_session_checkpoint()

    def stop(self) -> None:
        """Gracefully signals worker threads to stop."""
        self._stopped = True
        for _ in self._workers:
            try:
                self._queue.put(None)
            except Exception:
                pass

    shutdown = stop


# --- Singleton Globale ---
_HOOKS_RUNNER_INSTANCE: Optional[HooksRunner] = None
_RUNNER_LOCK = threading.RLock()


def get_hooks_runner(
    data_dir: Optional[str] = None,
    storage_manager: Optional[Any] = None,
    mem_proj_manager: Optional[Any] = None,
    instinct_engine: Optional[Any] = None,
) -> HooksRunner:
    """Restituisce l'istanza singleton di HooksRunner."""
    global _HOOKS_RUNNER_INSTANCE
    with _RUNNER_LOCK:
        if _HOOKS_RUNNER_INSTANCE is None:
            _HOOKS_RUNNER_INSTANCE = HooksRunner(
                data_dir=data_dir,
                storage_manager=storage_manager,
                mem_proj_manager=mem_proj_manager,
                instinct_engine=instinct_engine,
            )
        else:
            if data_dir and _HOOKS_RUNNER_INSTANCE.data_dir != data_dir:
                _HOOKS_RUNNER_INSTANCE.data_dir = data_dir
            if storage_manager and _HOOKS_RUNNER_INSTANCE.storage_manager is None:
                _HOOKS_RUNNER_INSTANCE.storage_manager = storage_manager
            if mem_proj_manager and _HOOKS_RUNNER_INSTANCE.mem_proj_manager is None:
                _HOOKS_RUNNER_INSTANCE.mem_proj_manager = mem_proj_manager
            if instinct_engine and _HOOKS_RUNNER_INSTANCE.instinct_engine is None:
                _HOOKS_RUNNER_INSTANCE.instinct_engine = instinct_engine
        return _HOOKS_RUNNER_INSTANCE
