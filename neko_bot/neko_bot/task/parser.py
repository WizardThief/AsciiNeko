"""
neko_bot.task.parser - Italian Natural-Language Task Parser with Dual-Engine Architecture.

Features:
- Deterministic extraction:
  - Project tags: #project, #unreal, #blender, #client-a
  - Priorities: !alta / !urgente -> high; !media / !normale -> medium; !bassa -> low
  - Deadlines: relative Italian phrases (domani, dopodomani, stasera, venerdì, tra 2 ore,
    alle 18:30, entro il 25/10) and standard ISO dates (2026-10-30 18:00).
  - Title extraction: cleanly strips tags, priority tokens, and temporal expressions.
- Ultra-low latency: < 5ms deterministic regex primary engine (< 0.2ms actual).
- Dual-engine fallback: Ollama extraction fallback when text is conversational/complex,
  automatically bypassed under creative process load.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from neko_bot.task.models import TaskPriority

logger = logging.getLogger("neko_bot.task.parser")


@dataclass
class ParsedTask:
    """Structured extraction result from natural language task input."""
    title: str
    project: str = "default"
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: Optional[str] = None  # Formatted as "YYYY-MM-DD HH:MM" or "YYYY-MM-DD HH:MM:SS"
    tags: List[str] = field(default_factory=list)
    raw_prompt: str = ""
    parsed_by: str = "deterministic_engine"
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "project": self.project,
            "priority": self.priority.value if hasattr(self.priority, "value") else str(self.priority),
            "due_date": self.due_date,
            "tags": list(self.tags),
            "raw_prompt": self.raw_prompt,
            "parsed_by": self.parsed_by,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }


# --- Pre-compiled Regular Expressions ---

TAG_PATTERN = re.compile(
    r'(?:^|\s)#([a-zA-Z0-9_\-]+)(?=[.,;:!?]?(?:\s|$))',
    re.UNICODE
)

PRIORITY_PATTERN = re.compile(
    r'(?:^|\s)!([a-zA-Z]+)!?(?=[.,;:!?]?(?:\s|$))',
    re.IGNORECASE | re.UNICODE
)

PRIORITY_EXPLICIT_PATTERN = re.compile(
    r'\b(?:priorit[àa]|priority)\s*[:=]?\s*(alta|urgente|high|urgent|media|medio|normale|medium|normal|bassa|basso|low)\b',
    re.IGNORECASE
)

ISO_DATE_PATTERN = re.compile(
    r'\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})'
    r'(?:[T\s](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?\b'
)

REL_DAY_PATTERN = re.compile(
    r'\b(?:(?:entro|per)\s+(?:il\s+|la\s+)?)?'
    r'(?P<day>oggi|today|stasera|tonight|domani|tomorrow|dopodomani|dopo\s+domani)'
    r'(?:\s+(?:alle|per\s+le|entro\s+le|at)\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?)?\b',
    re.IGNORECASE
)

WEEKDAY_PATTERN = re.compile(
    r'\b(?:(?:entro|per)\s+(?:il\s+)?)?(?:prossimo\s+|prossima\s+|next\s+)?'
    r'(?P<weekday>luned[ìi]|marted[ìi]|mercoled[ìi]|gioved[ìi]|venerd[ìi]|sabato|domenica|'
    r'monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
    r'(?:\s+(?:alle|per\s+le|entro\s+le|at)\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?)?\b',
    re.IGNORECASE
)

INTERVAL_PATTERN = re.compile(
    r'\b(?P<prep>tra|in|entro)\s+'
    r'(?P<amount>\d+|mezz[a\']?|un[ao\']?|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)\s*'
    r'(?P<unit>ore|ora|h|minuti|minuto|min|m|giorni|giorno|gg|g)\b',
    re.IGNORECASE
)

CALENDAR_DATE_PATTERN = re.compile(
    r'\b(?:(?:entro|per)\s+(?:il\s+)?)?'
    r'(?P<day>\d{1,2})(?:[\/\-](?P<month>\d{1,2})(?:[\/\-](?P<year>\d{2,4}))?|'
    r'\s+(?P<month_name>gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|'
    r'january|february|march|april|may|june|july|august|september|october|november|december|'
    r'gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))'
    r'(?:\s+(?:alle|per\s+le|entro\s+le|at)\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?)?\b',
    re.IGNORECASE
)

STANDALONE_TIME_PATTERN = re.compile(
    r'\b(?:(?:entro|per)\s+le|alle|at)\s+(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{2}))?\b',
    re.IGNORECASE
)

WEEKDAY_MAP = {
    "lunedi": 0, "lunedì": 0, "monday": 0,
    "martedi": 1, "martedì": 1, "tuesday": 1,
    "mercoledi": 2, "mercoledì": 2, "wednesday": 2,
    "giovedi": 3, "giovedì": 3, "thursday": 3,
    "venerdi": 4, "venerdì": 4, "friday": 4,
    "sabato": 5, "saturday": 5,
    "domenica": 6, "sunday": 6,
}

MONTH_MAP = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
    "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
    "jan": 1, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "dec": 12,
}

WORD_TO_NUM = {
    "un": 1, "uno": 1, "una": 1, "un'": 1,
    "mezz": 0.5, "mezza": 0.5, "mezz'": 0.5,
    "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
}


def clean_title(raw: str, removal_spans: List[Tuple[int, int]]) -> str:
    """Removes identified metadata spans, command prefixes, and dangling prepositions."""
    if not removal_spans:
        text = raw
    else:
        # Merge overlapping spans
        sorted_spans = sorted(removal_spans, key=lambda s: s[0])
        merged: List[Tuple[int, int]] = []
        for start, end in sorted_spans:
            if not merged:
                merged.append((start, end))
            else:
                prev_start, prev_end = merged[-1]
                if start <= prev_end:
                    merged[-1] = (prev_start, max(prev_end, end))
                else:
                    merged.append((start, end))

        text = raw
        for start, end in reversed(merged):
            text = text[:start] + " " + text[end:]

    # Remove command prefix keywords: only match if followed by colon/dash or explicit command phrasing
    text = re.sub(
        r'^(?:neko\s+)?(?:task\s+add|do)\s+',
        '',
        text.strip(),
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'^(?:aggiungi(?:\s+task)?\s*[:\-]|crea(?:\s+task)?\s*[:\-]|nuovo\s+task\s*[:\-]|'
        r'ricordami(?:\s+di\s+|\s*[:\-])|ricorda(?:\s+di\s+|\s*[:\-])|'
        r'da\s+fare\s*[:\-]|fare\s*[:\-]|todo\s*[:\-]|task\s*[:\-]|promemoria\s*[:\-])\s*',
        '',
        text.strip(),
        flags=re.IGNORECASE,
    )

    # Remove dangling prepositions left over from date extraction at the end
    text = re.sub(
        r'(?:^|\s+)(?:entro\s+il|entro\s+la|entro|per\s+il|per\s+la|per\s+le|per|alle|al|a)\s*$',
        '',
        text.strip(),
        flags=re.IGNORECASE,
    )

    # Remove dangling prepositions at the beginning
    text = re.sub(
        r'^(?:entro\s+il|entro\s+la|entro|per\s+il|per\s+la|per\s+le|per|alle|al|a)\s+',
        '',
        text.strip(),
        flags=re.IGNORECASE,
    )

    # Clean multiple spaces and trim punctuation
    text = re.sub(r'\s+', ' ', text).strip(" ,;:-")

    # Capitalize first character preserving remaining casing
    if text:
        text = text[0].upper() + text[1:]
    return text


def parse_deterministic(text: str, now: Optional[datetime] = None) -> ParsedTask:
    """
    Deterministic zero-VRAM parser for Italian task text.
    Executes in < 0.2ms. Returns ParsedTask.
    """
    if now is None:
        now = datetime.now()

    raw = text.strip()
    removal_spans: List[Tuple[int, int]] = []

    # 1. Project hashtags (#project)
    tags: List[str] = []
    for match in TAG_PATTERN.finditer(raw):
        tags.append(match.group(1))
        removal_spans.append(match.span())
    project = tags[0] if tags else "default"

    # 2. Priority (!alta, !urgente, !media, !bassa)
    priority = TaskPriority.MEDIUM
    prio_match = PRIORITY_PATTERN.search(raw)
    if prio_match:
        p_val = prio_match.group(1).lower()
        priority = TaskPriority.from_str(p_val)
        removal_spans.append(prio_match.span())
    else:
        exp_match = PRIORITY_EXPLICIT_PATTERN.search(raw)
        if exp_match:
            p_val = exp_match.group(1).lower()
            priority = TaskPriority.from_str(p_val)
            removal_spans.append(exp_match.span())

    # 3. Deadlines and temporal expressions
    due_dt: Optional[datetime] = None
    has_seconds = False

    # 3a. ISO format: YYYY-MM-DD [HH:MM[:SS]]
    iso_match = ISO_DATE_PATTERN.search(raw)
    if iso_match:
        y = int(iso_match.group("year"))
        m = int(iso_match.group("month"))
        d = int(iso_match.group("day"))
        h = int(iso_match.group("hour")) if iso_match.group("hour") else 18
        mi = int(iso_match.group("minute")) if iso_match.group("minute") else 0
        s = int(iso_match.group("second")) if iso_match.group("second") else 0
        if iso_match.group("second"):
            has_seconds = True
        try:
            due_dt = datetime(y, m, d, h, mi, s)
            removal_spans.append(iso_match.span())
        except ValueError:
            pass

    # 3b. Explicit calendar dates (entro il 25/10, 30 ottobre)
    if due_dt is None:
        cal_match = CALENDAR_DATE_PATTERN.search(raw)
        if cal_match:
            d = int(cal_match.group("day"))
            if cal_match.group("month"):
                m = int(cal_match.group("month"))
            else:
                m_name = cal_match.group("month_name").lower()
                m = MONTH_MAP.get(m_name, now.month)
            y = int(cal_match.group("year")) if cal_match.group("year") else now.year
            if y < 100:
                y += 2000
            if cal_match.group("year") is None:
                # If date has already passed by > 1 day in current year, roll over
                try:
                    cand = date(y, m, d)
                    if cand < (now.date() - timedelta(days=1)):
                        y += 1
                except ValueError:
                    pass
            h = int(cal_match.group("hour")) if cal_match.group("hour") else 18
            mi = int(cal_match.group("minute")) if cal_match.group("minute") else 0
            try:
                due_dt = datetime(y, m, d, h, mi, 0)
                removal_spans.append(cal_match.span())
            except ValueError:
                pass

    # 3c. Relative days (oggi, stasera, domani, dopodomani)
    if due_dt is None:
        rel_match = REL_DAY_PATTERN.search(raw)
        if rel_match:
            d_word = rel_match.group("day").lower().replace(" ", "")
            h_str = rel_match.group("hour")
            mi_str = rel_match.group("minute")

            if d_word in ("oggi", "today"):
                base_d = now.date()
                h = int(h_str) if h_str else 23
                mi = int(mi_str) if mi_str else (59 if not h_str else 0)
            elif d_word in ("stasera", "tonight"):
                base_d = now.date()
                h = int(h_str) if h_str else 20
                mi = int(mi_str) if mi_str else 0
            elif d_word in ("domani", "tomorrow"):
                base_d = now.date() + timedelta(days=1)
                h = int(h_str) if h_str else 18
                mi = int(mi_str) if mi_str else 0
            else:  # dopodomani
                base_d = now.date() + timedelta(days=2)
                h = int(h_str) if h_str else 18
                mi = int(mi_str) if mi_str else 0

            due_dt = datetime.combine(base_d, time(h, mi, 0))
            removal_spans.append(rel_match.span())

    # 3d. Weekdays (lunedì .. domenica)
    if due_dt is None:
        wk_match = WEEKDAY_PATTERN.search(raw)
        if wk_match:
            w_word = wk_match.group("weekday").lower()
            target_wd = WEEKDAY_MAP.get(w_word, 4)
            curr_wd = now.weekday()
            days_ahead = (target_wd - curr_wd) % 7
            h_str = wk_match.group("hour")
            mi_str = wk_match.group("minute")
            h = int(h_str) if h_str else 18
            mi = int(mi_str) if mi_str else 0
            target_time = time(h, mi, 0)

            if days_ahead == 0:
                if now.time() < target_time and h_str:
                    days_ahead = 0
                else:
                    days_ahead = 7

            target_date = now.date() + timedelta(days=days_ahead)
            due_dt = datetime.combine(target_date, target_time)
            removal_spans.append(wk_match.span())

    # 3e. Relative intervals (tra 2 ore, in 30 minuti)
    if due_dt is None:
        int_match = INTERVAL_PATTERN.search(raw)
        if int_match:
            amt_raw = int_match.group("amount").lower()
            if amt_raw.isdigit():
                amt = float(amt_raw)
            else:
                amt = float(WORD_TO_NUM.get(amt_raw, 1.0))
            unit = int_match.group("unit").lower()

            if unit in ("ore", "ora", "h"):
                due_dt = now + timedelta(hours=amt)
            elif unit in ("minuti", "minuto", "min", "m"):
                due_dt = now + timedelta(minutes=amt)
            elif unit in ("giorni", "giorno", "gg", "g"):
                due_dt = now + timedelta(days=amt)

            if due_dt:
                removal_spans.append(int_match.span())

    # 3f. Standalone time (alle 18:30)
    if due_dt is None:
        st_match = STANDALONE_TIME_PATTERN.search(raw)
        if st_match:
            h = int(st_match.group("hour"))
            mi = int(st_match.group("minute")) if st_match.group("minute") else 0
            target_t = time(h, mi, 0)
            target_d = now.date() if target_t > now.time() else now.date() + timedelta(days=1)
            due_dt = datetime.combine(target_d, target_t)
            removal_spans.append(st_match.span())

    if due_dt:
        if has_seconds:
            due_str = due_dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            due_str = due_dt.strftime("%Y-%m-%d %H:%M")
    else:
        due_str = None

    # 4. Clean Title Extraction
    title = clean_title(raw, removal_spans)
    if not title:
        title = f"Task #{project}" if project != "default" else raw

    return ParsedTask(
        title=title,
        project=project,
        priority=priority,
        due_date=due_str,
        tags=tags,
        raw_prompt=raw,
        parsed_by="deterministic_engine",
        confidence=1.0 if len(title) > 3 else 0.5,
    )


def parse_task_input(
    text: str,
    config: Optional[Any] = None,
    now: Optional[datetime] = None,
    force_llm: bool = False,
) -> ParsedTask:
    """
    Dual-engine parser entry point:
    1. Runs fast deterministic parse (< 0.2ms).
    2. If forced or low confidence, attempts Ollama extraction via router
       (bypassed under creative process load).
    """
    det_result = parse_deterministic(text, now=now)

    needs_llm = force_llm or (det_result.confidence < 0.6 and len(text) > 15)
    if not needs_llm:
        return det_result

    try:
        from neko_bot.core.router import get_default_router
        router = get_default_router(config)
        mode = router.resource_monitor.get_resource_mode()
        if mode == "creative_detected" and not force_llm:
            return det_result

        sys_prompt = (
            "You are a structured task parser. Extract task information from Italian/English text. "
            "Output JSON with exact keys: title (str), project (str or 'default'), "
            "priority ('high'|'medium'|'low'), due_date (ISO YYYY-MM-DD HH:MM or null)."
        )
        res = router.route_request(
            prompt=text,
            intent="task_parse",
            system_prompt=sys_prompt,
            fallback_func=lambda p: det_result.to_dict(),
        )
        data = res.get("data")
        if isinstance(data, dict) and "title" in data:
            return ParsedTask(
                title=str(data.get("title", det_result.title)).strip(),
                project=str(data.get("project", det_result.project)).strip(),
                priority=TaskPriority.from_str(data.get("priority", det_result.priority)),
                due_date=data.get("due_date", det_result.due_date),
                tags=det_result.tags,
                raw_prompt=text,
                parsed_by="ollama" if res.get("source") == "ollama" else "deterministic_engine",
                confidence=0.95 if res.get("source") == "ollama" else det_result.confidence,
            )
    except Exception as e:
        logger.debug("LLM fallback failed: %s", e)

    return det_result


class ItalianTaskParser:
    """Class wrapper for Italian task parser matching test suite conventions."""

    def __init__(self, reference_time: Optional[datetime] = None, config: Optional[Any] = None) -> None:
        self.reference_time = reference_time
        self.config = config

    def parse(self, text: str, force_llm: bool = False) -> ParsedTask:
        return parse_task_input(text, config=self.config, now=self.reference_time, force_llm=force_llm)


def parse_italian_task(text: str, now: Optional[datetime] = None) -> ParsedTask:
    """Convenience function matching test suite invocation."""
    return parse_task_input(text, now=now)
