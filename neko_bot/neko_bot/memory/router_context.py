"""
neko_bot.memory.router_context - Prompt Augmentation & Active Memory Context Formatting.

Formats retrieved memories (Semantic Facts, Active Projects, Past Episodes)
for prompt injection under the standardized block:
    [CONTESTO MEMORIA ATTIVA]
    - Regola Cliente: ...
    - Progetto: ...
"""

from typing import Any, Dict, List, Optional, Tuple, Union


CATEGORY_LABELS = {
    "preference": "Preferenza Utente",
    "client_rule": "Regola Cliente",
    "work_pattern": "Abitudine di Lavoro",
    "tech_spec": "Specifica Tecnica",
    "fact": "Fatto Utente",
}


def format_memory_entry(entry: Dict[str, Any], level: Optional[str] = None) -> str:
    """Formats a single memory dictionary into a concise contextual string."""
    detected_level = level or entry.get("level")

    # Layer 1: Semantic Fact
    if detected_level in ("semantic", "fact") or "content" in entry:
        cat = str(entry.get("category", "fact")).lower()
        label = CATEGORY_LABELS.get(cat, "Memoria")
        content = entry.get("content", "").strip()
        tags = entry.get("tags", [])
        tag_str = f" [#{', #'.join(tags)}]" if tags else ""
        return f"{label}: {content}{tag_str}"

    # Layer 2: Project Deadline
    if detected_level in ("project", "project_deadline") or "project_name" in entry:
        pname = entry.get("project_name", "Progetto")
        milestone = entry.get("milestone", "")
        due = entry.get("deadline_utc") or entry.get("due")
        risk = entry.get("risk_level") or entry.get("risk", "medium")
        notes = entry.get("notes", "")

        parts = [f"Progetto {pname}"]
        if milestone:
            parts.append(f"Milestone: {milestone}")
        if due:
            parts.append(f"Scadenza: {due}")
        if risk and risk != "medium":
            parts.append(f"Rischio: {risk}")
        if notes:
            parts.append(f"Note: {notes}")
        return " | ".join(parts)

    # Layer 3: Episodic History
    if detected_level in ("episodic", "episode") or "session_id" in entry:
        summary = entry.get("summary") or entry.get("full_text", "")
        event_type = entry.get("event_type", "interazione")
        outcome = entry.get("outcome", "neutral")
        outcome_str = f" ({outcome})" if outcome != "neutral" else ""
        return f"Episodio ({event_type}): {summary}{outcome_str}"

    # Generic
    content = str(entry.get("content") or entry.get("summary") or entry.get("title") or entry)
    return f"Nota: {content}"


def format_memory_context(
    memories: List[Union[Dict[str, Any], Any]],
    max_chars: int = 2000,
) -> str:
    """
    Formats a list of retrieved memory search results or entities into
    the standardized [CONTESTO MEMORIA ATTIVA] block.
    Returns empty string if memories list is empty.
    """
    if not memories:
        return ""

    lines: List[str] = ["[CONTESTO MEMORIA ATTIVA]"]
    current_chars = len(lines[0])

    for item in memories:
        # Handle search result wrapper {"score": ..., "level": ..., "entry": {...}}
        if isinstance(item, dict) and "entry" in item and isinstance(item["entry"], dict):
            entry_dict = item["entry"]
            lvl = item.get("level")
        elif isinstance(item, dict):
            entry_dict = item
            lvl = item.get("level")
        elif hasattr(item, "to_dict"):
            entry_dict = item.to_dict()
            lvl = getattr(item, "level", None)
        else:
            continue

        if entry_dict.get("deleted", False):
            continue

        formatted_line = f"- {format_memory_entry(entry_dict, level=lvl)}"
        if current_chars + len(formatted_line) + 1 > max_chars:
            break

        lines.append(formatted_line)
        current_chars += len(formatted_line) + 1

    if len(lines) <= 1:
        return ""

    return "\n".join(lines)


def get_active_memory_context(
    store: Any,
    prompt: str,
    top_k: int = 3,
    max_chars: int = 2000,
) -> Optional[str]:
    """
    Queries store with prompt, formats retrieved memories, and returns
    [CONTESTO MEMORIA ATTIVA] string, or None if no relevant memories found.
    """
    if not store or not prompt or not str(prompt).strip():
        return None

    try:
        results = store.search(prompt, level="all", limit=top_k)
        if not results:
            return None
        ctx = format_memory_context(results, max_chars=max_chars)
        return ctx if ctx else None
    except Exception:
        return None


def build_memory_augmented_prompt(
    prompt: str,
    store: Any,
    top_k: int = 3,
) -> Tuple[str, Optional[str]]:
    """
    Augments prompt with retrieved memory context.
    Returns: (augmented_prompt, context_block_or_None)
    """
    context = get_active_memory_context(store, prompt, top_k=top_k)
    if not context:
        return prompt, None
    augmented = f"{context}\n\n{prompt}"
    return augmented, context
