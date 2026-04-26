"""PersonaMem adapter for MemKraft.

Optimized conversion of PersonaMem conversation transcripts into
MemKraft's entity/preference/bitemporal structures. Preserves the
MemKraft philosophy: stdlib only, Markdown storage, no LLM calls.

Key improvements over v2 harness.py:
  1. Extract from EVERY user message (not just regex patterns)
  2. Session-aware: track which session/block each fact came from
  3. Reason tracking: capture "because/since/after" clauses
  4. Temporal ordering: use years AND conversation order
  5. Preference evolution: "I started X then switched to Y"
  6. Per-question-type context builders
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# ────────────────────────────────────────────────────────────
# Regex patterns (compiled once)
# ────────────────────────────────────────────────────────────

# Year markers used heavily by PersonaMem personas
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# Temporal/sequence markers
_SEQUENCE_MARKERS = [
    "initially", "at first", "originally", "back then",
    "later", "then", "after that", "subsequently", "eventually",
    "recently", "now", "currently", "these days", "lately",
    "used to", "i started", "i began", "i switched",
    "i moved to", "i transitioned", "i shifted", "i changed",
]

# Preference verbs (strong)
_LIKE_VERBS = r"(?:love|loves|loved|enjoy|enjoys|enjoyed|adore|adores|prefer|prefers|preferred|like|likes|liked|am (?:a )?fan of|am into|appreciate|appreciates|appreciated|cherish|cherishes|cherished)"
_DISLIKE_VERBS = r"(?:hate|hates|hated|dislike|dislikes|disliked|avoid|avoids|avoided|can't stand|couldn't stand|no longer enjoy|stopped enjoying|don't like|didn't like|am not a fan of|not into)"

# Activity/transition verbs
_START_VERBS = r"(?:started|began|took up|picked up|got into|embraced|adopted|launched|created|founded|organized|curated|explored|discovered|learned|practiced|pursued|developed|built)"
_STOP_VERBS = r"(?:stopped|quit|gave up|abandoned|dropped|ended|left|walked away from|moved on from)"
_SWITCH_VERBS = r"(?:switched to|moved to|transitioned to|shifted to|changed to|turned to|pivoted to)"

# Reason markers
_REASON_MARKERS = [
    "because", "since", "as ", "due to", "after ",
    "feeling", "felt", "found that", "realized",
    "which made", "that made", "which led", "so i",
    "discouraged", "motivated", "inspired",
]

# Fact statements ("I am X", "I work as Y", "my X is Y")
_FACT_PATTERNS = [
    # "I am/was a ..." / "I work as a ..."
    re.compile(r"\b(?:I['\u2019]?m|I am|I was|I work as|I'm working as|I've been)\s+(?:an?\s+|the\s+)?([a-z][a-z\s\-]{2,60}?)(?=[\.!?,;]|\s+and\s|\s+but\s|$)", re.IGNORECASE),
    # "my X is Y"
    re.compile(r"\bmy\s+([a-z][a-z\s\-]{2,30})\s+(?:is|are|was|were)\s+([^\.!?,;]{2,80})", re.IGNORECASE),
    # "I have X" (possessions / situations)
    re.compile(r"\bI\s+(?:have|had|own|owned)\s+(?:an?\s+|the\s+|some\s+)?([a-z][a-z\s\-]{3,60}?)(?=[\.!?,;]|\s+and\s|\s+but\s|$)", re.IGNORECASE),
]

# Category keyword map (broadly reused across PersonaMem topics)
_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "food": ["food", "cuisine", "restaurant", "cooking", "recipe", "dish", "meal",
              "eat", "ate", "eating", "chef", "bake", "baking", "diet", "flavor",
              "kitchen", "farmers market", "ingredient", "vegetarian", "vegan"],
    "music": ["music", "song", "playlist", "concert", "artist", "genre", "band",
               "album", "melody", "rhythm", "beat", "remix", "dj", "instrument",
               "guitar", "piano", "vocal", "lyric", "podcast", "audio"],
    "travel": ["travel", "trip", "vacation", "hotel", "flight", "destination",
                "adventure", "explore", "tour", "journey", "backpack", "resort"],
    "entertainment": ["movie", "film", "show", "series", "book", "novel", "reading",
                       "game", "gaming", "hobby", "sport", "theater", "exhibit",
                       "video", "youtube", "stream", "streaming"],
    "work": ["work", "career", "job", "professional", "business", "project",
              "client", "colleague", "office", "startup", "company", "employer"],
    "health": ["health", "fitness", "exercise", "wellness", "medical", "yoga",
                "running", "gym", "workout", "meditation", "therapy", "sleep"],
    "education": ["education", "learning", "course", "study", "school",
                   "university", "research", "class", "degree", "tutor"],
    "technology": ["tech", "software", "coding", "programming", "app", "digital",
                    "computer", "ai", "machine learning", "data", "api", "website"],
    "creative": ["art", "design", "photography", "writing", "painting",
                  "drawing", "creative", "sketch", "sculpture", "craft"],
    "relationships": ["friend", "family", "partner", "spouse", "kids", "children",
                       "parents", "dating", "marriage"],
    "finance": ["money", "invest", "stock", "budget", "saving", "finance"],
}


def infer_category(text: str) -> str:
    """Best-guess category from free text."""
    text_lower = text.lower()
    best_cat = "general"
    best_hits = 0
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > best_hits:
            best_hits = hits
            best_cat = cat
    return best_cat


def strip_role_prefix(content: str) -> str:
    """Strip leading 'User:' / 'Assistant:' role prefix."""
    for prefix in ("User: ", "Assistant: ", "user: ", "assistant: "):
        if content.startswith(prefix):
            return content[len(prefix):]
    return content


# ────────────────────────────────────────────────────────────
# Persona extraction (from system message)
# ────────────────────────────────────────────────────────────

def parse_persona_text(text: str) -> Dict[str, Any]:
    """Parse persona description from PersonaMem system message."""
    info: Dict[str, Any] = {}

    m = re.search(r"Name:\s*(.+?)[\r\n]", text)
    if m:
        info["name"] = m.group(1).strip()

    m = re.search(r"Gender Identity:\s*(.+?)[\r\n]", text)
    if m:
        info["gender"] = m.group(1).strip()

    m = re.search(r"(?:aged?\s+|is a\s+)(\d{1,3})[\s\-]+year", text, re.IGNORECASE)
    if m:
        info["age"] = m.group(1)

    m = re.search(r"Racial Identity:\s*(.+?)[\r\n]", text)
    if m:
        info["race"] = m.group(1).strip()

    # Profession — wide net
    prof_keywords = (
        r"software engineer|content creator|teacher|doctor|lawyer|designer|"
        r"writer|analyst|manager|consultant|therapist|photographer|musician|"
        r"artist|chef|researcher|developer|professor|architect|journalist|"
        r"entrepreneur|freelancer|student|retired|nurse|engineer|scientist|"
        r"editor|producer|director|accountant|pharmacist|podcaster"
    )
    m = re.search(rf"\b({prof_keywords})\b", text, re.IGNORECASE)
    if m:
        info["profession"] = m.group(1).strip().lower()

    # Likes from persona description
    likes = re.findall(
        r"(?:loves?|enjoys?|passionate about|keen (?:expertise|interest) in|"
        r"fond of|interested in|excited about|deeply involved in)\s+([^\.,;\n]+)",
        text, re.IGNORECASE
    )
    if likes:
        info["likes"] = [l.strip() for l in likes[:15]]

    dislikes = re.findall(
        r"(?:dislikes?|hates?|avoids?|not a fan of|doesn't like|don't enjoy)\s+([^\.,;\n]+)",
        text, re.IGNORECASE
    )
    if dislikes:
        info["dislikes"] = [d.strip() for d in dislikes[:10]]

    return info


def extract_persona_from_context(context: List[Dict]) -> Dict[str, Any]:
    """Pull the persona info out of the system message in a PersonaMem context."""
    for msg in context:
        if msg.get("role") == "system":
            return parse_persona_text(msg.get("content", ""))
    return {}


# ────────────────────────────────────────────────────────────
# Per-user-message fact extraction
# ────────────────────────────────────────────────────────────

def _split_sentences(text: str) -> List[str]:
    """Cheap sentence tokenizer (no nltk)."""
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Split on sentence-ending punctuation followed by space+capital or end
    parts = re.split(r"(?<=[\.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _detect_year(sentence: str, fallback_year: Optional[str] = None) -> Optional[str]:
    m = _YEAR_RE.search(sentence)
    if m:
        return m.group(1)
    return fallback_year


def _extract_reason(sentence: str) -> Optional[str]:
    """Find a reason clause in the sentence if present."""
    lower = sentence.lower()
    for marker in _REASON_MARKERS:
        idx = lower.find(marker)
        if idx == -1:
            continue
        # Take from the marker to end of sentence (capped)
        tail = sentence[idx:idx + 220].strip()
        tail = re.sub(r"\s+", " ", tail)
        if len(tail) > 12:
            return tail
    return None


def _classify_sentiment(sentence: str) -> str:
    """Return 'positive', 'negative', or 'neutral' for the sentence."""
    lower = sentence.lower()
    neg_cues = [
        "stopped", "quit", "gave up", "hate", "hated", "dislike", "disliked",
        "avoid", "avoided", "can't stand", "discourag", "stifl",
        "no longer", "not a fan", "didn't like", "don't like",
        "chaotic", "unproductive", "disappoint", "frustrat", "boring",
        "tired of", "fed up",
    ]
    pos_cues = [
        "love", "loved", "enjoy", "enjoyed", "excited", "thrilled",
        "passion", "inspired", "fulfill", "rewarding", "amazing",
        "wonderful", "great", "fantastic", "started", "began",
        "curated", "created", "organized", "launched", "explored",
        "learn", "discovered", "embrac",
    ]
    neg = sum(1 for c in neg_cues if c in lower)
    pos = sum(1 for c in pos_cues if c in lower)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def extract_statements_from_message(
    message: str,
    session_num: int,
    message_idx: int,
) -> List[Dict[str, Any]]:
    """Extract structured statements from a single user message.

    Returns a list of dicts, each with:
        - text: the raw sentence
        - kind: 'start' | 'stop' | 'switch' | 'like' | 'dislike' | 'fact' | 'general'
        - subject: topic phrase (best-effort)
        - value: the activity/object
        - year: year if mentioned
        - reason: reason clause if present
        - sentiment: positive / negative / neutral
        - category: inferred category
        - session: session number
        - msg_idx: message index in conversation
    """
    statements: List[Dict[str, Any]] = []
    message = strip_role_prefix(message)
    if not message or not message.strip():
        return statements

    sentences = _split_sentences(message)
    last_year = None

    for s_idx, sentence in enumerate(sentences):
        year = _detect_year(sentence, last_year)
        if year:
            last_year = year

        sentiment = _classify_sentiment(sentence)
        reason = _extract_reason(sentence)
        category = infer_category(sentence)
        s_lower = sentence.lower()

        # Try patterned matches in priority order
        found_kind: Optional[str] = None
        value: Optional[str] = None
        subject: Optional[str] = None

        # "I started X" / "I began X" / "I curated X"
        m = re.search(rf"\bI\s+{_START_VERBS}\s+(.+?)(?=\s+(?:in|around|back in)\s+\d{{4}}|[\.!?,;]|$)",
                      sentence, re.IGNORECASE)
        if m and not found_kind:
            value = m.group(1).strip()
            if 3 < len(value) < 180:
                found_kind = "start"

        # "I then switched to X"
        m = re.search(rf"\bI\s+(?:then\s+|also\s+|now\s+|later\s+)?{_SWITCH_VERBS}\s+(.+?)(?=[\.!?,;]|$)",
                      sentence, re.IGNORECASE)
        if m and not found_kind:
            value = m.group(1).strip()
            if 3 < len(value) < 180:
                found_kind = "switch"

        # "I stopped/quit X"
        m = re.search(rf"\bI\s+(?:then\s+|later\s+)?{_STOP_VERBS}\s+(.+?)(?=[\.!?,;]|$)",
                      sentence, re.IGNORECASE)
        if m and not found_kind:
            value = m.group(1).strip()
            if 3 < len(value) < 180:
                found_kind = "stop"

        # "I love/enjoy/prefer X"
        m = re.search(rf"\bI\s+(?:really\s+|truly\s+|still\s+|always\s+)?{_LIKE_VERBS}\s+(.+?)(?=[\.!?,;]|$)",
                      sentence, re.IGNORECASE)
        if m and not found_kind:
            value = m.group(1).strip()
            if 3 < len(value) < 180:
                found_kind = "like"

        # "I hate/dislike X"
        m = re.search(rf"\bI\s+(?:really\s+|truly\s+|no longer\s+)?{_DISLIKE_VERBS}\s+(.+?)(?=[\.!?,;]|$)",
                      sentence, re.IGNORECASE)
        if m and not found_kind:
            value = m.group(1).strip()
            if 3 < len(value) < 180:
                found_kind = "dislike"

        # "my favorite X is Y"
        m = re.search(r"\bmy\s+(?:current\s+|all[- ]time\s+)?favou?rite\s+(.+?)\s+(?:is|are|was|were)\s+(.+?)(?=[\.!?,;]|$)",
                      sentence, re.IGNORECASE)
        if m and not found_kind:
            subject = m.group(1).strip()
            value = m.group(2).strip()
            if value and 3 < len(value) < 180:
                found_kind = "favorite"

        # Fact patterns ("I am X", "my X is Y", "I have X")
        if not found_kind:
            for pat in _FACT_PATTERNS:
                fm = pat.search(sentence)
                if fm:
                    if fm.lastindex == 2:
                        subject = fm.group(1).strip()
                        value = fm.group(2).strip()
                    else:
                        value = fm.group(1).strip()
                    if value and 2 < len(value) < 180:
                        found_kind = "fact"
                        break

        # Strong sentiment without a pattern → general preference hint
        if not found_kind and sentiment != "neutral" and len(sentence) > 20:
            value = sentence.strip()[:180]
            found_kind = "like" if sentiment == "positive" else "dislike"

        if found_kind and value:
            statements.append({
                "text": sentence.strip(),
                "kind": found_kind,
                "subject": subject,
                "value": value,
                "year": year,
                "reason": reason,
                "sentiment": sentiment,
                "category": category,
                "session": session_num,
                "msg_idx": message_idx,
                "sentence_idx": s_idx,
            })
        else:
            # Keep the raw sentence as a general utterance (still useful for recall)
            if len(sentence) > 25 and any(c.isalpha() for c in sentence):
                statements.append({
                    "text": sentence.strip(),
                    "kind": "general",
                    "subject": None,
                    "value": sentence.strip()[:180],
                    "year": year,
                    "reason": reason,
                    "sentiment": sentiment,
                    "category": category,
                    "session": session_num,
                    "msg_idx": message_idx,
                    "sentence_idx": s_idx,
                })

    return statements


# ────────────────────────────────────────────────────────────
# Session splitting
# ────────────────────────────────────────────��───────────────

def detect_sessions(context: List[Dict]) -> List[List[int]]:
    """Group message indices into sessions.

    PersonaMem conversations often implicitly chain multiple sessions.
    Heuristic: every time a user message contains a greeting-style
    opener after at least one assistant reply, start a new session.
    """
    greetings = (
        "hi there", "hello", "hey there", "good morning", "good evening",
        "how's it going", "how are you", "what's up",
    )
    sessions: List[List[int]] = []
    current: List[int] = []
    prev_had_assistant = False

    for i, msg in enumerate(context):
        role = msg.get("role")
        content = (msg.get("content") or "").lower()
        content_stripped = strip_role_prefix(msg.get("content") or "").lower()

        if role == "system":
            continue

        is_greeting = any(content_stripped.startswith(g) for g in greetings)
        if role == "user" and is_greeting and prev_had_assistant and current:
            sessions.append(current)
            current = []
            prev_had_assistant = False

        current.append(i)
        if role == "assistant":
            prev_had_assistant = True

    if current:
        sessions.append(current)

    # Fallback: if no sessions split, treat whole thing as session 1
    if not sessions:
        sessions = [[i for i, m in enumerate(context) if m.get("role") != "system"]]

    return sessions


# ────────────────────────────────────────────────────────────
# Main adapter
# ────────────────────────────────────────────────────────────

class PersonaMemAdapter:
    """Feed a PersonaMem conversation into MemKraft storage."""

    def __init__(self, mk: Any) -> None:
        self.mk = mk

    def ingest(
        self,
        context: List[Dict],
        end_index: int,
        persona_name_fallback: str = "user",
    ) -> Dict[str, Any]:
        """Ingest a PersonaMem conversation up to ``end_index``.

        Returns a dict with statistics + the structured statements
        list (for building rich prompt contexts later).
        """
        persona_info = extract_persona_from_context(context)
        persona_name = persona_info.get("name") or persona_name_fallback
        self.mk.track(persona_name, entity_type="person", source="personamem")

        stats = {
            "facts": 0,
            "preferences": 0,
            "messages": 0,
            "sessions": 0,
            "statements": 0,
        }

        # Inject persona facts
        self._inject_persona_facts(persona_name, persona_info, stats)

        # Split into sessions
        sessions = detect_sessions(context[:end_index])
        stats["sessions"] = len(sessions)

        all_statements: List[Dict[str, Any]] = []

        for sess_num, msg_indices in enumerate(sessions, start=1):
            for m_idx in msg_indices:
                if m_idx >= end_index:
                    continue
                msg = context[m_idx]
                if msg.get("role") != "user":
                    continue

                stats["messages"] += 1
                statements = extract_statements_from_message(
                    msg.get("content", ""), sess_num, m_idx
                )
                all_statements.extend(statements)
                stats["statements"] += len(statements)

                for stmt in statements:
                    self._apply_statement(persona_name, stmt, stats)

        return {
            "persona_name": persona_name,
            "persona_info": persona_info,
            "stats": stats,
            "statements": all_statements,
            "sessions": sessions,
        }

    # ────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────

    def _inject_persona_facts(
        self, name: str, info: Dict[str, Any], stats: Dict[str, int]
    ) -> None:
        # Use dedicated facts (no LLM)
        for key in ("profession", "gender", "age", "race"):
            if key in info:
                self.mk.update(name, f"{key}: {info[key]}", source="personamem-persona")
                stats["facts"] += 1

        for like in info.get("likes", []) or []:
            # Stored as preference with strong strength
            self.mk.pref_set(
                name, "persona_like", like,
                category=infer_category(like),
                strength=0.9,
                source="personamem-persona",
            )
            stats["preferences"] += 1

        for dislike in info.get("dislikes", []) or []:
            self.mk.pref_set(
                name, "persona_dislike", dislike,
                category=infer_category(dislike),
                strength=0.9,
                source="personamem-persona",
            )
            stats["preferences"] += 1

    def _apply_statement(
        self, name: str, stmt: Dict[str, Any], stats: Dict[str, int]
    ) -> None:
        kind = stmt["kind"]
        value = stmt["value"]
        category = stmt["category"]
        year = stmt.get("year")
        reason = stmt.get("reason") or ""
        session = stmt.get("session", 1)
        valid_from = f"{year}-01-01" if year else None

        # Map kind to preference key; facts go to the live note as updates
        if kind in ("start", "switch"):
            key = "activity"
            strength = 0.85
            self.mk.pref_set(
                name, key, value, category=category,
                strength=strength, reason=reason,
                source=f"personamem-session{session}",
                valid_from=valid_from,
            )
            stats["preferences"] += 1
        elif kind == "stop":
            # Record as a dislike / discontinuation
            self.mk.pref_set(
                name, "discontinued", value, category=category,
                strength=0.7, reason=reason,
                source=f"personamem-session{session}",
                valid_from=valid_from,
            )
            stats["preferences"] += 1
        elif kind == "like":
            self.mk.pref_set(
                name, "likes", value, category=category,
                strength=0.8, reason=reason,
                source=f"personamem-session{session}",
                valid_from=valid_from,
            )
            stats["preferences"] += 1
        elif kind == "dislike":
            self.mk.pref_set(
                name, "dislikes", value, category=category,
                strength=0.8, reason=reason,
                source=f"personamem-session{session}",
                valid_from=valid_from,
            )
            stats["preferences"] += 1
        elif kind == "favorite":
            subject = stmt.get("subject") or "item"
            pref_key = f"favorite_{re.sub(r'[^a-z0-9]+', '_', subject.lower()).strip('_')}"[:40] or "favorite"
            self.mk.pref_set(
                name, pref_key, value, category=category,
                strength=0.95, reason=reason,
                source=f"personamem-session{session}",
                valid_from=valid_from,
            )
            stats["preferences"] += 1
        elif kind == "fact":
            info_line = value
            if stmt.get("subject"):
                info_line = f"{stmt['subject']}: {value}"
            self.mk.update(name, info_line, source=f"personamem-session{session}")
            stats["facts"] += 1
        else:
            # "general" — record the raw sentence into live note too (for recall)
            snippet = value[:140]
            self.mk.update(name, f"[s{session}] {snippet}", source=f"personamem-session{session}")
            stats["facts"] += 1


# ────────────────────────────────────────────────────────────
# Context builders — one per PersonaMem question type
# ────────────────────────────────────────────────────────────

QUESTION_TYPES = (
    "recall_user_shared_facts",
    "recalling_facts_mentioned_by_the_user",
    "acknowledge_latest_preferences",
    "track_full_preference_evolution",
    "revisit_reasons_behind_preference_updates",
    "recalling_the_reasons_behind_previous_updates",
    "provide_preference_aligned_recommendations",
    "suggest_new_ideas",
    "generalizing_to_new_scenarios",
)


def _format_pref_line(p: Dict[str, Any]) -> str:
    """Render a single preference dict as a readable bullet line."""
    line = f"- [{p.get('category','general')}] {p['key']}: {p['value']}"
    if p.get("valid_from"):
        line += f" (since {p['valid_from'][:4]}"
        if p.get("valid_to"):
            line += f", until {p['valid_to'][:4]}"
        line += ")"
    line += f" [strength {p.get('strength', 1.0):.1f}]"
    return line


def build_context(
    mk: Any,
    persona_name: str,
    question_type: str,
    topic: str,
    question: str,
    statements: Optional[List[Dict[str, Any]]] = None,
    max_statements: int = 120,
) -> str:
    """Build a per-question-type structured summary.

    This summary is designed to be the most useful compressed view of
    the ingested MemKraft data for the given PersonaMem question type.
    """
    parts: List[str] = []
    statements = statements or []

    # Every prompt starts with a compact persona bio
    brief = ""
    try:
        brief = mk.brief(persona_name, save=False) or ""
    except Exception:
        brief = ""
    if brief:
        # Keep only the first ~1500 chars of the brief (entity info + live note)
        parts.append("## Persona Profile\n" + brief[:1500].strip())

    all_prefs = []
    try:
        all_prefs = mk.pref_get(persona_name) or []
    except Exception:
        pass

    # Sort by valid_from (chronological)
    all_prefs.sort(key=lambda p: (p.get("valid_from") or "0000", p.get("recorded", "")))

    qt = (question_type or "").strip()

    # ── Type: recall facts ─────────────────────────────────────
    if qt in ("recall_user_shared_facts", "recalling_facts_mentioned_by_the_user"):
        # Facts + all raw statements relevant to topic
        topic_keywords = _keywords_from(topic) + _keywords_from(question)
        relevant = _filter_statements_by_keywords(statements, topic_keywords)

        if relevant:
            parts.append("## Relevant Facts Shared by the User")
            for s in relevant[:max_statements]:
                yr = f"({s['year']}) " if s.get('year') else ""
                parts.append(f"- {yr}[s{s.get('session','?')}] {s['text'][:220]}")

        if all_prefs:
            parts.append("## Known Preferences")
            for p in all_prefs[:40]:
                parts.append(_format_pref_line(p))

    # ── Type: latest preferences ───────────────────────────────
    elif qt == "acknowledge_latest_preferences":
        parts.append("## Current (Most Recent) Preferences")
        # Group by key, show latest value per key
        latest_by_key: Dict[str, Dict[str, Any]] = {}
        for p in all_prefs:
            k = p["key"]
            if k not in latest_by_key or (p.get("valid_from") or "") >= (latest_by_key[k].get("valid_from") or ""):
                latest_by_key[k] = p
        for k, p in latest_by_key.items():
            parts.append(_format_pref_line(p))

        # Recent statements (last session)
        if statements:
            last_session = max((s.get("session", 1) for s in statements), default=1)
            recent = [s for s in statements if s.get("session") == last_session]
            if recent:
                parts.append(f"\n## Statements From Latest Session ({last_session})")
                for s in recent[:60]:
                    parts.append(f"- {s['text'][:220]}")

    # ── Type: full preference evolution ────────────────────────
    elif qt == "track_full_preference_evolution":
        topic_keywords = _keywords_from(topic) + _keywords_from(question)
        # Show full evolution grouped by key
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        for p in all_prefs:
            by_key.setdefault(p["key"], []).append(p)

        parts.append("## Preference Evolution Timeline")
        for k, plist in by_key.items():
            plist.sort(key=lambda p: p.get("valid_from") or "0000")
            if len(plist) == 1 and not topic_keywords_match(plist[0], topic_keywords):
                continue
            parts.append(f"\n### {k}")
            for p in plist:
                parts.append(_format_pref_line(p))
                if p.get("valid_from") is None:
                    continue

        # Plus chronological statements (the real signal)
        if statements:
            ordered = sorted(statements, key=lambda s: (s.get("msg_idx", 0)))
            kind_marked = [s for s in ordered
                           if s.get("kind") in ("start", "switch", "stop", "like", "dislike", "favorite")]
            if kind_marked:
                parts.append("\n## Chronological Changes (from conversation)")
                for s in kind_marked[:max_statements]:
                    yr = f"({s['year']}) " if s.get('year') else ""
                    parts.append(f"- {yr}[s{s.get('session','?')}] [{s['kind']}] "
                                 f"{s['text'][:220]}")

    # ── Type: reasons behind preference updates ────────────────
    elif qt in ("revisit_reasons_behind_preference_updates",
                "recalling_the_reasons_behind_previous_updates"):
        parts.append("## Preference Changes & Reasons")
        # Statements that show change + reason
        change_statements = [s for s in statements
                             if s.get("kind") in ("start", "switch", "stop", "dislike")
                             or (s.get("reason") and s.get("sentiment") != "neutral")]

        change_statements.sort(key=lambda s: (s.get("msg_idx", 0)))
        for s in change_statements[:max_statements]:
            yr = f"({s['year']}) " if s.get("year") else ""
            line = f"- {yr}[s{s.get('session','?')}] [{s.get('kind','')}] {s['text'][:220]}"
            if s.get("reason"):
                line += f"\n  - reason: {s['reason'][:200]}"
            parts.append(line)

        # Preference conflicts (before/after value changes) from MemKraft
        try:
            conflicts = mk.pref_conflicts(persona_name) or []
        except Exception:
            conflicts = []
        if conflicts:
            parts.append("\n## Detected Preference Transitions")
            for c in conflicts[:20]:
                chain = " → ".join(
                    f"{v['value']} ({v.get('valid_from','?')[:4]})"
                    for v in c.get("values", [])
                )
                parts.append(f"- {c['key']}: {chain} — current: {c.get('current','?')}")

    # ── Type: preference-aligned recommendations / suggest new ─
    elif qt in ("provide_preference_aligned_recommendations", "suggest_new_ideas"):
        # Give a condensed current preference profile
        parts.append("## Current Preference Profile")
        # group by category
        by_cat: Dict[str, List[Dict[str, Any]]] = {}
        for p in all_prefs:
            by_cat.setdefault(p.get("category", "general"), []).append(p)
        for cat, plist in by_cat.items():
            parts.append(f"\n### {cat}")
            for p in plist[:10]:
                parts.append(_format_pref_line(p))

        # Plus strong positive/negative signals
        pos = [s for s in statements if s.get("sentiment") == "positive"]
        neg = [s for s in statements if s.get("sentiment") == "negative"]
        if pos:
            parts.append("\n## Enjoys / Values")
            for s in pos[:40]:
                parts.append(f"- {s['text'][:200]}")
        if neg:
            parts.append("\n## Avoids / Dislikes")
            for s in neg[:40]:
                parts.append(f"- {s['text'][:200]}")

    # ── Type: generalizing to new scenarios (cross-domain) ─────
    elif qt == "generalizing_to_new_scenarios":
        try:
            ctx = mk.pref_context(persona_name, question or topic or "general", max_prefs=30) or {}
        except Exception:
            ctx = {}
        parts.append("## Cross-Domain Preference Profile")
        for p in ctx.get("preferences", [])[:30]:
            parts.append(_format_pref_line(p))

        # Show recent high-salience statements
        strong = [s for s in statements
                  if s.get("kind") in ("like", "dislike", "start", "switch", "stop", "favorite")]
        if strong:
            parts.append("\n## Strong Signals (likes/dislikes/changes)")
            for s in strong[:50]:
                yr = f"({s['year']}) " if s.get("year") else ""
                parts.append(f"- {yr}[{s.get('kind','')}] {s['text'][:200]}")

    # ── Fallback / unknown question type ───────────────────────
    else:
        parts.append("## Preferences")
        for p in all_prefs[:30]:
            parts.append(_format_pref_line(p))
        if statements:
            parts.append("\n## Statements")
            for s in statements[:80]:
                parts.append(f"- {s['text'][:200]}")

    return "\n".join(parts)


# ────────────────────────────────────────────────────────────
# Keyword helpers
# ────────────────────────────────────────────────────────────

_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "for", "with", "about", "from", "by",
    "as", "if", "or", "and", "but", "not", "no", "yes", "do", "did",
    "does", "have", "had", "has", "my", "your", "our", "their", "his",
    "her", "this", "that", "these", "those", "there", "here",
    "i", "you", "we", "they", "it", "he", "she", "me", "him", "us",
    "them", "recommend", "recommendation", "suggestion", "suggest",
    "tell", "please", "would", "could", "should", "will", "can", "may",
    "what", "when", "where", "how", "why", "who", "which",
}


def _keywords_from(text: str) -> List[str]:
    if not text:
        return []
    toks = re.findall(r"[a-zA-Z][a-zA-Z\-']{2,}", text.lower())
    return [t for t in toks if t not in _STOP and len(t) >= 3]


def _filter_statements_by_keywords(
    statements: List[Dict[str, Any]], keywords: List[str]
) -> List[Dict[str, Any]]:
    if not keywords:
        return statements
    kwset = set(keywords)
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for s in statements:
        text_low = s["text"].lower()
        score = sum(1 for k in kwset if k in text_low)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], x[1].get("msg_idx", 0)))
    return [s for _, s in scored]


def topic_keywords_match(pref: Dict[str, Any], keywords: List[str]) -> bool:
    if not keywords:
        return True
    haystack = (pref.get("key", "") + " " + pref.get("value", "") + " "
                + pref.get("category", "")).lower()
    return any(k in haystack for k in keywords)
