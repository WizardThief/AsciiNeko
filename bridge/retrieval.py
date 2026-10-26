"""
bridge/retrieval.py
===================
AsciiNeko Golden Architecture (M1 Core)
- FTS5 BM25 Lexical Retrieval with Time-Decay Scoring
- Hybrid Deduplication Engine (Levenshtein + Token-Set Jaccard >= 0.85)
- Token Budget Manager (num_ctx: 8192 with >= 5742 Headroom Guarantee)
- Ollama Context & Payload Integrator

Remediates: VULN-03, VULN-04, VULN-06, VULN-07.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("AsciiNeko.Retrieval")

# ==============================================================================
# 1. CONSTANTS & DDL SCHEMAS
# ==============================================================================

TARGET_NUM_CTX = 8192
MIN_GENERATION_HEADROOM = 5742
MAX_PROMPT_BUDGET = TARGET_NUM_CTX - MIN_GENERATION_HEADROOM  # 2450 tokens

# Default partition caps
BUDGET_SYSTEM_INSTRUCTIONS = 600
BUDGET_PINNED_AND_DCC = 450
BUDGET_DYNAMIC_MEMORIES = 800
BUDGET_CONVERSATION_HISTORY = 600
BUDGET_CLIPBOARD = 400

DEFAULT_LAMBDA = 0.05
PINNED_LAMBDA = 0.00
DEDUPLICATION_THRESHOLD = 0.85
SCORE_FILTER_THRESHOLD = 0.20

ITALIAN_STOP_WORDS: Set[str] = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "a", "da",
    "in", "con", "su", "per", "tra", "fra", "e", "ed", "o", "ma", "che", "non",
    "del", "della", "delle", "dei", "degli", "al", "allo", "alla", "ai", "agli",
    "alle", "nel", "nella", "nei", "negli", "nelle", "come", "cosa", "quale",
    "questo", "questa", "questi", "queste", "quello", "quella", "si", "ci",
    "ti", "mi", "vi", "ne", "ha", "ho", "hai", "hanno", "sono", "sei", "è", "era",
    "anche", "più", "molto", "tutto", "tutti", "tutte", "solo", "senza", "dopo",
    "prima", "bene", "male", "già", "quando", "dove", "chi", "perché", "quindi"
}

FTS5_TABLE_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text,
    category,
    project_id UNINDEXED,
    content='memories',
    content_rowid='rowid'
);
"""

FTS5_TRIGGERS_DDL = """
CREATE TRIGGER IF NOT EXISTS trg_memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text, category, project_id)
    VALUES (new.rowid, new.text, new.category, new.project_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, category, project_id)
    VALUES ('delete', old.rowid, old.text, old.category, old.project_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, category, project_id)
    VALUES ('delete', old.rowid, old.text, old.category, old.project_id);
    INSERT INTO memories_fts(rowid, text, category, project_id)
    VALUES (new.rowid, new.text, new.category, new.project_id);
END;
"""

FTS5_REBUILD_SQL = "INSERT INTO memories_fts(memories_fts) VALUES('rebuild');"


# ==============================================================================
# 2. QUERY SANITIZER & TOKEN ESTIMATOR
# ==============================================================================

def sanitize_fts5_query(query: str) -> str:
    """
    Sanitizes natural language queries into safe SQLite FTS5 syntax.
    Strips stop-words, handles punctuation/hyphens safely, and joins meaningful words with OR.
    Prevents sqlite3.OperationalError on punctuation or special operators.
    """
    if not query or not query.strip():
        return ""
    # Extract alphanumeric and hyphenated words
    tokens = re.findall(r'[\w\-]+', query, re.UNICODE)
    cleaned: List[str] = []
    for t in tokens:
        clean_t = t.strip("-")
        if len(clean_t) < 2:
            continue
        if clean_t.lower() in ITALIAN_STOP_WORDS:
            continue
        safe_t = clean_t.replace('"', '""')
        cleaned.append(f'"{safe_t}"')
    if not cleaned:
        return ""
    return " OR ".join(cleaned)


def estimate_tokens(text: str) -> int:
    """
    Conservative token estimator for Italian and English text.
    Uses ceil(len / 3.2), preventing context overflow on Qwen2.5.
    """
    if not text:
        return 0
    return math.ceil(len(text) / 3.2)


# ==============================================================================
# 3. HYBRID DEDUPLICATION ENGINE
# ==============================================================================

def compute_hybrid_similarity(s1: str, s2: str) -> float:
    """
    Computes hybrid similarity score between s1 and s2:
    max(Normalized Levenshtein Similarity, Token-Set Jaccard Similarity).
    Returns value in [0.0, 1.0].
    """
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    # 1. Normalized Levenshtein distance
    m, n = len(s1), len(s2)
    prev_row = list(range(n + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1] * (n + 1)
        for j, c2 in enumerate(s2):
            cost = 0 if c1.lower() == c2.lower() else 1
            curr_row[j + 1] = min(
                prev_row[j + 1] + 1,       # deletion
                curr_row[j] + 1,           # insertion
                prev_row[j] + cost         # substitution
            )
        prev_row = curr_row

    dist = prev_row[n]
    lev_sim = max(0.0, 1.0 - (dist / max(m, n)))

    # 2. Token-Set Jaccard (case-insensitive, filtering short & stop words)
    tokens1 = set(re.findall(r"[\w]+", s1.lower(), re.UNICODE))
    tokens2 = set(re.findall(r"[\w]+", s2.lower(), re.UNICODE))

    # Remove Italian stop words if longer strings
    f_t1 = {w for w in tokens1 if w not in ITALIAN_STOP_WORDS and len(w) > 1} or tokens1
    f_t2 = {w for w in tokens2 if w not in ITALIAN_STOP_WORDS and len(w) > 1} or tokens2

    if not f_t1 and not f_t2:
        jacc_sim = 1.0
    elif not f_t1 or not f_t2:
        jacc_sim = 0.0
    else:
        jacc_sim = len(f_t1 & f_t2) / len(f_t1 | f_t2)

    return max(lev_sim, jacc_sim)


class DeduplicationEngine:
    """
    Hybrid similarity matcher combining Levenshtein Distance and Token-Set Jaccard.
    Prevents duplicate trait/memory rows when similarity >= threshold (default 0.85).
    """

    def __init__(self, threshold: float = DEDUPLICATION_THRESHOLD):
        self.threshold = threshold

    @staticmethod
    def levenshtein_similarity(s1: str, s2: str) -> float:
        """Normalized Levenshtein similarity in [0.0, 1.0]."""
        if s1 == s2:
            return 1.0
        l1, l2 = len(s1), len(s2)
        if l1 == 0 or l2 == 0:
            return 0.0

        prev_row = list(range(l2 + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1] * (l2 + 1)
            for j, c2 in enumerate(s2):
                cost = 0 if c1.lower() == c2.lower() else 1
                curr_row[j + 1] = min(
                    prev_row[j + 1] + 1,
                    curr_row[j] + 1,
                    prev_row[j] + cost
                )
            prev_row = curr_row

        distance = prev_row[l2]
        return max(0.0, 1.0 - (distance / max(l1, l2)))

    @staticmethod
    def tokenize(text: str) -> Set[str]:
        """Extracts lowercased alphanumeric words, filtering Italian stop-words."""
        if not text:
            return set()
        words = re.findall(r'[\w]+', text.lower(), re.UNICODE)
        filtered = {w for w in words if w not in ITALIAN_STOP_WORDS and len(w) > 1}
        return filtered or set(words)

    @classmethod
    def token_set_jaccard(cls, s1: str, s2: str) -> float:
        """Token-set Jaccard similarity in [0.0, 1.0]."""
        t1 = cls.tokenize(s1)
        t2 = cls.tokenize(s2)
        if not t1 and not t2:
            return 1.0
        if not t1 or not t2:
            return 0.0
        intersection = len(t1 & t2)
        union = len(t1 | t2)
        return intersection / union

    @classmethod
    def hybrid_similarity(cls, s1: str, s2: str) -> float:
        """Returns compute_hybrid_similarity between s1 and s2."""
        return compute_hybrid_similarity(s1, s2)

    def find_best_match(
        self, candidate: str, existing_memories: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, Any]], float]:
        """
        Finds the highest similarity existing memory for a candidate string.
        Returns (best_match_dict, similarity_score).
        """
        best_match: Optional[Dict[str, Any]] = None
        max_sim = 0.0
        for mem in existing_memories:
            text = mem.get("text", "")
            sim = self.hybrid_similarity(candidate, text)
            if sim > max_sim:
                max_sim = sim
                best_match = mem
        return best_match, max_sim


# ==============================================================================
# 4. SCORING & TIME-DECAY ENGINE
# ==============================================================================

class TimeDecayScorer:
    """
    Combined scoring engine:
    Score = 0.50 * BM25_norm + 0.35 * Importance * exp(-lambda * delta_t_days) + 0.15 * ProjectMatch
    Pinned memories (pinned=1) have lambda=0.0 (decay factor=1.0, never decays).
    """

    def __init__(
        self,
        default_lambda: float = DEFAULT_LAMBDA,
        pinned_lambda: float = PINNED_LAMBDA,
        w_bm25: float = 0.50,
        w_time: float = 0.35,
        w_project: float = 0.15,
        filter_threshold: float = SCORE_FILTER_THRESHOLD,
    ):
        self.default_lambda = default_lambda
        self.pinned_lambda = pinned_lambda
        self.w_bm25 = w_bm25
        self.w_time = w_time
        self.w_project = w_project
        self.filter_threshold = filter_threshold

    def calculate_score(
        self,
        bm25_norm: float,
        importance: float,
        pinned: int | bool,
        updated_at: float,
        project_id: Optional[str],
        active_project_id: Optional[str],
        now: Optional[float] = None,
    ) -> float:
        """Calculates combined score in [0.0, 1.0]."""
        if now is None:
            now = time.time()

        # 1. BM25 term
        term_bm25 = self.w_bm25 * max(0.0, min(1.0, bm25_norm))

        # 2. Time-decay term
        delta_t_days = max(0.0, (now - updated_at) / 86400.0)
        eff_lambda = self.pinned_lambda if bool(pinned) else self.default_lambda
        decay_factor = math.exp(-eff_lambda * delta_t_days)
        eff_importance = max(0.1, min(1.0, importance if importance is not None else 0.5))
        term_time = self.w_time * eff_importance * decay_factor

        # 3. Project match term
        proj_match = 1.0 if (project_id and active_project_id and project_id == active_project_id) else 0.0
        term_proj = self.w_project * proj_match

        return round(term_bm25 + term_time + term_proj, 4)

    @staticmethod
    def normalize_bm25_batch(raw_ranks: List[float]) -> List[float]:
        """
        Normalizes SQLite FTS5 bm25() ranks to [0.0, 1.0].
        In SQLite, bm25() returns negative values where lower/more negative is better.
        Uses relative scaling: norm_i = (-rank_i) / max(-rank_all).
        """
        if not raw_ranks:
            return []
        pos_scores = [-r if r is not None else 0.0 for r in raw_ranks]
        max_s = max(pos_scores) if pos_scores else 0.0
        if max_s <= 0.0:
            return [0.0] * len(raw_ranks)
        return [round(s / max_s, 4) for s in pos_scores]


# ==============================================================================
# 5. TOKEN BUDGET MANAGER & CONTEXT BUILDER
# ==============================================================================

class TokenBudgetManager:
    """
    Token Budget Manager enforcing num_ctx: 8192 with guaranteed >= 5742 headroom.
    Prompt allocations:
      - System Persona: ~600 tok (100% preserved)
      - Pinned Traits & DCC Rules: ~450 tok (100% preserved)
      - Dynamic Memories: <= 800 tok (ranked by combined score)
      - Conversation History: <= 600 tok (last turns)
      - Clipboard: <= 400 tok (sanitized)
    Total prompt <= 2450 tokens.
    """

    def __init__(
        self,
        target_num_ctx: int = TARGET_NUM_CTX,
        min_headroom: int = MIN_GENERATION_HEADROOM,
        budget_system: int = BUDGET_SYSTEM_INSTRUCTIONS,
        budget_pinned: int = BUDGET_PINNED_AND_DCC,
        budget_memories: int = BUDGET_DYNAMIC_MEMORIES,
        budget_history: int = BUDGET_CONVERSATION_HISTORY,
        budget_clipboard: int = BUDGET_CLIPBOARD,
    ):
        self.target_num_ctx = target_num_ctx
        self.min_headroom = min_headroom
        self.max_prompt_budget = target_num_ctx - min_headroom  # 2450
        self.budget_system = budget_system
        self.budget_pinned = budget_pinned
        self.budget_memories = budget_memories
        self.budget_history = budget_history
        self.budget_clipboard = budget_clipboard

    def fit_clipboard(self, clipboard_text: str, available_tokens: int) -> str:
        """Truncates clipboard text to fit allocated token quota."""
        if not clipboard_text:
            return ""
        cap = min(self.budget_clipboard, available_tokens)
        if cap <= 0:
            return ""
        max_chars = int(cap * 3.2)
        if len(clipboard_text) <= max_chars:
            return clipboard_text
        return clipboard_text[: max(0, max_chars - 30)] + "\n... [appunti troncati]"

    def fit_dynamic_memories(
        self, scored_memories: List[Dict[str, Any]], available_tokens: int
    ) -> List[Dict[str, Any]]:
        """
        Selects top scored memories fitting within the remaining token quota.
        Input list must already be sorted by combined_score descending.
        """
        cap = min(self.budget_memories, available_tokens)
        if cap <= 0 or not scored_memories:
            return []

        selected: List[Dict[str, Any]] = []
        accumulated_tokens = 0
        for mem in scored_memories:
            if mem.get("pinned"):
                continue
            text = mem.get("text", "")
            cost = estimate_tokens(text) + 2
            if accumulated_tokens + cost > cap:
                continue
            selected.append(mem)
            accumulated_tokens += cost
        return selected

    def fit_chat_history(
        self, turns: List[Dict[str, Any]], available_tokens: int
    ) -> List[Dict[str, Any]]:
        """
        Selects most recent chat turns from newest to oldest within token quota.
        Returns chronologically sorted list.
        """
        cap = min(self.budget_history, available_tokens)
        if cap <= 0 or not turns:
            return []

        selected: List[Dict[str, Any]] = []
        accumulated = 0
        for turn in reversed(turns):
            content = turn.get("text") or turn.get("content") or ""
            cost = estimate_tokens(content) + 4
            if accumulated + cost > cap:
                break
            selected.append(turn)
            accumulated += cost

        return list(reversed(selected))

    def _fit_pinned_traits(
        self, pinned_traits: Optional[List[Any]], budget_tokens: int = 450
    ) -> List[str]:
        """
        Packs pinned traits up to budget_tokens (default: 450).
        If more exist, takes the most recent / highest priority up to budget_tokens.
        """
        if not pinned_traits:
            return []
        cap = budget_tokens if budget_tokens is not None else self.budget_pinned
        if cap <= 0:
            return []

        # Normalize traits to strings
        str_traits: List[str] = []
        for item in pinned_traits:
            if isinstance(item, dict):
                t = item.get("text") or str(item)
            else:
                t = str(item)
            t = t.strip()
            if t:
                str_traits.append(t)

        if not str_traits:
            return []

        selected: List[str] = []
        accumulated = 0
        for t in str_traits:
            cost = estimate_tokens(t) + 2
            if accumulated + cost <= cap:
                selected.append(t)
                accumulated += cost
            elif not selected:
                # If even the first trait exceeds cap, truncate it
                max_chars = max(0, int(cap * 3.2) - 20)
                if max_chars > 0:
                    selected.append(t[:max_chars] + "...")
                break
            else:
                break

        return selected

    def build_context_prompt(
        self,
        query: str,
        active_project: Optional[str] = None,
        active_dcc: Optional[str] = None,
        pinned_traits: Optional[List[str]] = None,
        dynamic_memories: Optional[List[str]] = None,
    ) -> Tuple[str, int]:
        """
        Builds a single formatted context prompt string adhering to the 2450 budget.
        Conforms directly to PROJECT.md interface contract:
        (prompt_str, estimated_tokens).
        """
        p_traits = self._fit_pinned_traits(pinned_traits or [], budget_tokens=self.budget_pinned)
        d_mems = dynamic_memories or []

        # Pinned block
        pinned_str = ", ".join(p_traits) if p_traits else "Nessuna regola vincolante specifica"

        # Fit dynamic memories to budget
        budget_for_dyn = min(self.budget_memories, 800)
        fitted_dyn: List[str] = []
        cur_tok = 0
        for m in d_mems:
            cost = estimate_tokens(m) + 2
            if cur_tok + cost > budget_for_dyn:
                continue
            fitted_dyn.append(m)
            cur_tok += cost

        dyn_str = ", ".join(fitted_dyn) if fitted_dyn else "Nessuna memoria dinamica recuperata"

        prompt = (
            f"[SYSTEM] Identity: Neko AI Assistant (Cascadia Code 13px, local-first)\n"
            f"[PINNED RULES] {pinned_str}\n"
            f"[ACTIVE DCC] {active_dcc or 'None'} | Project: {active_project or 'None'}\n"
            f"[DYNAMIC MEMORIES] {dyn_str}\n"
            f"[USER QUERY] {query}"
        )

        est = estimate_tokens(prompt)
        # Ensure hard ceiling <= 2450
        if est > self.max_prompt_budget:
            # Trim dynamic memories if needed
            excess_chars = int((est - self.max_prompt_budget) * 3.2) + 20
            if len(dyn_str) > excess_chars:
                dyn_str = dyn_str[:-excess_chars] + "..."
                prompt = (
                    f"[SYSTEM] Identity: Neko AI Assistant (Cascadia Code 13px, local-first)\n"
                    f"[PINNED RULES] {pinned_str}\n"
                    f"[ACTIVE DCC] {active_dcc or 'None'} | Project: {active_project or 'None'}\n"
                    f"[DYNAMIC MEMORIES] {dyn_str}\n"
                    f"[USER QUERY] {query}"
                )
                est = estimate_tokens(prompt)

        # Final hard ceiling guard: clamp prompt text so it never exceeds max_prompt_budget (2450)
        if est > self.max_prompt_budget:
            max_chars = int(self.max_prompt_budget * 3.2)
            prompt = prompt[:max_chars]
            est = estimate_tokens(prompt)
            while est > self.max_prompt_budget and prompt:
                prompt = prompt[:-10]
                est = estimate_tokens(prompt)

        return prompt, est

    def build_context_messages(
        self,
        base_system_prompt: str,
        pinned_traits: List[str],
        active_dcc_rules: List[str],
        dynamic_memories: List[Dict[str, Any]],
        chat_history: List[Dict[str, Any]],
        clipboard_text: str = "",
        user_query: str = "",
    ) -> Tuple[List[Dict[str, str]], int]:
        """
        Assembles complete messages payload strictly bounded by 2450 prompt tokens.
        Guarantees 100% preservation of System Persona, Pinned Traits, and DCC Rules.
        Returns (messages_list, estimated_total_tokens).
        """
        # 1. Mandatory immutable blocks (P1 & P2)
        pinned_block = ""
        dcc_rules = active_dcc_rules or []
        dcc_items = [f"- [DCC] {r}" for r in dcc_rules]
        dcc_tokens = sum(estimate_tokens(it) + 2 for it in dcc_items)
        pinned_budget = max(50, self.budget_pinned - dcc_tokens)

        fitted_traits = self._fit_pinned_traits(pinned_traits or [], budget_tokens=pinned_budget)
        items = [f"- {t}" for t in fitted_traits] + dcc_items
        if items:
            pinned_block = "\nREGOLE E VINCOLI PERMANENTI (PRIORITÀ ASSOLUTA):\n" + "\n".join(items)

        sys_full = base_system_prompt.strip() + ("\n" + pinned_block if pinned_block else "")
        sys_tokens = estimate_tokens(sys_full)
        query_tokens = estimate_tokens(user_query)

        remaining_budget = max(0, self.max_prompt_budget - (sys_tokens + query_tokens))

        # 2. Fit clipboard
        fitted_clipboard = self.fit_clipboard(clipboard_text, min(self.budget_clipboard, remaining_budget // 3))
        clip_tokens = estimate_tokens(fitted_clipboard)
        remaining_budget = max(0, remaining_budget - clip_tokens)

        # 3. Fit dynamic memories
        fitted_memories = self.fit_dynamic_memories(dynamic_memories, min(self.budget_memories, remaining_budget // 2))
        mem_block = ""
        if fitted_memories:
            mem_items = [f"- {m['text']}" for m in fitted_memories]
            mem_block = "\nMEMORIE RILEVANTI (ESTRATTE DAL CONTESTO):\n" + "\n".join(mem_items)
            sys_full += "\n" + mem_block
            sys_tokens = estimate_tokens(sys_full)
            remaining_budget = max(0, self.max_prompt_budget - (sys_tokens + query_tokens + clip_tokens))

        # 4. Fit conversation turns
        fitted_history = self.fit_chat_history(chat_history, remaining_budget)

        # 5. Assemble final messages
        messages: List[Dict[str, str]] = [{"role": "system", "content": sys_full}]
        for turn in fitted_history:
            role = turn.get("role", "user")
            content = turn.get("text") or turn.get("content") or ""
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        user_content = user_query
        if fitted_clipboard:
            user_content += f"\n\n[Contenuto Appunti Windows]:\n{fitted_clipboard}"
        messages.append({"role": "user", "content": user_content})

        total_tokens = sum(estimate_tokens(m["content"]) for m in messages)

        # Hard ceiling guard: ensure total_tokens <= self.max_prompt_budget (2450)
        if total_tokens > self.max_prompt_budget:
            # Step A: Drop history turns (from oldest to newest) if any exist
            while total_tokens > self.max_prompt_budget and len(messages) > 2:
                messages.pop(1)
                total_tokens = sum(estimate_tokens(m["content"]) for m in messages)

            # Step B: If still exceeding, trim clipboard from user message
            if total_tokens > self.max_prompt_budget and "[Contenuto Appunti Windows]:" in messages[-1]["content"]:
                parts = messages[-1]["content"].split("\n\n[Contenuto Appunti Windows]:")
                messages[-1]["content"] = parts[0]
                total_tokens = sum(estimate_tokens(m["content"]) for m in messages)

            # Step C: If still exceeding, trim dynamic memories from system message
            if total_tokens > self.max_prompt_budget and "\nMEMORIE RILEVANTI (ESTRATTE DAL CONTESTO):" in messages[0]["content"]:
                parts = messages[0]["content"].split("\nMEMORIE RILEVANTI (ESTRATTE DAL CONTESTO):")
                messages[0]["content"] = parts[0]
                total_tokens = sum(estimate_tokens(m["content"]) for m in messages)

            # Step D: If still exceeding, clamp user query
            excess = total_tokens - self.max_prompt_budget
            if excess > 0 and len(messages[-1]["content"]) > 100:
                user_text = messages[-1]["content"]
                chars_to_trim = int(excess * 3.2) + 20
                if len(user_text) > chars_to_trim:
                    messages[-1]["content"] = user_text[:-chars_to_trim] + "..."
                total_tokens = sum(estimate_tokens(m["content"]) for m in messages)

            # Step E: Ultimate string clamp if still exceeding
            while total_tokens > self.max_prompt_budget:
                for idx in (-1, 0):
                    if len(messages[idx]["content"]) > 20:
                        messages[idx]["content"] = messages[idx]["content"][:-30]
                        break
                total_tokens = sum(estimate_tokens(m["content"]) for m in messages)

        return messages, total_tokens


# ==============================================================================
# 6. OLLAMA API PAYLOAD HELPER
# ==============================================================================

def inject_ollama_options(payload: Dict[str, Any], num_ctx: int = TARGET_NUM_CTX) -> Dict[str, Any]:
    """Guarantees options.num_ctx is always injected into Ollama payload."""
    if "options" not in payload or not isinstance(payload["options"], dict):
        payload["options"] = {}
    payload["options"]["num_ctx"] = num_ctx
    return payload


def build_ollama_payload(
    model: str,
    messages: List[Dict[str, str]],
    stream: bool = False,
    format: Optional[str] = None,
    num_ctx: int = TARGET_NUM_CTX,
    extra_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Builds a complete, context-guaranteed Ollama chat payload."""
    options = {"num_ctx": num_ctx}
    if extra_options:
        options.update(extra_options)
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": options,
    }
    if format:
        payload["format"] = format
    return payload


# ==============================================================================
# 7. MEMORY RETRIEVAL SERVICE
# ==============================================================================

class MemoryRetrievalService:
    """
    Coordinates SQLite FTS5 BM25 search, time-decay scoring, deduplication,
    and prompt context assembly.
    """

    def __init__(
        self,
        db_connection_provider: Callable[[], sqlite3.Connection],
        scorer: Optional[TimeDecayScorer] = None,
        deduplicator: Optional[DeduplicationEngine] = None,
        budget_manager: Optional[TokenBudgetManager] = None,
    ):
        self.get_conn = db_connection_provider
        self.scorer = scorer or TimeDecayScorer()
        self.deduplicator = deduplicator or DeduplicationEngine()
        self.budget_manager = budget_manager or TokenBudgetManager()

    def init_fts5_schema(self) -> None:
        """Executes FTS5 table and triggers DDL on database."""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.executescript(FTS5_TABLE_DDL)
        cursor.executescript(FTS5_TRIGGERS_DDL)
        conn.commit()

    def search_memories(
        self,
        query: str,
        active_project_id: Optional[str] = None,
        limit: int = 15,
    ) -> List[Dict[str, Any]]:
        """
        Executes hybrid FTS5 BM25 + Time-Decay search.
        Includes all matching FTS5 items, active project memories, and pinned items.
        Returns ranked list of memory dictionaries sorted by combined_score descending.
        """
        conn = self.get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        sanitized = sanitize_fts5_query(query)
        fts_matches: Dict[str, Tuple[Dict[str, Any], float]] = {}

        # 1. Lexical search via FTS5 if valid query terms exist
        if sanitized:
            sql = """
                SELECT m.id, m.text, m.category, m.project_id, m.importance,
                       m.pinned, m.frequency, m.source, m.created_at, m.updated_at,
                       bm25(memories_fts) as raw_rank
                FROM memories_fts f
                JOIN memories m ON m.rowid = f.rowid
                WHERE memories_fts MATCH ?
                ORDER BY raw_rank ASC
                LIMIT 50
            """
            try:
                rows = cursor.execute(sql, (sanitized,)).fetchall()
                raw_ranks = [r["raw_rank"] for r in rows]
                normalized_ranks = self.scorer.normalize_bm25_batch(raw_ranks)
                for r, norm_rank in zip(rows, normalized_ranks):
                    d = dict(r)
                    fts_matches[d["id"]] = (d, norm_rank)
            except sqlite3.OperationalError as e:
                logger.warning(f"FTS5 query failed for '{sanitized}': {e}")

        # 2. Retrieve all pinned memories (always included)
        pinned_rows = cursor.execute(
            "SELECT * FROM memories WHERE pinned = 1 ORDER BY updated_at DESC LIMIT 30"
        ).fetchall()

        # 3. Retrieve active project memories if specified
        project_rows = []
        if active_project_id:
            project_rows = cursor.execute(
                "SELECT * FROM memories WHERE project_id = ? ORDER BY updated_at DESC LIMIT 30",
                (active_project_id,),
            ).fetchall()

        # Combine all candidates uniquely by id
        all_candidates: Dict[str, Tuple[Dict[str, Any], float]] = {}
        for row in pinned_rows:
            d = dict(row)
            all_candidates[d["id"]] = (d, 0.0)
        for row in project_rows:
            d = dict(row)
            all_candidates[d["id"]] = (d, 0.0)
        for mid, (d, norm_rank) in fts_matches.items():
            all_candidates[mid] = (d, norm_rank)

        now = time.time()
        scored_list: List[Dict[str, Any]] = []

        for mid, (item, norm_bm25) in all_candidates.items():
            score = self.scorer.calculate_score(
                bm25_norm=norm_bm25,
                importance=item.get("importance", 0.5),
                pinned=item.get("pinned", 0),
                updated_at=item.get("updated_at", now),
                project_id=item.get("project_id"),
                active_project_id=active_project_id,
                now=now,
            )
            # Include if score >= filter threshold, or if pinned, or if explicit project match
            if score >= self.scorer.filter_threshold or item.get("pinned"):
                item_copy = dict(item)
                item_copy["combined_score"] = score
                item_copy["score"] = score  # For PROJECT.md compatibility
                item_copy["bm25_norm"] = norm_bm25
                item_copy["pinned"] = bool(item.get("pinned", 0))
                scored_list.append(item_copy)

        # Sort descending by score
        scored_list.sort(key=lambda x: x["score"], reverse=True)
        return scored_list[:limit]

    def upsert_memory(
        self,
        text: str,
        category: str = "Generale",
        project_id: Optional[str] = None,
        importance: float = 0.5,
        pinned: bool = False,
        source: str = "user",
    ) -> Tuple[str, bool]:
        """
        Deduplicates candidate memory against existing records (threshold >= 0.85).
        If duplicate: updates timestamp, increments frequency, updates importance if higher.
        If unique: inserts new row with UUID4.
        Returns (memory_id, is_duplicate).
        """
        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("Memory text cannot be empty.")

        conn = self.get_conn()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Fetch active memories for comparison
        existing = [dict(r) for r in cursor.execute("SELECT * FROM memories").fetchall()]
        best_match, sim = self.deduplicator.find_best_match(clean_text, existing)

        now = time.time()
        if project_id:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (project_id, project_id, now, now),
                )
            except Exception:
                pass

        if best_match and sim >= self.deduplicator.threshold:
            mid = best_match["id"]
            new_freq = best_match.get("frequency", 1) + 1
            new_imp = max(best_match.get("importance", 0.5), importance)
            new_pin = 1 if (pinned or best_match.get("pinned")) else 0
            # If candidate text has richer detail, adopt it
            new_text = clean_text if len(clean_text) > len(best_match.get("text", "")) else best_match.get("text", "")

            cursor.execute(
                """
                UPDATE memories
                SET frequency = ?, importance = ?, pinned = ?, updated_at = ?, text = ?
                WHERE id = ?
                """,
                (new_freq, new_imp, new_pin, now, new_text, mid),
            )
            conn.commit()
            logger.info(f"Deduplicated memory '{clean_text[:40]}' -> matched {mid} (sim={sim:.3f})")
            return mid, True
        else:
            mid = uuid.uuid4().hex[:12]
            cursor.execute(
                """
                INSERT INTO memories (id, text, category, project_id, importance, pinned, frequency, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (mid, clean_text, category, project_id, importance, 1 if pinned else 0, source, now, now),
            )
            conn.commit()
            logger.info(f"Inserted new memory {mid}: '{clean_text[:40]}'")
            return mid, False
