"""
neko_bot.memory.store - Thread-safe CRDT LWW-Element-Set Memory Store with Atomic Persistence.

Manages 3 persistent memory layers:
1. semantic_facts.json -> SemanticFact entries
2. projects_deadlines.json -> ProjectDeadline entries
3. episodes.json -> Episode entries

Features:
- CRDT LWW-Element-Set conflict resolution matching TaskStore rules.
- Atomic file writes via atomic_write_json_file with Windows lock retry backoff.
- Case-insensitive prefix matching with ambiguity detection.
- Unified CRDT entity export and mutation application for cloud synchronization.
- Status and upcoming deadlines aggregation for HUD telemetry.
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from neko_bot.core.config import Config, atomic_write_json_file
from neko_bot.memory.models import (
    Episode,
    ProjectDeadline,
    SemanticFact,
    generate_memory_id,
    now_utc_iso,
)
from neko_bot.memory.search import MemorySearchEngine

logger = logging.getLogger("neko_bot.memory.store")


class AmbiguousMemoryPrefixError(Exception):
    """Raised when a memory entry ID prefix matches multiple items."""
    pass


_DIR_LOCKS: Dict[str, threading.RLock] = {}
_DIR_LOCKS_MUTEX = threading.Lock()


def _get_dir_lock(dir_path: Path) -> threading.RLock:
    """Returns a shared process-wide RLock per canonical directory path."""
    key = str(dir_path.resolve()).lower()
    with _DIR_LOCKS_MUTEX:
        if key not in _DIR_LOCKS:
            _DIR_LOCKS[key] = threading.RLock()
        return _DIR_LOCKS[key]


class MemoryStore:
    """
    Thread-safe CRDT LWW-Element-Set store for multi-level persistent memory.
    """

    def __init__(
        self,
        workspace_dir: Optional[Union[Path, str]] = None,
        config: Optional[Config] = None,
    ) -> None:
        self.config = config or Config.load()

        # Resolve memory directory
        if workspace_dir is not None:
            p = Path(workspace_dir).resolve()
            if p.name == "memory":
                self.memory_dir = p
            else:
                self.memory_dir = p / "memory"
        else:
            self.memory_dir = self.config.workspace_dir / "memory"

        self._lock = _get_dir_lock(self.memory_dir)
        self.search_engine = MemorySearchEngine()

        self.facts_file = self.memory_dir / "semantic_facts.json"
        self.projects_file = self.memory_dir / "projects_deadlines.json"
        self.episodes_file = self.memory_dir / "episodes.json"

        self._facts: Dict[str, SemanticFact] = {}
        self._projects: Dict[str, ProjectDeadline] = {}
        self._episodes: Dict[str, Episode] = {}

        self._purged_ids: Set[str] = set()
        self._mtimes: Dict[str, float] = {
            "facts": 0.0,
            "projects": 0.0,
            "episodes": 0.0,
        }

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._reload_all()

    # =========================================================================
    # Internal Persistence & Synchronization
    # =========================================================================

    @staticmethod
    def _read_json_with_retry(file_path: Path, max_retries: int = 10, delay: float = 0.005) -> Any:
        """Reads and parses a JSON file with retry backoff on Windows file lock contention."""
        for attempt in range(max_retries):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (PermissionError, OSError):
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)

    def _reload_all(self) -> None:
        """Loads all three memory stores from disk."""
        with self._lock:
            self._load_facts()
            self._load_projects()
            self._load_episodes()

    def _reload_if_modified(self) -> None:
        """Checks file mtimes and reloads files modified externally."""
        with self._lock:
            if self.facts_file.exists():
                try:
                    mtime = os.path.getmtime(self.facts_file)
                    if mtime > self._mtimes["facts"]:
                        self._load_facts()
                except (PermissionError, OSError):
                    pass

            if self.projects_file.exists():
                try:
                    mtime = os.path.getmtime(self.projects_file)
                    if mtime > self._mtimes["projects"]:
                        self._load_projects()
                except (PermissionError, OSError):
                    pass

            if self.episodes_file.exists():
                try:
                    mtime = os.path.getmtime(self.episodes_file)
                    if mtime > self._mtimes["episodes"]:
                        self._load_episodes()
                except (PermissionError, OSError):
                    pass

    def _load_facts(self) -> None:
        if not self.facts_file.exists():
            self._facts = {}
            self._mtimes["facts"] = 0.0
            return
        try:
            data = self._read_json_with_retry(self.facts_file)
            items = data.get("facts", data) if isinstance(data, dict) else {}
            self._facts = {
                k: SemanticFact.from_dict(v)
                for k, v in items.items()
                if isinstance(v, dict) and k not in self._purged_ids
            }
            self._mtimes["facts"] = os.path.getmtime(self.facts_file)
        except Exception as e:
            logger.warning("Error reading %s: %s (keeping existing state)", self.facts_file, e)

    def _load_projects(self) -> None:
        if not self.projects_file.exists():
            self._projects = {}
            self._mtimes["projects"] = 0.0
            return
        try:
            data = self._read_json_with_retry(self.projects_file)
            items = data.get("projects", data) if isinstance(data, dict) else {}
            self._projects = {
                k: ProjectDeadline.from_dict(v)
                for k, v in items.items()
                if isinstance(v, dict) and k not in self._purged_ids
            }
            self._mtimes["projects"] = os.path.getmtime(self.projects_file)
        except Exception as e:
            logger.warning("Error reading %s: %s (keeping existing state)", self.projects_file, e)

    def _load_episodes(self) -> None:
        if not self.episodes_file.exists():
            self._episodes = {}
            self._mtimes["episodes"] = 0.0
            return
        try:
            data = self._read_json_with_retry(self.episodes_file)
            items = data.get("episodes", data) if isinstance(data, dict) else {}
            self._episodes = {
                k: Episode.from_dict(v)
                for k, v in items.items()
                if isinstance(v, dict) and k not in self._purged_ids
            }
            self._mtimes["episodes"] = os.path.getmtime(self.episodes_file)
        except Exception as e:
            logger.warning("Error reading %s: %s (keeping existing state)", self.episodes_file, e)

    def _save_facts(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if self.facts_file.exists() and os.path.getmtime(self.facts_file) > self._mtimes.get("facts", 0.0):
            try:
                disk_data = self._read_json_with_retry(self.facts_file)
                if isinstance(disk_data, dict):
                    disk_items = disk_data.get("facts", disk_data)
                    if isinstance(disk_items, dict):
                        for k, v in disk_items.items():
                            if k in self._purged_ids:
                                continue
                            if isinstance(v, dict):
                                if k not in self._facts:
                                    self._facts[k] = SemanticFact.from_dict(v)
                                else:
                                    if self._should_win(v, self._facts[k].to_dict()):
                                        self._facts[k] = SemanticFact.from_dict(v)
            except Exception as e:
                logger.debug("Error merging disk facts before save %s: %s", self.facts_file, e)

        payload = {
            "version": "1.0",
            "updated_at": now_utc_iso(),
            "facts": {k: v.to_dict() for k, v in self._facts.items() if k not in self._purged_ids},
        }
        atomic_write_json_file(self.facts_file, payload)
        if self.facts_file.exists():
            try:
                self._mtimes["facts"] = os.path.getmtime(self.facts_file)
            except (PermissionError, OSError):
                pass

    def _save_projects(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if self.projects_file.exists() and os.path.getmtime(self.projects_file) > self._mtimes.get("projects", 0.0):
            try:
                disk_data = self._read_json_with_retry(self.projects_file)
                if isinstance(disk_data, dict):
                    disk_items = disk_data.get("projects", disk_data)
                    if isinstance(disk_items, dict):
                        for k, v in disk_items.items():
                            if k in self._purged_ids:
                                continue
                            if isinstance(v, dict):
                                if k not in self._projects:
                                    self._projects[k] = ProjectDeadline.from_dict(v)
                                else:
                                    if self._should_win(v, self._projects[k].to_dict()):
                                        self._projects[k] = ProjectDeadline.from_dict(v)
            except Exception as e:
                logger.debug("Error merging disk projects before save %s: %s", self.projects_file, e)

        payload = {
            "version": "1.0",
            "updated_at": now_utc_iso(),
            "projects": {k: v.to_dict() for k, v in self._projects.items() if k not in self._purged_ids},
        }
        atomic_write_json_file(self.projects_file, payload)
        if self.projects_file.exists():
            try:
                self._mtimes["projects"] = os.path.getmtime(self.projects_file)
            except (PermissionError, OSError):
                pass

    def _save_episodes(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if self.episodes_file.exists() and os.path.getmtime(self.episodes_file) > self._mtimes.get("episodes", 0.0):
            try:
                disk_data = self._read_json_with_retry(self.episodes_file)
                if isinstance(disk_data, dict):
                    disk_items = disk_data.get("episodes", disk_data)
                    if isinstance(disk_items, dict):
                        for k, v in disk_items.items():
                            if k in self._purged_ids:
                                continue
                            if isinstance(v, dict):
                                if k not in self._episodes:
                                    self._episodes[k] = Episode.from_dict(v)
                                else:
                                    if self._should_win(v, self._episodes[k].to_dict()):
                                        self._episodes[k] = Episode.from_dict(v)
            except Exception as e:
                logger.debug("Error merging disk episodes before save %s: %s", self.episodes_file, e)

        payload = {
            "version": "1.0",
            "updated_at": now_utc_iso(),
            "episodes": {k: v.to_dict() for k, v in self._episodes.items() if k not in self._purged_ids},
        }
        atomic_write_json_file(self.episodes_file, payload)
        if self.episodes_file.exists():
            try:
                self._mtimes["episodes"] = os.path.getmtime(self.episodes_file)
            except (PermissionError, OSError):
                pass

    # =========================================================================
    # Mutation Methods
    # =========================================================================

    def add_fact(
        self,
        content: str,
        category: str = "fact",
        tags: Optional[List[str]] = None,
        importance: int = 3,
        confidence: float = 1.0,
        source: str = "cli",
    ) -> Dict[str, Any]:
        """Creates and stores a SemanticFact entry."""
        if not content or not str(content).strip():
            raise ValueError("Fact content cannot be empty")

        with self._lock:
            self._reload_if_modified()
            fact = SemanticFact(
                category=category or "fact",
                content=str(content).strip(),
                tags=tags or [],
                importance=importance,
                confidence=confidence,
                source=source,
            )
            self._facts[fact.id] = fact
            self._save_facts()
            return fact.to_dict()

    def add_project(
        self,
        project_name: str,
        milestone: str,
        deadline_utc: Optional[str] = None,
        risk_level: str = "medium",
        dependencies: Optional[List[str]] = None,
        time_budget_hours: Optional[float] = None,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Creates and stores a ProjectDeadline entry."""
        if not project_name or not str(project_name).strip():
            raise ValueError("Project name cannot be empty")
        if not milestone or not str(milestone).strip():
            raise ValueError("Milestone cannot be empty")

        with self._lock:
            self._reload_if_modified()
            proj = ProjectDeadline(
                project_name=str(project_name).strip(),
                milestone=str(milestone).strip(),
                deadline_utc=str(deadline_utc).strip() if deadline_utc else None,
                dependencies=dependencies or [],
                risk_level=risk_level or "medium",
                time_budget_hours=time_budget_hours,
                notes=str(notes).strip() if notes else "",
            )
            self._projects[proj.id] = proj
            self._save_projects()
            return proj.to_dict()

    def add_episode(
        self,
        session_id: str = "default",
        event_type: str = "user_prompt",
        summary: str = "",
        full_text: str = "",
        context_tags: Optional[List[str]] = None,
        related_entities: Optional[Dict[str, List[str]]] = None,
        outcome: str = "neutral",
    ) -> Dict[str, Any]:
        """Creates and stores an Episode entry."""
        with self._lock:
            self._reload_if_modified()
            ep = Episode(
                session_id=session_id or "default",
                event_type=event_type or "user_prompt",
                summary=str(summary).strip() if summary else "",
                full_text=str(full_text).strip() if full_text else "",
                context_tags=context_tags or [],
                related_entities=related_entities or {},
                outcome=outcome or "neutral",
            )
            self._episodes[ep.id] = ep
            self._save_episodes()
            return ep.to_dict()

    # =========================================================================
    # Query & Search Methods
    # =========================================================================

    def search(
        self,
        query: str,
        level: str = "all",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Executes zero-dependency BM25/TF-IDF scoring query with recency decay across selected layers.
        Returns list of ranked result dictionaries up to limit.
        """
        with self._lock:
            self._reload_if_modified()
            corpus: List[Tuple[str, Dict[str, Any]]] = []
            clean_level = str(level).lower().strip()

            if clean_level in ("all", "*"):
                for f in self._facts.values():
                    if not f.deleted:
                        corpus.append(("semantic", f.to_dict()))
                for p in self._projects.values():
                    if not p.deleted:
                        corpus.append(("project", p.to_dict()))
                for e in self._episodes.values():
                    if not e.deleted:
                        corpus.append(("episodic", e.to_dict()))

            elif clean_level in ("semantic", "fact", "facts"):
                for f in self._facts.values():
                    if not f.deleted:
                        corpus.append(("semantic", f.to_dict()))

            elif clean_level in ("project", "projects", "deadline", "deadlines"):
                for p in self._projects.values():
                    if not p.deleted:
                        corpus.append(("project", p.to_dict()))

            elif clean_level in ("episodic", "episode", "episodes", "history"):
                for e in self._episodes.values():
                    if not e.deleted:
                        corpus.append(("episodic", e.to_dict()))

            results = self.search_engine.score_corpus(query, corpus)
            top_results = results[:limit]

            # Mark accessed on retrieved facts
            save_facts_needed = False
            for item in top_results:
                if item.get("level") == "semantic":
                    fid = item.get("entry", {}).get("id")
                    if fid and fid in self._facts:
                        self._facts[fid].mark_accessed()
                        item["entry"] = self._facts[fid].to_dict()
                        save_facts_needed = True

            if save_facts_needed:
                try:
                    self._save_facts()
                except Exception:
                    pass

            return top_results

    def list_entries(
        self,
        level: str = "semantic",
        project: Optional[str] = None,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lists entries for a given level, optionally filtering by project or including deleted."""
        with self._lock:
            self._reload_if_modified()
            clean_level = str(level).lower().strip()
            res: List[Dict[str, Any]] = []

            if clean_level in ("semantic", "fact", "facts"):
                for f in self._facts.values():
                    if include_deleted or not f.deleted:
                        res.append(f.to_dict())

            elif clean_level in ("project", "projects", "deadline", "deadlines"):
                clean_p = project.lstrip("#").lower() if project else None
                for p in self._projects.values():
                    if not include_deleted and p.deleted:
                        continue
                    if clean_p and p.project_name.lower() != clean_p:
                        continue
                    res.append(p.to_dict())

            elif clean_level in ("episodic", "episode", "episodes"):
                for e in self._episodes.values():
                    if include_deleted or not e.deleted:
                        res.append(e.to_dict())

            elif clean_level in ("all", "*"):
                for f in self._facts.values():
                    if include_deleted or not f.deleted:
                        res.append({**f.to_dict(), "level": "semantic"})
                for p in self._projects.values():
                    if include_deleted or not p.deleted:
                        res.append({**p.to_dict(), "level": "project"})
                for e in self._episodes.values():
                    if include_deleted or not e.deleted:
                        res.append({**e.to_dict(), "level": "episodic"})

            return res

    def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves entry by exact ID or unique prefix.
        Raises AmbiguousMemoryPrefixError if prefix matches multiple items.
        """
        if not entry_id:
            return None

        with self._lock:
            self._reload_if_modified()
            query_id = entry_id.strip().lower()

            # Exact match check across all 3 stores
            if query_id in self._facts:
                return self._facts[query_id].to_dict()
            if query_id in self._projects:
                return self._projects[query_id].to_dict()
            if query_id in self._episodes:
                return self._episodes[query_id].to_dict()

            # Prefix matching
            matches: List[Tuple[str, str, Dict[str, Any]]] = []  # (layer, id, dict)
            for k, f in self._facts.items():
                if k.lower().startswith(query_id) or (k.startswith("fact-") and k[5:].lower().startswith(query_id)):
                    matches.append(("fact", k, f.to_dict()))
            for k, p in self._projects.items():
                if k.lower().startswith(query_id) or (k.startswith("proj-") and k[5:].lower().startswith(query_id)):
                    matches.append(("project", k, p.to_dict()))
            for k, e in self._episodes.items():
                if k.lower().startswith(query_id) or (k.startswith("ep-") and k[3:].lower().startswith(query_id)):
                    matches.append(("episode", k, e.to_dict()))

            if len(matches) == 1:
                return matches[0][2]
            elif len(matches) > 1:
                matched_ids = [m[1] for m in matches]
                raise AmbiguousMemoryPrefixError(
                    f"Ambiguous memory ID prefix '{entry_id}' matches multiple entries: {', '.join(matched_ids)}"
                )

            return None

    def delete_entry(self, entry_id: str, hard: bool = False) -> bool:
        """
        Deletes entry by exact ID or unique prefix.
        If hard=False (default), marks tombstone (deleted=True) with updated_at timestamp.
        Returns True if found and deleted, False otherwise.
        """
        if not entry_id:
            return False

        with self._lock:
            self._reload_if_modified()
            target_entry = self.get_entry(entry_id)
            if target_entry is None:
                return False

            real_id = target_entry["id"]

            if real_id in self._facts:
                if hard:
                    self._purged_ids.add(real_id)
                    del self._facts[real_id]
                else:
                    self._facts[real_id].deleted = True
                    self._facts[real_id].updated_at = now_utc_iso()
                self._save_facts()
                self._purged_ids.discard(real_id)
                return True

            if real_id in self._projects:
                if hard:
                    self._purged_ids.add(real_id)
                    del self._projects[real_id]
                else:
                    self._projects[real_id].deleted = True
                    self._projects[real_id].updated_at = now_utc_iso()
                self._save_projects()
                self._purged_ids.discard(real_id)
                return True

            if real_id in self._episodes:
                if hard:
                    self._purged_ids.add(real_id)
                    del self._episodes[real_id]
                else:
                    self._episodes[real_id].deleted = True
                    self._episodes[real_id].updated_at = now_utc_iso()
                self._save_episodes()
                self._purged_ids.discard(real_id)
                return True

            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Computes memory statistics conforming to Project & Bridge telemetry schema:
        {
            "total_facts": int,
            "active_projects": int,
            "recent_episodes": int,
            "upcoming_deadlines": [
                {"project": "CyberpunkCity", "milestone": "Beta", "due": "2026-10-28", "risk": "medium"}
            ]
        }
        """
        with self._lock:
            self._reload_if_modified()

            total_facts = sum(1 for f in self._facts.values() if not f.deleted)
            active_projects = sum(
                1 for p in self._projects.values()
                if not p.deleted and p.status in ("planning", "active", "blocked")
            )
            recent_episodes = sum(1 for e in self._episodes.values() if not e.deleted)

            # Extract upcoming deadlines
            active_proj_list = [
                p for p in self._projects.values()
                if not p.deleted and p.deadline_utc and p.status in ("planning", "active", "blocked")
            ]
            # Sort by deadline ascending
            active_proj_list.sort(key=lambda p: str(p.deadline_utc))

            upcoming_deadlines: List[Dict[str, Any]] = []
            for p in active_proj_list[:5]:
                upcoming_deadlines.append({
                    "project": p.project_name,
                    "milestone": p.milestone,
                    "due": p.deadline_utc,
                    "risk": p.risk_level,
                    "overdue": p.is_overdue(),
                })

            return {
                "total_facts": total_facts,
                "active_projects": active_projects,
                "recent_episodes": recent_episodes,
                "upcoming_deadlines": upcoming_deadlines,
            }

    # =========================================================================
    # CRDT LWW Synchronization (Milestones 2 & 4 Contract)
    # =========================================================================

    def export_crdt_entities(self) -> List[Dict[str, Any]]:
        """
        Exports all memory entries (including tombstones) as uniform CRDT entities
        ready for cloud synchronization reconciliation.
        """
        with self._lock:
            self._reload_if_modified()
            entities: List[Dict[str, Any]] = []

            for f in self._facts.values():
                entities.append({
                    "entity_type": "memory_fact",
                    "id": f.id,
                    "data": f.to_dict(),
                    "updated_at": f.updated_at,
                    "deleted": f.deleted,
                })

            for p in self._projects.values():
                entities.append({
                    "entity_type": "memory_project",
                    "id": p.id,
                    "data": p.to_dict(),
                    "updated_at": p.updated_at,
                    "deleted": p.deleted,
                })

            for e in self._episodes.values():
                entities.append({
                    "entity_type": "memory_episode",
                    "id": e.id,
                    "data": e.to_dict(),
                    "updated_at": e.updated_at,
                    "deleted": e.deleted,
                })

            return entities

    def apply_crdt_mutation(self, mutation: Dict[str, Any]) -> bool:
        """
        Applies a remote CRDT mutation using deterministic Last-Write-Wins rules:
        1. Greater updated_at timestamp wins.
        2. If equal, tombstone (deleted=True) wins.
        3. If equal, lexicographical JSON string comparison breaks ties.
        Returns True if the mutation was applied/updated locally, False if ignored.
        """
        if not isinstance(mutation, dict):
            return False

        with self._lock:
            self._reload_if_modified()

            # Normalize mutation payload
            entity_type = mutation.get("entity_type", "")
            entity_id = mutation.get("id") or mutation.get("data", {}).get("id")
            if not entity_id:
                return False

            raw_data = mutation.get("data")
            if isinstance(raw_data, dict):
                candidate_data = dict(raw_data)
            else:
                candidate_data = dict(mutation)

            candidate_data["id"] = entity_id
            if "updated_at" in mutation and "updated_at" not in candidate_data:
                candidate_data["updated_at"] = mutation["updated_at"]
            if "deleted" in mutation and "deleted" not in candidate_data:
                candidate_data["deleted"] = mutation["deleted"]

            # Deterministic fallback timestamp
            fallback_ts = candidate_data.get("updated_at") or mutation.get("updated_at") or now_utc_iso()

            # Preserve incoming created_at and timestamp fields; fallback to mutation's updated_at instead of generating local now_utc_iso()
            if "created_at" in mutation and "created_at" not in candidate_data:
                candidate_data["created_at"] = mutation["created_at"]
            if not candidate_data.get("created_at"):
                candidate_data["created_at"] = fallback_ts

            if "timestamp" in mutation and "timestamp" not in candidate_data:
                candidate_data["timestamp"] = mutation["timestamp"]
            if not candidate_data.get("timestamp"):
                candidate_data["timestamp"] = fallback_ts

            # Deduce entity type from prefix or data keys if not explicitly stated
            if not entity_type:
                if entity_id.startswith("fact-") or "content" in candidate_data:
                    entity_type = "memory_fact"
                elif entity_id.startswith("proj-") or "project_name" in candidate_data or "milestone" in candidate_data:
                    entity_type = "memory_project"
                elif entity_id.startswith("ep-") or "session_id" in candidate_data or "event_type" in candidate_data:
                    entity_type = "memory_episode"
                else:
                    entity_type = "memory_fact"

            # Apply according to deduced store
            if entity_type in ("memory_fact", "fact"):
                local_fact = self._facts.get(entity_id)
                cand_obj = SemanticFact.from_dict(candidate_data)
                if local_fact is not None and not (isinstance(raw_data, dict) and raw_data.get("created_at")):
                    cand_obj.created_at = local_fact.created_at
                cand_dict = cand_obj.to_dict()

                if local_fact is None or self._should_win(cand_dict, local_fact.to_dict()):
                    self._facts[entity_id] = cand_obj
                    self._save_facts()
                    return True
                return False

            elif entity_type in ("memory_project", "project"):
                local_proj = self._projects.get(entity_id)
                cand_obj = ProjectDeadline.from_dict(candidate_data)
                if local_proj is not None and not (isinstance(raw_data, dict) and raw_data.get("created_at")):
                    cand_obj.created_at = local_proj.created_at
                cand_dict = cand_obj.to_dict()

                if local_proj is None or self._should_win(cand_dict, local_proj.to_dict()):
                    self._projects[entity_id] = cand_obj
                    self._save_projects()
                    return True
                return False

            elif entity_type in ("memory_episode", "episode"):
                local_ep = self._episodes.get(entity_id)
                cand_obj = Episode.from_dict(candidate_data)
                if local_ep is not None and not (isinstance(raw_data, dict) and raw_data.get("created_at")):
                    cand_obj.created_at = local_ep.created_at
                if local_ep is not None and not (isinstance(raw_data, dict) and raw_data.get("timestamp")):
                    cand_obj.timestamp = local_ep.timestamp
                cand_dict = cand_obj.to_dict()

                if local_ep is None or self._should_win(cand_dict, local_ep.to_dict()):
                    self._episodes[entity_id] = cand_obj
                    self._save_episodes()
                    return True
                return False

            return False

    @staticmethod
    def _parse_timestamp(ts: Optional[str]) -> float:
        """Parses ISO-8601 string to epoch timestamp safely, defaulting naive strings to UTC."""
        if not ts:
            return 0.0
        try:
            clean = str(ts).replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0

    @classmethod
    def _should_win(cls, candidate: Dict[str, Any], current: Dict[str, Any]) -> bool:
        """
        Deterministic LWW tie-breaker:
        1. Compare updated_at timestamps.
        2. If equal, tombstone (deleted=True) wins.
        3. If both tombstone or both active, compare lexicographical canonical JSON strings.
        """
        t_cand = cls._parse_timestamp(candidate.get("updated_at"))
        t_curr = cls._parse_timestamp(current.get("updated_at"))

        if t_cand > t_curr:
            return True
        if t_cand < t_curr:
            return False

        # Timestamps are equal - tombstone wins
        cand_del = bool(candidate.get("deleted", False))
        curr_del = bool(current.get("deleted", False))
        if cand_del != curr_del:
            return cand_del

        # Tie-break lexicographically on canonical JSON
        s_cand = json.dumps(candidate, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        s_curr = json.dumps(current, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
        return s_cand > s_curr
