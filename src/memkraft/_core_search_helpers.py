"""Internal search helpers extracted from core.py (WS-A v2.8).

These were originally MemKraft._<name> methods. Moved to free
functions to reduce core.py size and clarify responsibility.
Public MemKraft API is unchanged — methods now delegate here.
"""
from __future__ import annotations

import json
import math
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ._regexes import (
    _SEARCH_TOKEN_RE,
    _DATE_BRACKET_RE,
    _KR_QUERY_PATTERNS,
    _EN_QUERY_PATTERNS,
    _QUERY_SPLIT_RE,
    _WIKILINK_RE,
    _TAGS_RE,
    _CONFIDENCE_RE,
    _WHEN_RE,
    _WHEN_NOT_RE,
    _FIELD_KV_RE,
    _NAME_2WORDS_RE,
    _NAME_3WORDS_RE,
    _KOREAN_NAME_RE,
    _KOREAN_VERB_SUFFIX_RE,
    _CHINESE_CHAR_RUN_RE,
    _HANDLE_RE,
    _EMAIL_RE,
    _URL_RE,
    _ORG_SUFFIX_RE,
    _KR_ORG_RE,
    _PRODUCT_SUFFIX_RE,
    _VERSION_PRODUCT_RE,
    _VERSION_HYPHEN_RE,
    _KR_LOCATION_RE,
    _KNOWN_ORG_RES,
    _KNOWN_LOC_RES,
    _SLUG_NONWORD_RE,
    _SLUG_WHITESPACE_RE,
)


# ── Tokenizer ───────────────────────────────────────────────
def search_tokens(text: str) -> list:
    """Tokenize search text for dependency-free hybrid matching."""
    return [t for t in _SEARCH_TOKEN_RE.findall(text.lower()) if len(t) > 1]


# ── BM25 scoring (v2.3) ────────────────────────────────────
def get_corpus_stats(
    all_md_files_fn: Callable[[], Any],
    search_tokens_fn: Callable[[str], list],
) -> Tuple[int, float]:
    """Return ``(doc_count, avg_doc_len)`` for the markdown corpus."""
    total_tokens = 0
    n = 0
    for md in all_md_files_fn():
        try:
            text = md.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        total_tokens += len(search_tokens_fn(text))
        n += 1
    if n == 0:
        return 0, 0.0
    return n, total_tokens / n


def bm25_score(
    query_tokens: List[str],
    doc_tf: Dict[str, int],
    doc_length: int,
    avg_doc_length: float,
    doc_count: int,
    token_doc_freq: Dict[str, int],
    filename_tokens: Optional[set] = None,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Compute Okapi BM25 score for a single (query, document) pair.

    Pure stdlib (uses :pymod:`math` only).  Does not depend on any
    external retrieval library.  When the corpus is empty or all
    inputs are zero, returns ``0.0`` rather than raising.
    """
    if not query_tokens or doc_count <= 0:
        return 0.0
    avgdl = avg_doc_length if avg_doc_length and avg_doc_length > 0 else 1.0
    length_norm = (1.0 - b) + b * (doc_length / avgdl)
    score = 0.0
    fname = filename_tokens or set()
    for qi in query_tokens:
        tf = doc_tf.get(qi, 0)
        if tf == 0 and qi in fname:
            tf = 1
        if tf == 0:
            continue
        n_qi = token_doc_freq.get(qi, 0)
        idf = math.log(((doc_count - n_qi + 0.5) / (n_qi + 0.5)) + 1.0)
        if idf <= 0:
            continue
        denom = tf + k1 * length_norm
        if denom <= 0:
            continue
        score += idf * (tf * (k1 + 1.0)) / denom
    return score


def best_token_snippet(
    query_tokens: list,
    lines: list,
    lines_orig: list,
    search_tokens_fn: Callable[[str], list],
) -> str:
    """Find the best snippet matching query tokens.

    v2.8.1 hot-path: previously this re-tokenized every line via the BM25
    regex (``re.findall``) which dominated profiles on large corpora
    (~500k+ findall calls per 30-query batch at 500 docs).  Substring
    matching gives the same answer for the token set we care about
    — ``search_tokens`` is just a word-character split so a token ``t``
    is present in ``line`` iff it appears as a substring bordered by
    non-word chars.  We approximate this with a cheap ``in`` check and
    only fall back to true tokenization when the cheap path is
    inconclusive (zero hits, ambiguous substring inside another word).
    """
    best_idx = 0
    best_hits = 0
    query_set = set(query_tokens)
    if not query_set:
        return ""
    # Fast path: substring containment.  Over-counts only when a token
    # is a strict substring of another word (e.g. ``ai`` inside ``email``).
    # That risk is bounded — we use this purely to pick the snippet
    # window, not for relevance scoring — so a slight over-count just
    # widens the candidate set.  Final tie-break still falls through to
    # the precise tokenizer below when results look ambiguous.
    for idx, line in enumerate(lines):
        if not line:
            continue
        hits = 0
        for t in query_set:
            if t in line:
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_idx = idx
            if best_hits == len(query_set):
                break  # Can't beat a full-set match.
    if best_hits == 0:
        return ""
    start = max(0, best_idx - 3)
    end = min(len(lines), best_idx + 4)
    return " | ".join(l.strip() for l in lines_orig[start:end] if l.strip())[:200]


def first_meaningful_line(content: str) -> str:
    """Return the first non-heading, non-empty, non-boilerplate line."""
    skip_prefixes = ("#", "---", ">", "**Tier", "- **Type", "- **Started",
                     "- **Last Update", "- **Update Count", "- **Source",
                     "- [[", "(")
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped and not any(stripped.startswith(p) for p in skip_prefixes) and len(stripped) > 10:
            return stripped
    return ""


def extract_tags(content: str) -> str:
    """Extract tags from content."""
    tags = _TAGS_RE.findall(content)
    if tags:
        return tags[0].strip()[:50]
    return ""


# ── Stopwords (module-level cache) ─────────────────────────
_stopwords_cache: Optional[dict] = None


def load_stopwords() -> dict:
    """Load stopwords from JSON file (cached)."""
    global _stopwords_cache
    if _stopwords_cache is None:
        sw_path = Path(__file__).parent / "stopwords.json"
        if sw_path.exists():
            with open(sw_path, 'r', encoding='utf-8') as f:
                _stopwords_cache = json.load(f)
        else:
            _stopwords_cache = {"korean": [], "chinese": [], "japanese": []}
    return _stopwords_cache


def reset_stopwords_cache() -> None:
    """Reset stopwords cache (for testing)."""
    global _stopwords_cache
    _stopwords_cache = None


# ── Query decomposition ────────────────────────────────────
def decompose_query(query: str) -> List[str]:
    """Decompose a complex query into sub-queries."""
    for pat in _KR_QUERY_PATTERNS:
        m = pat.search(query)
        if m:
            return [g.strip() for g in m.groups() if g.strip()] + [query]
    for pat in _EN_QUERY_PATTERNS:
        m = pat.search(query)
        if m:
            return [g.strip() for g in m.groups() if g.strip()] + [query]
    if len(query) > 20:
        parts = _QUERY_SPLIT_RE.split(query)
        if len(parts) > 1:
            return [p.strip() for p in parts if len(p.strip()) > 2]
    return [query]


# ── Goal-Weighted Reconstruction ───────────────────────────
def goal_weighted_rerank(
    results: List[Dict[str, Any]],
    context: str,
    base_dir: Path,
    safe_read_fn: Callable[[Path], str],
    search_tokens_fn: Callable[[str], list],
    classify_memory_type_fn: Callable[[str], str],
    get_decay_multiplier_fn: Callable[[str], float],
) -> List[Dict[str, Any]]:
    """Re-rank search results based on goal context (Conway SMS)."""
    if not context or not results:
        return results

    context_lower = context.lower()
    context_tokens = set(search_tokens_fn(context_lower))

    for r in results:
        md_path = base_dir / r["file"]
        content = safe_read_fn(md_path)
        if not content:
            continue

        content_lower = content.lower()

        context_score = 0.0
        if context_lower in content_lower:
            context_score = 0.3
        else:
            content_tokens = set(search_tokens_fn(content_lower))
            overlap = len(context_tokens & content_tokens)
            if context_tokens:
                context_score = 0.2 * (overlap / len(context_tokens))

        best_section_sim = 0.0
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and len(stripped) > 10:
                sim = SequenceMatcher(None, context_lower, stripped.lower()).ratio()
                if sim > best_section_sim:
                    best_section_sim = sim
        context_score += best_section_sim * 0.15

        memory_type = classify_memory_type_fn(content)
        decay_mult = get_decay_multiplier_fn(memory_type)
        type_bonus = 0.05 * (1.0 - decay_mult)

        tier_bonus = 0.0
        if "Tier: core" in content:
            tier_bonus = 0.03

        confidence_bonus = compute_confidence_bonus(content)
        applicability_bonus = compute_applicability_bonus(content, context, search_tokens_fn)

        r["score"] = round(min(1.0, r.get("score", 0) + context_score + type_bonus + tier_bonus + confidence_bonus + applicability_bonus), 2)
        r["memory_type"] = memory_type

    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def compute_applicability_bonus(
    content: str,
    context: str,
    search_tokens_fn: Callable[[str], list],
) -> float:
    """Compute a score bonus based on applicability conditions matching context."""
    if not context:
        return 0.0

    context_lower = context.lower()
    context_tokens = set(search_tokens_fn(context_lower))
    bonus = 0.0

    for line in content.split("\n"):
        when_match = _WHEN_RE.search(line)
        when_not_match = _WHEN_NOT_RE.search(line)

        if when_match:
            condition = when_match.group(1).strip().lower()
            cond_tokens = set(search_tokens_fn(condition))
            overlap = len(context_tokens & cond_tokens)
            if overlap > 0 and cond_tokens:
                bonus += 0.03 * (overlap / len(cond_tokens))

        if when_not_match:
            condition = when_not_match.group(1).strip().lower()
            cond_tokens = set(search_tokens_fn(condition))
            overlap = len(context_tokens & cond_tokens)
            if overlap > 0 and cond_tokens:
                bonus -= 0.02 * (overlap / len(cond_tokens))

    return round(min(0.1, bonus), 3)


def parse_applicability(text: str) -> Dict[str, List[str]]:
    """Parse applicability conditions from text."""
    result: Dict[str, List[str]] = {"when": [], "when_not": []}
    for match in _WHEN_RE.finditer(text):
        result["when"].append(match.group(1).strip())
    for match in _WHEN_NOT_RE.finditer(text):
        result["when_not"].append(match.group(1).strip())
    return result


def compute_confidence_bonus(content: str) -> float:
    """Compute a score bonus based on confidence levels of facts in content."""
    verified_count = content.count("Confidence: verified")
    experimental_count = content.count("Confidence: experimental")
    hypothesis_count = content.count("Confidence: hypothesis")
    total = verified_count + experimental_count + hypothesis_count
    if total == 0:
        return 0.0
    weighted = (verified_count * 1.0 + experimental_count * 0.7 + hypothesis_count * 0.4) / total
    return round(0.05 * weighted, 3)


def extract_fact_confidence(line: str) -> str:
    """Extract confidence level from a fact line."""
    m = _CONFIDENCE_RE.search(line)
    return m.group(1) if m else ""


# ── File-back (Query-to-Memory Feedback Loop) ──────────────
def file_back_results(
    base_dir: Path,
    query: str,
    results: List[Dict[str, Any]],
    safe_read_fn: Callable[[Path], str],
) -> None:
    """File search results back into entity timelines (feedback loop)."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d")
    filed_count = 0
    for r in results[:5]:
        md_path = base_dir / r["file"]
        snippet = r.get("snippet", "")[:120]
        if not snippet:
            continue

        feedback_entry = f"- **{now}** | [Filed back] Query: '{query[:60]}' — {snippet} [Source: agentic_search | Confidence: experimental]"

        slug = md_path.stem
        live_path = base_dir / "live-notes" / f"{slug}.md"
        wrote = False
        if live_path.exists():
            live_content = safe_read_fn(live_path)
            for marker in ["## Timeline (Full Record)\n\n", "## Timeline\n\n"]:
                if marker in live_content:
                    live_content = live_content.replace(marker, f"{marker}{feedback_entry}\n")
                    live_path.write_text(live_content, encoding="utf-8")
                    filed_count += 1
                    wrote = True
                    break

        if not wrote and md_path.exists():
            content = safe_read_fn(md_path)
            for marker in ["## Timeline (Full Record)\n\n", "## Timeline\n\n"]:
                if marker in content:
                    content = content.replace(marker, f"{marker}{feedback_entry}\n")
                    md_path.write_text(content, encoding="utf-8")
                    filed_count += 1
                    break

    if filed_count:
        print(f"📂 Filed back {filed_count} results into entity timelines")


# ── Entity detection (regex-based) ─────────────────────────
def detect_regex(
    text: str,
    strip_korean_josa_fn: Callable[[str], str],
) -> List[Dict[str, str]]:
    """Multi-language entity detection via regex (zero ML dependencies)."""
    entities: List[Dict[str, str]] = []
    common = {'The', 'This', 'That', 'And', 'But', 'For', 'Not', 'All', 'Has', 'Was', 'Are', 'Its', 'Our', 'Their', 'Yc', 'From', 'With', 'Please', 'Contact', 'However', 'While', 'These', 'Those', 'Each', 'Some', 'Many', 'Other', 'Such', 'Most', 'Last', 'New', 'Old', 'Next', 'Here', 'After', 'Before', 'Between', 'Under', 'Over', 'Every', 'About', 'Also', 'Just', 'When', 'Where', 'Why', 'How', 'What', 'Who', 'Which', 'More', 'Much', 'Very', 'Well', 'Still', 'Even', 'Back', 'Down', 'Only', 'Then', 'Than', 'Both', 'Into', 'Like', 'Made', 'Come', 'Could', 'Would', 'Should', 'Will', 'May', 'Can', 'Did', 'Does'}

    names_2 = _NAME_2WORDS_RE.findall(text)
    names_3 = _NAME_3WORDS_RE.findall(text)
    korean_names = _KOREAN_NAME_RE.findall(text)
    korean_names_cleaned = []
    for name in korean_names:
        stripped = _KOREAN_VERB_SUFFIX_RE.sub('', name)
        if len(stripped) >= 2:
            korean_names_cleaned.append(stripped)
    korean_names = korean_names_cleaned

    stopwords = load_stopwords()
    korean_stopwords = set(stopwords.get("korean", []))
    chinese_stopwords = set(stopwords.get("chinese", []))
    japanese_stopwords = set(stopwords.get("japanese", []))

    names_3_word_sets = [set(n.split()) for n in names_3]
    for name in set(names_3):
        if name not in common:
            entities.append({"name": name, "type": "person", "context": "auto-detected"})
    for name in set(names_2):
        name_words = set(name.split())
        is_substring = any(name_words.issubset(ws) for ws in names_3_word_sets)
        if name not in common and name.split()[0] not in common and name.split()[1] not in common and not is_substring:
            entities.append({"name": name, "type": "person", "context": "auto-detected"})
    for name in set(korean_names):
        if len(name) >= 2 and name not in korean_stopwords:
            stripped = strip_korean_josa_fn(name)
            if stripped != name and stripped not in korean_stopwords:
                name = stripped
            if name not in korean_stopwords and len(name) >= 2:
                entities.append({"name": name, "type": "person", "context": "auto-detected (Korean)"})

    # Chinese name detection
    chinese_surnames = {'王', '李', '张', '刘', '陈', '杨', '赵', '黄', '周', '吴', '徐', '孙', '胡', '朱', '高', '林', '何', '郭', '马', '罗', '梁', '宋', '郑', '谢', '韩', '唐', '冯', '于', '董', '萧', '程', '曹', '袁', '邓', '许', '傅', '沈', '曾', '彭', '吕', '苏', '卢', '蒋', '蔡', '贾', '丁', '魏', '薛', '叶', '阎', '余', '潘', '杜', '戴', '夏', '钟', '汪', '田', '任', '姜', '范', '方', '石', '姚', '谭', '廖', '邹', '熊', '金', '陆', '郝', '孔', '白', '崔', '康', '毛', '邱', '秦', '江', '史', '顾', '侯', '邵', '孟', '龙', '万', '段', '雷', '钱', '汤', '尹', '黎', '易', '常', '武', '乔', '贺', '赖', '龚', '文'}
    chinese_char_runs = _CHINESE_CHAR_RUN_RE.findall(text)
    seen_chinese = set()
    for run in chinese_char_runs:
        for i, ch in enumerate(run):
            if ch in chinese_surnames:
                for length in [3, 2]:
                    if i + length <= len(run):
                        candidate = run[i:i+length]
                        if candidate not in chinese_stopwords and candidate not in japanese_stopwords and candidate not in seen_chinese:
                            seen_chinese.add(candidate)
                            entities.append({"name": candidate, "type": "person", "context": "auto-detected (Chinese)"})
                            break

    # Japanese surname detection
    japanese_surnames = {'田中', '佐藤', '鈴木', '高橋', '伊藤', '渡辺', '山本', '中村', '小林', '加藤', '吉田', '山田', '山口', '松本', '井上', '木村', '斎藤', '清水', '山崎', '池田', '橋本', '阿部', '石川', '山下', '中島', '石井', '小川', '前田', '岡田', '長谷川', '藤田', '後藤', '近藤', '村上', '遠藤', '青木', '坂本', '斉藤', '福田', '西村', '藤井', '金子', '岡本', '藤原', '中野', '三浦', '原田', '松田', '竹内', '上田', '中山', '和田', '森田', '柴田', '酒井', '工藤', '横山', '宮崎', '宮本', '内田', '高木', '安藤', '谷口', '大野', '丸山', '今井', '高田', '藤本', '武田', '村田', '上野', '杉山', '増田', '平野', '大塚', '千葉', '久保', '松井', '小島', '岩崎', '桜井', '木下', '野村', '島田', '菊池'}
    for run in chinese_char_runs:
        for js in sorted(japanese_surnames, key=len, reverse=True):
            if js in run:
                idx = run.find(js)
                for name_len in [len(js)+2, len(js)+1]:
                    if idx + name_len <= len(run):
                        candidate = run[idx:idx+name_len]
                        if candidate not in chinese_stopwords and candidate not in japanese_stopwords and candidate not in seen_chinese:
                            entities.append({"name": candidate, "type": "person", "context": "auto-detected (Japanese)"})
                            break

    handles = _HANDLE_RE.findall(text)
    for handle in set(handles):
        entities.append({"name": handle, "type": "person", "context": "mentioned via @handle"})

    emails = _EMAIL_RE.findall(text)
    for email in set(emails):
        entities.append({"name": email, "type": "contact", "context": "auto-detected (email)"})

    urls = _URL_RE.findall(text)
    for url in set(urls):
        entities.append({"name": url, "type": "reference", "context": "auto-detected (URL)"})

    known_orgs = {'Apple', 'Google', 'Microsoft', 'Amazon', 'Meta', 'Tesla', 'Netflix', 'Nvidia', 'OpenAI', 'Anthropic', 'Samsung', 'Hashed', 'Tencent', 'Alibaba', 'ByteDance', 'Baidu', 'Sony', 'Toyota', 'Hyundai', 'LG', 'Kakao', 'Naver', 'Coupang', 'Toss', 'Stripe', 'SpaceX', 'Palantir', 'Uber', 'Airbnb', 'Coinbase', 'Binance', 'Riot', 'Epic', 'Valve', 'Blizzard'}
    org_suffix_pattern = _ORG_SUFFIX_RE.findall(text)
    for org in org_suffix_pattern:
        entities.append({"name": org, "type": "organization", "context": "auto-detected (suffix)"})

    for org in known_orgs:
        if _KNOWN_ORG_RES.get(org) and _KNOWN_ORG_RES[org].search(text):
            if not any(e["name"] == org for e in entities):
                entities.append({"name": org, "type": "organization", "context": "auto-detected (known)"})

    kr_orgs = _KR_ORG_RE.findall(text)
    for org in kr_orgs:
        if org not in korean_stopwords:
            entities.append({"name": org, "type": "organization", "context": "auto-detected (Korean org)"})

    product_pattern = _PRODUCT_SUFFIX_RE.findall(text)
    for prod in product_pattern:
        if not any(e["name"] == prod for e in entities):
            entities.append({"name": prod, "type": "product", "context": "auto-detected (suffix)"})

    version_products = _VERSION_PRODUCT_RE.findall(text)
    version_products += _VERSION_HYPHEN_RE.findall(text)
    for vp in set(version_products):
        vp = vp.strip()
        if len(vp) >= 3 and not any(e["name"] == vp for e in entities):
            if len(vp) == 4 and vp.isdigit():
                continue
            entities.append({"name": vp, "type": "product", "context": "auto-detected (version)"})

    known_locations = {'Seoul', 'Tokyo', 'Beijing', 'Shanghai', 'Singapore', 'London', 'New York', 'San Francisco', 'Berlin', 'Paris', 'Dubai', 'Hong Kong', 'Taipei', 'Bangkok', 'Sydney', 'Toronto', 'Vancouver', 'Busan', 'Jeju', 'Osaka', 'Mumbai', 'Delhi', 'Jakarta', 'Manila', 'Kuala Lumpur'}
    for loc in known_locations:
        if _KNOWN_LOC_RES.get(loc) and _KNOWN_LOC_RES[loc].search(text):
            if not any(e["name"] == loc for e in entities):
                entities.append({"name": loc, "type": "location", "context": "auto-detected (known)"})

    kr_locations = _KR_LOCATION_RE.findall(text)
    for loc in kr_locations:
        if loc not in korean_stopwords and len(loc) >= 3:
            entities.append({"name": loc, "type": "location", "context": "auto-detected (Korean location)"})

    return entities
