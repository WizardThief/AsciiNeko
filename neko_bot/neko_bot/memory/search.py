"""
neko_bot.memory.search - Zero-Dependency BM25/TF-IDF Query Engine with Recency Decay.

Features:
- Pure Python using math, re, collections.Counter, datetime (no heavy external ML/vector DB dependencies).
- Multilingual tokenization with Italian and English stop-word filtering and stem normalization.
- Combined ranking score:
    FinalScore(doc) = w_sim * S_term(doc, q) + w_imp * (importance / 5.0) + w_rec * exp(-lambda * delta_t)
- Specialized recency half-lives per memory level (Semantic: 30d, Project: 14d, Episode: 7d).
- Exact phrase bonus and tag match weighting.
"""

from collections import Counter
from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Weights according to survey and architectural specification
W_SIM = 0.60
W_IMP = 0.25
W_REC = 0.15

# Recency decay half-lives in days
HALF_LIFE_DAYS = {
    "semantic": 30.0,
    "fact": 30.0,
    "project": 14.0,
    "episodic": 7.0,
    "episode": 7.0,
    "default": 14.0,
}

# Common Italian and English stop words
STOP_WORDS: Set[str] = {
    # Italian
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "del", "dello", "della",
    "dei", "degli", "delle", "al", "allo", "alla", "ai", "agli", "alle", "dal", "dallo",
    "dalla", "dai", "dagli", "dalle", "nel", "nello", "nella", "nei", "negli", "nelle",
    "sul", "sullo", "sulla", "sui", "sugli", "sulle", "di", "a", "da", "in", "con", "su",
    "per", "tra", "fra", "e", "ed", "o", "od", "ma", "se", "perche", "perché", "che",
    "chi", "cui", "non", "piu", "più", "sono", "sei", "è", "era", "stato", "stata", "solo",
    # English
    "the", "a", "an", "and", "or", "to", "in", "on", "for", "of", "with", "at", "by",
    "is", "it", "this", "that", "from", "be", "as", "are", "was", "were",
}

# Common Italian suffixes for lightweight rule-based stemming
ITALIAN_SUFFIXES = [
    "amento", "amenti", "azione", "azioni", "mente",
    "ando", "endo", "abile", "abili", "ibile", "ibili",
    "are", "ere", "ire", "ato", "ata", "ati", "ate",
    "uto", "uta", "uti", "ute", "ito", "ita", "iti", "ite",
]


def tokenize(text: str, remove_stopwords: bool = True, stem: bool = True) -> List[str]:
    """Tokenizes text into normalized words with optional stopword removal and stemming."""
    if not text:
        return []
    raw_tokens = re.findall(r"\w+", text.lower())
    result: List[str] = []
    for token in raw_tokens:
        if remove_stopwords and token in STOP_WORDS:
            continue
        if stem and len(token) > 4:
            token = stem_token(token)
        result.append(token)
    return result


def stem_token(token: str) -> str:
    """Applies lightweight rule-based Italian suffix stripping."""
    for sfx in ITALIAN_SUFFIXES:
        if token.endswith(sfx) and len(token) - len(sfx) >= 3:
            return token[:-len(sfx)]
    # Strip basic plural vowels (e/i -> a/o or stem) if length >= 4
    if token.endswith(("i", "e")) and len(token) >= 4:
        return token[:-1]
    return token


def parse_timestamp_safe(ts_str: Optional[str]) -> datetime:
    """Parses ISO-8601 UTC timestamp safely, returning current UTC time if invalid or None."""
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        clean = str(ts_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def compute_recency_score(ts_str: Optional[str], level: Optional[str]) -> float:
    """
    Computes exponential recency decay:
    R = exp(-lambda * delta_t) where lambda = ln(2) / T_half
    """
    dt = parse_timestamp_safe(ts_str)
    now = datetime.now(timezone.utc)
    delta_seconds = max(0.0, (now - dt).total_seconds())
    delta_days = delta_seconds / 86400.0

    lvl = str(level).lower() if level else "default"
    half_life = HALF_LIFE_DAYS.get(lvl, HALF_LIFE_DAYS["default"])
    lam = math.log(2.0) / half_life
    return math.exp(-lam * delta_days)


def extract_document_text(entry: Dict[str, Any], level: Optional[str]) -> Tuple[str, List[str], int, str]:
    """
    Extracts searchable text, tags, importance (1-5), and timestamp string from an entry dictionary.
    Returns: (text_corpus, tags_list, importance_1_to_5, timestamp_str)
    """
    tags: List[str] = []
    importance = 3
    ts_str = entry.get("updated_at") or entry.get("created_at") or entry.get("timestamp")
    lvl = str(level).lower() if level else "default"

    if lvl in ("semantic", "fact"):
        content = str(entry.get("content") or "")
        category = str(entry.get("category") or "")
        raw_tags = entry.get("tags") or []
        tags = [str(t) for t in raw_tags if t is not None] if isinstance(raw_tags, (list, tuple, set)) else []
        raw_imp = entry.get("importance")
        if raw_imp is not None:
            try:
                importance = int(raw_imp)
            except (ValueError, TypeError):
                importance = 3
        text = f"{category} {content} {' '.join(tags)}"

    elif lvl in ("project", "project_deadline"):
        pname = str(entry.get("project_name") or "")
        milestone = str(entry.get("milestone") or "")
        notes = str(entry.get("notes") or "")
        risk = str(entry.get("risk_level") or "medium")
        raw_deps = entry.get("dependencies") or []
        deps = [str(d) for d in raw_deps if d is not None] if isinstance(raw_deps, (list, tuple, set)) else []
        tags = [t for t in [pname, risk] if t] + deps
        # Map risk to importance
        risk_map = {"critical": 5, "high": 4, "medium": 3, "low": 2}
        importance = risk_map.get(risk.lower(), 3)
        text = f"{pname} {milestone} {notes} {risk} {' '.join(tags)}"

    elif lvl in ("episodic", "episode"):
        summary = str(entry.get("summary") or "")
        full_text = str(entry.get("full_text") or "")
        event_type = str(entry.get("event_type") or "")
        outcome = str(entry.get("outcome") or "neutral")
        raw_tags = entry.get("context_tags") or []
        tags = [str(t) for t in raw_tags if t is not None] if isinstance(raw_tags, (list, tuple, set)) else []
        # Slightly elevate failed episodes so bot learns from past mistakes
        importance = 4 if outcome == "failure" else (3 if outcome == "success" else 2)
        text = f"{event_type} {summary} {full_text} {outcome} {' '.join(tags)}"

    else:
        # Generic dict fallback
        content = str(entry.get("content") or entry.get("summary") or entry.get("title") or "")
        text = content
        raw_imp = entry.get("importance")
        if raw_imp is not None:
            try:
                importance = int(raw_imp)
            except (ValueError, TypeError):
                importance = 3

    importance = max(1, min(5, importance))
    return text, tags, importance, ts_str or ""


class MemorySearchEngine:
    """
    BM25/TF-IDF query engine for multi-level memory.
    Features document token/stats caching and posting list indexing for < 5ms query performance on 1,000+ entries.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._doc_cache: Dict[Any, Dict[str, Any]] = {}
        self._last_corpus_sig: Any = None
        self._cached_corpus_data: Optional[Dict[str, Any]] = None

    def clear_cache(self) -> None:
        """Clears both document and corpus caches."""
        self._doc_cache.clear()
        self._last_corpus_sig = None
        self._cached_corpus_data = None

    def _get_doc_key(self, level: Optional[str], entry: Dict[str, Any]) -> Any:
        eid = entry.get("id")
        if eid is not None:
            return (
                level,
                str(eid),
                entry.get("updated_at"),
                entry.get("timestamp"),
                bool(entry.get("deleted", False)),
                entry.get("importance"),
                entry.get("risk_level"),
            )
        return (
            level,
            id(entry),
            entry.get("content"),
            entry.get("summary"),
            entry.get("project_name"),
            entry.get("updated_at"),
            entry.get("timestamp"),
            bool(entry.get("deleted", False)),
            entry.get("importance"),
        )

    def _get_doc_data(self, level: Optional[str], entry: Dict[str, Any]) -> Dict[str, Any]:
        key = self._get_doc_key(level, entry)
        cached = self._doc_cache.get(key)
        if cached is None:
            text, tags, importance, ts_str = extract_document_text(entry, level)
            tokens = tokenize(text, remove_stopwords=True, stem=True)
            tag_tokens = tokenize(" ".join([str(t) for t in tags if t is not None]), remove_stopwords=False, stem=True)
            doc_len = len(tokens)
            cached = {
                "level": level or "default",
                "entry": entry,
                "text": text,
                "text_lower": text.lower(),
                "tokens": tokens,
                "term_freq": Counter(tokens),
                "tag_tokens": tag_tokens,
                "tag_freq": Counter(tag_tokens),
                "doc_len": doc_len,
                "unique_tokens": set(tokens),
                "importance": importance,
                "ts_str": ts_str,
                "deleted": bool(entry.get("deleted", False)),
            }
            if len(self._doc_cache) > 20000:
                self._doc_cache.clear()
            self._doc_cache[key] = cached
        return cached

    def score_corpus(
        self,
        query: str,
        entries: List[Tuple[str, Dict[str, Any]]],  # List of (level, entry_dict)
    ) -> List[Dict[str, Any]]:
        """
        Scores entries against the query using combined BM25 similarity, importance, and recency decay.
        Returns list of result dicts sorted by score descending.
        """
        if not entries:
            return []

        clean_query = query.strip()
        query_tokens = tokenize(clean_query, remove_stopwords=True, stem=True)
        query_lower = clean_query.lower()

        first_k = self._get_doc_key(str(entries[0][0]) if entries[0][0] else "default", entries[0][1])
        mid_k = self._get_doc_key(str(entries[len(entries) // 2][0]) if entries[len(entries) // 2][0] else "default", entries[len(entries) // 2][1])
        last_k = self._get_doc_key(str(entries[-1][0]) if entries[-1][0] else "default", entries[-1][1])
        corpus_sig = (id(entries), len(entries), first_k, mid_k, last_k)

        if self._last_corpus_sig == corpus_sig and self._cached_corpus_data is not None:
            doc_data = self._cached_corpus_data["doc_data"]
            avgdl = self._cached_corpus_data["avgdl"]
            df_counter = self._cached_corpus_data["df_counter"]
            n_docs = self._cached_corpus_data["n_docs"]
            postings = self._cached_corpus_data["postings"]
        else:
            doc_data = []
            total_len = 0
            df_counter: Counter = Counter()
            postings: Dict[str, List[int]] = {}

            for raw_level, entry in entries:
                if entry.get("deleted", False):
                    continue
                lvl = str(raw_level).lower() if raw_level else "default"
                item = self._get_doc_data(lvl, entry)
                idx = len(doc_data)
                doc_data.append(item)
                doc_len = item["doc_len"]
                total_len += doc_len
                for ut in item["unique_tokens"]:
                    df_counter[ut] += 1
                    if ut not in postings:
                        postings[ut] = []
                    postings[ut].append(idx)

            if not doc_data:
                return []

            n_docs = len(doc_data)
            avgdl = (total_len / n_docs) if n_docs > 0 else 1.0
            if avgdl == 0:
                avgdl = 1.0

            self._cached_corpus_data = {
                "doc_data": doc_data,
                "avgdl": avgdl,
                "df_counter": df_counter,
                "n_docs": n_docs,
                "postings": postings,
            }
            self._last_corpus_sig = corpus_sig

        if not doc_data:
            return []

        # Candidate selection
        if clean_query:
            candidate_indices: Set[int] = set()
            if query_tokens:
                for qt in query_tokens:
                    pl = postings.get(qt)
                    if pl:
                        candidate_indices.update(pl)

            if query_lower:
                for i, item in enumerate(doc_data):
                    if i not in candidate_indices:
                        raw_tags = item["entry"].get("tags")
                        tags_iterable = raw_tags if isinstance(raw_tags, (list, tuple, set)) else []
                        if query_lower in item["text_lower"] or any(
                            tag is not None and query_lower == str(tag).lower()
                            for tag in tags_iterable
                        ):
                            candidate_indices.add(i)

            candidate_items = [doc_data[i] for i in candidate_indices]
        else:
            candidate_items = doc_data

        if clean_query and not candidate_items:
            return []

        # Compute IDF for query terms
        idf_map: Dict[str, float] = {}
        for qt in query_tokens:
            df = df_counter.get(qt, 0)
            idf_map[qt] = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)) + 1.0

        max_raw_bm25 = 0.0
        scored_candidates: List[Tuple[Dict[str, Any], float]] = []

        for item in candidate_items:
            term_freq = item["term_freq"]
            tag_freq = item["tag_freq"]
            raw_bm25 = 0.0
            matched_terms = 0

            for qt in query_tokens:
                tf = term_freq.get(qt, 0)
                if tag_freq.get(qt, 0) > 0:
                    tf += 2

                if tf > 0:
                    matched_terms += 1
                    idf = idf_map.get(qt, 1.0)
                    numerator = tf * (self.k1 + 1.0)
                    denominator = tf + self.k1 * (1.0 - self.b + self.b * (item["doc_len"] / avgdl))
                    raw_bm25 += idf * (numerator / denominator)

            # Exact substring match bonus
            if query_lower and query_lower in item["text_lower"]:
                raw_bm25 += 2.0
                matched_terms += 1

            # Tag exact match bonus
            raw_entry_tags = item["entry"].get("tags") or []
            if isinstance(raw_entry_tags, (list, tuple, set)):
                for tag in raw_entry_tags:
                    if tag is not None and query_lower == str(tag).lower():
                        raw_bm25 += 3.0
                        matched_terms += 1

            if clean_query and (matched_terms == 0 or raw_bm25 <= 0.0):
                continue

            if raw_bm25 > max_raw_bm25:
                max_raw_bm25 = raw_bm25

            scored_candidates.append((item, raw_bm25))

        if not scored_candidates:
            return []

        results: List[Dict[str, Any]] = []
        for item, raw_bm25 in scored_candidates:
            if clean_query:
                norm_sim = (raw_bm25 / max_raw_bm25) if max_raw_bm25 > 0 else 0.0
            else:
                norm_sim = 0.0

            norm_imp = item["importance"] / 5.0
            rec_score = compute_recency_score(item["ts_str"], item["level"])

            if clean_query:
                final_score = (W_SIM * norm_sim) + (W_IMP * norm_imp) + (W_REC * rec_score)
            else:
                final_score = (0.60 * norm_imp) + (0.40 * rec_score)

            results.append({
                "score": round(final_score, 4),
                "sim_score": round(norm_sim, 4),
                "imp_score": round(norm_imp, 4),
                "rec_score": round(rec_score, 4),
                "level": item["level"],
                "entry": item["entry"],
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results
