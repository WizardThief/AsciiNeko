"""
bridge/instinct_engine.py
=========================
Continuous Learning v2.1 Instinct Engine for AsciiNeko Desktop Companion.
Dual-persisted to SQLite-WAL (neko_storage.db: table 'instincts') and
mirrored to bridge/data/instincts.json via AtomicJsonStore.

Features:
- Atomic Instincts Model: id, trigger, action, confidence [0.30, 0.95],
  scope ('project' | 'global'), project_id, domain, use_count, created_at, last_seen_at.
- Confidence evolution: reinforcing boosts confidence up to 0.95 and increments use_count;
  strictly clamped to [0.30, 0.95].
- Project -> Global auto-promotion: when an instinct with the same normalized trigger/action
  is observed across 2+ distinct projects, its scope is promoted to 'global'.
- Confidence decay & pruning: decays unconfirmed inactive instincts; prunes instincts with
  confidence <= 0.35 older than 14 days.
- Idempotent startup seeding for Nave Intermarine navy logo and 3D realtime pipeline.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from bridge.storage_manager import (
    AtomicJsonStore,
    StorageManager,
    get_storage_manager,
)

logger = logging.getLogger("AsciiNeko.InstinctEngine")

CONFIDENCE_MIN = 0.30
CONFIDENCE_MAX = 0.95
CONFIDENCE_THRESHOLD_GLOBAL = 0.55
CONFIDENCE_PRUNE_THRESHOLD = 0.35
PRUNE_MAX_AGE_DAYS = 14.0

INITIAL_SEEDS: List[Dict[str, Any]] = [
    {
        "id": "inst_seed_intermarine_logo",
        "trigger": "Quando si prepara materiale o render per Intermarine",
        "action": "Usa sempre il colore Blu Marino (dark navy) per il logo ufficiale",
        "confidence": 0.90,
        "scope": "project",
        "project_id": "proj_nave_intermarine",
        "domain": "client-rule",
        "use_count": 5,
    },
    {
        "id": "inst_seed_glb_draco_pipeline",
        "trigger": "Quando si esporta un modello 3D per il web",
        "action": "Esporta in GLB con compressione Draco prima dell'integrazione Rust + Three.js",
        "confidence": 0.85,
        "scope": "global",
        "project_id": None,
        "domain": "pipeline",
        "use_count": 4,
    },
]


def clamp_confidence(value: float) -> float:
    """Clamps confidence strictly into [0.30, 0.95]."""
    try:
        val = float(value)
    except (ValueError, TypeError):
        val = 0.50
    return round(max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, val)), 4)


def normalize_instinct_text(text: str) -> str:
    """Normalizes text for matching triggers and actions across projects."""
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_timestamp_epoch(ts: Any) -> float:
    """Parses ISO string, float, or int timestamp into epoch seconds."""
    if ts is None:
        return time.time()
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return float(ts)
        except ValueError:
            pass
        try:
            # Handle ISO string e.g. 2026-10-27T10:00:00Z or +00:00
            iso_str = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(iso_str).timestamp()
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(ts, fmt).timestamp()
            except Exception:
                pass
    return time.time()


class InstinctEngine:
    """
    Manages atomic instincts dual-persisted to SQLite and instincts.json.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        storage_manager: Optional[StorageManager] = None,
    ):
        self._lock = threading.RLock()
        if storage_manager:
            self.storage = storage_manager
            self.data_dir = os.path.abspath(data_dir or storage_manager.data_dir)
        elif data_dir:
            self.data_dir = os.path.abspath(data_dir)
            self.storage = get_storage_manager(data_dir=self.data_dir)
        else:
            self.storage = get_storage_manager()
            self.data_dir = os.path.abspath(self.storage.data_dir)

        os.makedirs(self.data_dir, exist_ok=True)
        self.json_path = os.path.join(self.data_dir, "instincts.json")
        self.json_store = AtomicJsonStore(
            file_path=self.json_path,
            default=[],
            storage_manager=self.storage,
        )

        # Synchronize SQLite and JSON on startup and seed initial instincts
        self._sync_and_seed()

    def _sync_and_seed(self) -> None:
        """
        Reconciles SQLite and instincts.json, applies auto-promotion if duplicate
        triggers exist across projects, and idempotently seeds initial rules.
        """
        with self._lock:
            db_instincts = self.storage.get_all_instincts()
            db_by_id = {i["id"]: dict(i) for i in db_instincts}

            json_raw = self.json_store.load(default=[])
            if not isinstance(json_raw, list):
                json_raw = []
            json_by_id = {i["id"]: dict(i) for i in json_raw if isinstance(i, dict) and "id" in i}

            # 1. Backfill records from JSON to SQLite if missing
            for i_id, inst in json_by_id.items():
                if i_id not in db_by_id:
                    self.storage.save_instinct(inst)
                    db_by_id[i_id] = inst

            # 2. Backfill records from SQLite to JSON if missing
            for i_id, inst in db_by_id.items():
                if i_id not in json_by_id:
                    json_by_id[i_id] = inst

            # 3. Seed initial instincts idempotently (unless clean_slate mode is active)
            if os.path.isfile(os.path.join(self.data_dir, ".clean_slate")):
                seeds_to_apply = []
            else:
                seeds_to_apply = INITIAL_SEEDS
            now_iso = datetime.now(timezone.utc).isoformat()
            for seed in seeds_to_apply:
                s_id = seed["id"]
                s_trig_norm = normalize_instinct_text(seed["trigger"])
                s_act_norm = normalize_instinct_text(seed["action"])

                # Check by ID or matching normalized trigger + action
                matched_existing = None
                for existing in db_by_id.values():
                    if existing["id"] == s_id:
                        matched_existing = existing
                        break
                    if (
                        normalize_instinct_text(existing.get("trigger", "")) == s_trig_norm
                        and normalize_instinct_text(existing.get("action", "")) == s_act_norm
                    ):
                        matched_existing = existing
                        break

                if matched_existing is not None:
                    # Reconcile seed record if project_id or scope is missing/mismatched
                    needs_update = False
                    if s_id == "inst_seed_intermarine_logo" or seed.get("project_id"):
                        target_pid = seed.get("project_id")
                        if matched_existing.get("project_id") != target_pid:
                            matched_existing["project_id"] = target_pid
                            needs_update = True
                    if seed.get("scope"):
                        if matched_existing.get("scope") != seed.get("scope"):
                            matched_existing["scope"] = seed["scope"]
                            needs_update = True
                    if seed.get("scope") == "global" and matched_existing.get("project_id") is not None:
                        matched_existing["project_id"] = None
                        needs_update = True
                    if needs_update:
                        self.storage.save_instinct(matched_existing)
                        db_by_id[matched_existing["id"]] = matched_existing
                else:
                    seeded_inst = {
                        "id": s_id,
                        "trigger": seed["trigger"],
                        "action": seed["action"],
                        "confidence": clamp_confidence(seed["confidence"]),
                        "scope": seed["scope"],
                        "project_id": seed.get("project_id"),
                        "domain": seed.get("domain", "workflow"),
                        "use_count": int(seed.get("use_count", 1)),
                        "created_at": now_iso,
                        "last_seen_at": now_iso,
                    }
                    self.storage.save_instinct(seeded_inst)
                    db_by_id[s_id] = seeded_inst

            # 4. Save synced state to instincts.json
            self._write_json_mirror(list(db_by_id.values()))

    def _write_json_mirror(self, instincts: List[Dict[str, Any]]) -> None:
        """Writes current instincts list to instincts.json atomically."""
        # Sort descending by confidence, then use_count
        sorted_inst = sorted(
            instincts,
            key=lambda x: (float(x.get("confidence", 0.0)), int(x.get("use_count", 0))),
            reverse=True,
        )
        self.json_store.save(sorted_inst)

    def get_all_instincts(self) -> List[Dict[str, Any]]:
        """Returns all instincts sorted by confidence descending, use_count descending."""
        with self._lock:
            instincts = self.storage.get_all_instincts()
            return [dict(i) for i in instincts]

    list_instincts = get_all_instincts

    def get_instinct(self, instinct_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single instinct by ID."""
        with self._lock:
            for inst in self.storage.get_all_instincts():
                if inst["id"] == instinct_id:
                    return dict(inst)
            return None

    def get_active_instincts(
        self, active_project_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Returns active instincts relevant to current context:
        - scope == 'global' AND confidence >= 0.55
        - scope == 'project' AND project_id == active_project_id
        """
        with self._lock:
            all_inst = self.get_all_instincts()
            active: List[Dict[str, Any]] = []
            for inst in all_inst:
                scope = inst.get("scope", "global")
                conf = float(inst.get("confidence", 0.5))
                proj = inst.get("project_id")
                if scope == "global" and conf >= CONFIDENCE_THRESHOLD_GLOBAL:
                    active.append(inst)
                elif scope == "project" and active_project_id and proj == active_project_id:
                    active.append(inst)
            return active

    def add_instinct(
        self,
        trigger: str,
        action: str,
        confidence: float = 0.50,
        scope: str = "project",
        project_id: Optional[str] = None,
        domain: str = "workflow",
        use_count: int = 1,
        instinct_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Adds a new instinct or updates an existing matching instinct.
        Performs Project -> Global auto-promotion if observed in 2+ distinct projects.
        Dual-persisted to SQLite and instincts.json.
        """
        with self._lock:
            clean_trigger = trigger.strip()
            clean_action = action.strip()
            if not clean_trigger or not clean_action:
                raise ValueError("Trigger and action cannot be empty.")

            norm_trig = normalize_instinct_text(clean_trigger)
            norm_act = normalize_instinct_text(clean_action)

            now_iso = datetime.now(timezone.utc).isoformat()
            all_inst = self.get_all_instincts()

            # Check if this exact trigger/action has already been seen in other projects
            matching = [
                i for i in all_inst
                if normalize_instinct_text(i.get("trigger", "")) == norm_trig
                and normalize_instinct_text(i.get("action", "")) == norm_act
            ]

            distinct_projects: Set[str] = set()
            for m in matching:
                p = m.get("project_id")
                if p:
                    distinct_projects.add(p)
            if project_id:
                distinct_projects.add(project_id)

            # Auto-promotion condition: observed in 2 or more distinct projects or already global
            is_already_global = any(m.get("scope") == "global" for m in matching)
            is_auto_promoted = is_already_global or len(distinct_projects) >= 2 or scope == "global"
            final_scope = "global" if is_auto_promoted else scope
            existing_pid = matching[0].get("project_id") if matching else None
            final_project_id = None if is_auto_promoted else (project_id or existing_pid)

            # Determine ID and confidence
            if matching:
                # Update first matching instinct
                target = matching[0]
                target_id = target["id"]
                new_conf = clamp_confidence(
                    float(target.get("confidence", confidence)) + (0.05 if is_auto_promoted else 0.02)
                )
                new_count = int(target.get("use_count", 0)) + use_count
                inst_dict = {
                    "id": target_id,
                    "trigger": clean_trigger,
                    "action": clean_action,
                    "confidence": new_conf,
                    "scope": final_scope,
                    "project_id": final_project_id,
                    "domain": domain or target.get("domain", "workflow"),
                    "use_count": new_count,
                    "created_at": target.get("created_at", now_iso),
                    "last_seen_at": now_iso,
                }
                # If auto-promoted, delete any redundant matching records
                for extra in matching[1:]:
                    self.storage.delete_instinct(extra["id"])
            else:
                target_id = instinct_id or f"inst_{uuid.uuid4().hex[:12]}"
                inst_dict = {
                    "id": target_id,
                    "trigger": clean_trigger,
                    "action": clean_action,
                    "confidence": clamp_confidence(confidence),
                    "scope": final_scope,
                    "project_id": final_project_id,
                    "domain": domain or "workflow",
                    "use_count": max(1, int(use_count)),
                    "created_at": now_iso,
                    "last_seen_at": now_iso,
                }

            self.storage.save_instinct(inst_dict)
            self._write_json_mirror(self.storage.get_all_instincts())
            return inst_dict

    def confirm_instinct(
        self, instinct_id: str, delta: float = 0.05
    ) -> Optional[Dict[str, Any]]:
        """
        Reinforces an instinct: increases confidence by delta (clamped to 0.95),
        increments use_count, and updates last_seen_at.
        """
        with self._lock:
            inst = self.get_instinct(instinct_id)
            if not inst:
                return None

            cur_conf = float(inst.get("confidence", 0.50))
            new_conf = clamp_confidence(cur_conf + delta)
            inst["confidence"] = new_conf
            inst["use_count"] = int(inst.get("use_count", 0)) + 1
            inst["last_seen_at"] = datetime.now(timezone.utc).isoformat()

            self.storage.save_instinct(inst)
            self._write_json_mirror(self.storage.get_all_instincts())
            return inst

    def reinforce_instinct(
        self, instinct_id: str, delta: float = 0.05
    ) -> Optional[Dict[str, Any]]:
        """
        Reinforces an instinct by ID: boosts confidence by delta (clamped to 0.95)
        and increments use_count. Alias/wrapper for confirm_instinct.
        """
        return self.confirm_instinct(instinct_id, delta=delta)

    def promote_instinct(self, instinct_id: str) -> Optional[Dict[str, Any]]:
        """
        Manually or programmatically promotes an instinct from 'project' to 'global' scope.
        Increments confidence by 0.05 (clamped to 0.95) and clears project_id.
        """
        with self._lock:
            inst = self.get_instinct(instinct_id)
            if not inst:
                return None

            inst["scope"] = "global"
            inst["project_id"] = None
            inst["confidence"] = clamp_confidence(float(inst.get("confidence", 0.50)) + 0.05)
            inst["last_seen_at"] = datetime.now(timezone.utc).isoformat()

            self.storage.save_instinct(inst)
            self._write_json_mirror(self.storage.get_all_instincts())
            return inst

    def delete_instinct(self, instinct_id: str) -> bool:
        """Deletes an instinct by ID from both SQLite and instincts.json."""
        with self._lock:
            ok = self.storage.delete_instinct(instinct_id)
            if ok:
                self._write_json_mirror(self.storage.get_all_instincts())
            return ok

    def prune_expired_instincts(
        self,
        max_age_days: float = PRUNE_MAX_AGE_DAYS,
        min_confidence: float = CONFIDENCE_PRUNE_THRESHOLD,
    ) -> int:
        """
        Confidence decay and pruning protocol:
        1. Inactivity decay: For instincts inactive > 7 days, reduce confidence by 0.05 per 7 days.
        2. Prune: Permanently deletes instincts where confidence <= min_confidence and age > max_age_days.
           Exempts seed instincts (inst_seed_*).
        Returns total count of pruned instincts.
        """
        with self._lock:
            all_inst = self.storage.get_all_instincts()
            now = time.time()
            to_delete: List[str] = []
            updated = False

            for inst in all_inst:
                i_id = inst["id"]
                # Skip seed rules from deletion
                if i_id.startswith("inst_seed_"):
                    continue

                c_epoch = parse_timestamp_epoch(inst.get("created_at"))
                s_epoch = parse_timestamp_epoch(inst.get("last_seen_at"))
                conf = float(inst.get("confidence", 0.5))

                # Step 1: Inactivity decay if last seen > 7 days ago
                days_inactive = max(0.0, (now - s_epoch) / 86400.0)
                if days_inactive >= 7.0:
                    decay_steps = int(days_inactive // 7.0)
                    decayed_conf = clamp_confidence(conf - (0.05 * decay_steps))
                    new_s_epoch = s_epoch + (decay_steps * 7.0 * 86400.0)
                    inst["last_seen_at"] = datetime.fromtimestamp(new_s_epoch, tz=timezone.utc).isoformat()
                    if decayed_conf != conf:
                        conf = decayed_conf
                        inst["confidence"] = conf
                    self.storage.save_instinct(inst)
                    updated = True

                # Step 2: Prune check
                age_days = (now - c_epoch) / 86400.0
                if conf <= min_confidence and age_days >= max_age_days:
                    to_delete.append(i_id)

            pruned_count = 0
            for i_id in to_delete:
                if self.storage.delete_instinct(i_id):
                    pruned_count += 1
                    updated = True

            if updated or pruned_count > 0:
                self._write_json_mirror(self.storage.get_all_instincts())

            return pruned_count

    def close(self):
        """Closes underlying storage connection."""
        if hasattr(self.storage, "close_connection"):
            self.storage.close_connection()


# Global Module Singleton Accessor
_global_instinct_engine: Optional[InstinctEngine] = None
_global_engine_lock = threading.Lock()


def get_instinct_engine(
    data_dir: Optional[str] = None,
    storage_manager: Optional[StorageManager] = None,
) -> InstinctEngine:
    """Returns the process-wide InstinctEngine singleton."""
    global _global_instinct_engine
    with _global_engine_lock:
        if _global_instinct_engine is None:
            _global_instinct_engine = InstinctEngine(
                data_dir=data_dir, storage_manager=storage_manager
            )
        elif data_dir and os.path.abspath(data_dir) != os.path.abspath(_global_instinct_engine.data_dir):
            _global_instinct_engine = InstinctEngine(
                data_dir=data_dir, storage_manager=storage_manager
            )
        return _global_instinct_engine
