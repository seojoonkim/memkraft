"""Precompiled regex constants for hot-path operations (WS-B).

All patterns are module-level compiled once at import time.
Naming convention: ``_<DESCRIPTIVE>_RE`` for Pattern objects,
``_<DESCRIPTIVE>_PATTERNS`` for lists of Patterns.

This module is imported by core.py, search.py, multi_pass.py, graph.py,
and other hot-path modules to avoid repeated re.compile() at call time.
"""

from __future__ import annotations

import re

# ── core.py: update() hot path ────────────────────────────────────
_UPDATE_COUNT_RE = re.compile(r'(?:Update Count|업데이트 횟수):\*\* (\d+)')
_LAST_UPDATE_RE = re.compile(r'(?:Last Update|마지막 업데이트):\*\* \d{4}-\d{2}-\d{2}')
_DATE_YYYYMMDD_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
_DIGITS_RE = re.compile(r'\d+')

# ── core.py: _extract_bullet_facts() ──────────────────────────────
_SOURCE_TAG_RE = re.compile(r'\[Source:.*?\]')
_CONFLICT_TAG_RE = re.compile(r'\[CONFLICT\]')
_DATE_BULLET_RE = re.compile(r'^- \*\*\d{4}-\d{2}-\d{2}\*\* \| ')
_PENDING_BULLET_RE = re.compile(r'^- ⏳ ')

# ── core.py: _extract_registry_facts() ────────────────────────────
_MONEY_RE = re.compile(
    r'[\$₩€]\s?[\d,.]+(?:\s*(?:million|billion|trillion|만|억|조|M|B|K))?\b',
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r'\d+(?:\.\d+)?%')
_COUNT_ITEMS_RE = re.compile(
    r'\d+(?:,\d+)*(?:\s+(?:items|users|employees|members|people|명|개|건|팀))'
)
_FACT_REGISTRY_LINE_RE = re.compile(r'^- (.+?)(?: \[Source:.*\])?$', re.MULTILINE)

# ── core.py: _apply_state_changes() ───────────────────────────────
_SECTION_HEADER_RE = re.compile(r'## (?:Current State|State|현재 상태)\n')
_NEXT_SECTION_RE = re.compile(r'\n## ')
_PLACEHOLDER_RE = re.compile(
    r'^\((?:Latest information accumulates here|enrichment needed).*\)\n?',
    re.MULTILINE,
)

# ── core.py: _extract_state_candidates() ──────────────────────────
# These are used with re.IGNORECASE; compile with flag.
_STATE_ROLE_RE = re.compile(
    r'(?:^|\b)(?:role|title|position)\s*(?::|is|=)\s*(.+)$', re.IGNORECASE,
)
_STATE_ROLE_IS_RE = re.compile(
    r'\b(?:is|became|serves as|was named|appointed as)\s+(?:the\s+)?(.+?)(?:\.|$)',
    re.IGNORECASE,
)
_STATE_AFFIL_RE = re.compile(
    r'(?:^|\b)(?:affiliation|company|organization|org)\s*(?::|is|=)\s*(.+)$',
    re.IGNORECASE,
)
_STATE_AFFIL_VERB_RE = re.compile(
    r'\b(?:joined|left|moved to)\s+(.+?)(?:\.|$)', re.IGNORECASE,
)
_STATE_STATUS_RE = re.compile(
    r'(?:^|\b)status\s*(?::|is|=)\s*(.+)$', re.IGNORECASE,
)
_STATE_LOCATION_RE = re.compile(
    r'(?:^|\b)location\s*(?::|is|=)\s*(.+)$', re.IGNORECASE,
)
_STATE_LOCATION_IS_RE = re.compile(
    r'\b(?:based in|located in)\s+(.+?)(?:\.|$)', re.IGNORECASE,
)

# ── core.py: _detect_regex() NER patterns ─────────────────────────
_NAME_2WORDS_RE = re.compile(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b')
_NAME_3WORDS_RE = re.compile(r'\b([A-Z][a-z]+ [A-Z][a-z]+ [A-Z][a-z]+)\b')
_KOREAN_NAME_RE = re.compile(r'[\uAC00-\uD7AF]{2,4}')
_KOREAN_VERB_SUFFIX_RE = re.compile(
    r'(했|할|해|되|됐|받|만|지|보|주|가|오|알|인|있|없|갈|될|만들|사용|개발|적용|설정|'
    r'확인|업데이트|추가|수정|삭제|생성|실행|테스트|분석|검색|연결|설치|시작|완료|진행|'
    r'보고|논의|발표|참여|준비|요청|제안|검토|승인|거절|검증|배포|구축|도입|운영|관리|'
    r'모니터링|추적|감지|정리|보강|업그레이드|마이그레이션|이|이다|입니다|였다|였음)'
    r'(다|해|함|요|서|고|며|니|까|지|은|는|이|을|를|와|과|도|만|로|으로|라|라서|의)?$'
)
_CHINESE_CHAR_RUN_RE = re.compile(r'[\u4E00-\u9FFF]+')
_HANDLE_RE = re.compile(r'(?:^|(?<=\s))@(\w+)')
_EMAIL_RE = re.compile(
    r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
)
_URL_RE = re.compile(r'https?://[^\s)<>\]]+')
_ORG_SUFFIX_RE = re.compile(
    r'\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+'
    r'(?:Corp|Inc|Ltd|Co|Foundation|Labs|Group|Capital|Ventures|Systems|'
    r'Technologies|Networks|AI|IO|Software|Digital|Dynamics|Industries|Holdings))\b'
)
_KR_ORG_RE = re.compile(
    r'([가-힣]{2,8}(?:기관|회사|은행|그룹|재단|연구소|대학|대학교|병원|센터|연합|협회|'
    r'위원회|청|부|처|실|국|원|전자|자동차|물산|중공업|건설|해운|항공|통신|제약|화학|철강|'
    r'에너지|인터넷|소프트웨어|테크|랩스|벤처스|캐피탈|파트너스|네트워크|시스템|솔루션|'
    r'미디어|엔터|엔터테인먼트|게임즈|스튜디오|플랫폼))'
)
_PRODUCT_SUFFIX_RE = re.compile(
    r'\b([A-Za-z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+'
    r'(?:Pro|Max|Ultra|Plus|Mini|Air|Lite|SE|Studio|Suite|Cloud|Engine|Platform|OS|OSX))\b'
)
_VERSION_PRODUCT_RE = re.compile(
    r'\b([a-zA-Z]*[A-Z][A-Za-z]*[\s-]\d+(?:\.\d+)?(?:\s+(?:Pro|Max|Ultra|Plus|Mini|Air))?)\b'
)
_VERSION_HYPHEN_RE = re.compile(r'\b([A-Z][A-Za-z]+-\d+(?:\.\d+)?)\b')
_KR_LOCATION_RE = re.compile(
    r'([가-힣]{2,5}(?:시|도|구|군|읍|면|동|로|길))'
)

# ── core.py: _search_tokens() ─────────────────────────────────────
_SEARCH_TOKEN_RE = re.compile(r'[\w\uAC00-\uD7AF\u4E00-\u9FFF]+')

# ── core.py: _decompose_query() ───────────────────────────────────
_KR_QUERY_PATTERNS = [
    re.compile(r'(.+?)이/가 누구'),
    re.compile(r'(.+?)의 (.+?)'),
    re.compile(r'(.+?)은/는 어디'),
    re.compile(r'(.+?)에 대해'),
]
_EN_QUERY_PATTERNS = [
    re.compile(r'who is (.+)', re.IGNORECASE),
    re.compile(r'what is (.+)', re.IGNORECASE),
    re.compile(r'tell me about (.+)', re.IGNORECASE),
    re.compile(r'(.+?) of (.+)', re.IGNORECASE),
    re.compile(r"(.+?)'s (.+)", re.IGNORECASE),
]
_QUERY_SPLIT_RE = re.compile(r'[,.]|\s+(?:and|or|but|그리고|또는)\s+')

# ── core.py: conflict detection ───────────────────────────────────
_CONFLICT_BLOCK_RE = re.compile(
    r'### (.+?)\n- \*\*Old:\*\* (.+?)\n- \*\*New:\*\* (.+?)\n'
    r'- \*\*Similarity:\*\* ([\d.]+)\n- \*\*File:\*\* (.+?)\n'
    r'- \*\*Status:\*\* ❌ unresolved'
)

# ── core.py: _slugify() ───────────────────────────────────────────
_SLUG_NONWORD_RE = re.compile(r'[^\w\s\-\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]')
_SLUG_WHITESPACE_RE = re.compile(r'\s+')

# ── core.py: facts / metadata extraction ──────────────────────────
_FACT_LINE_RE = re.compile(r'^- (.+)$', re.MULTILINE)
_DATE_BRACKET_RE = re.compile(r'\*\*(\d{4}-\d{2}-\d{2})\*\*')
_WHEN_RE = re.compile(r'When:\s*([^|\]]+)')
_WHEN_NOT_RE = re.compile(r'When NOT:\s*([^|\]]+)')
_CONFIDENCE_RE = re.compile(
    r'Confidence:\s*(verified|experimental|hypothesis)'
)
_WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')
_TIER_RE = re.compile(r'\*\*Tier: \w+')
_STATUS_RE = re.compile(r'\*\*Status:\*\* (\w+)')
_DESC_RE = re.compile(r'\*\*Description:\*\* (.+)')
_RESOLUTION_RE = re.compile(r'\*\*Resolution:\*\* (.+)')
_EVIDENCE_RE = re.compile(r'- \*\*.+?\*\* \| \[H\d+\]')
_TAGS_RE = re.compile(r'(?:tags?:|태그:)\s*(.+)', re.IGNORECASE)
_LINK_COUNT_RE = re.compile(r'\[\[[^\]]+\]\]')

# ── core.py: _detect_regex() known orgs/locations word-boundary ──
# (These are used with re.escape(org) + word boundary, so we keep them
# as patterns that are built dynamically — but we can cache the
# re.escape() results.  Actually, since the known_orgs / known_locations
# sets are fixed, we pre-build the compiled patterns.)

_KNOWN_ORGS = {
    'Apple', 'Google', 'Microsoft', 'Amazon', 'Meta', 'Tesla', 'Netflix',
    'Nvidia', 'OpenAI', 'Anthropic', 'Samsung', 'Hashed', 'Tencent',
    'Alibaba', 'ByteDance', 'Baidu', 'Sony', 'Toyota', 'Hyundai', 'LG',
    'Kakao', 'Naver', 'Coupang', 'Toss', 'Stripe', 'SpaceX', 'Palantir',
    'Uber', 'Airbnb', 'Coinbase', 'Binance', 'Riot', 'Epic', 'Valve',
    'Blizzard',
}
_KNOWN_LOCATIONS = {
    'Seoul', 'Tokyo', 'Beijing', 'Shanghai', 'Singapore', 'London',
    'New York', 'San Francisco', 'Berlin', 'Paris', 'Dubai', 'Hong Kong',
    'Taipei', 'Bangkok', 'Sydney', 'Toronto', 'Vancouver', 'Busan', 'Jeju',
    'Osaka', 'Mumbai', 'Delhi', 'Jakarta', 'Manila', 'Kuala Lumpur',
}
# Pre-compile word-boundary matchers for known entities
_KNOWN_ORG_RES = {
    org: re.compile(r'\b' + re.escape(org) + r'\b') for org in _KNOWN_ORGS
}
_KNOWN_LOC_RES = {
    loc: re.compile(r'\b' + re.escape(loc) + r'\b') for loc in _KNOWN_LOCATIONS
}

# ── core.py: _is_material_state_change() / _detect_conflicts() ────
_FIELD_KV_RE = re.compile(r'(\w+):\s*(.+?)$')

# ── core.py: detect_conflicts patterns ────────────────────────────
_KR_CONFLICT_PATTERNS = [
    re.compile(r'(.+?)이/가 누구'),
    re.compile(r'(.+?)의 (.+?)'),
    re.compile(r'(.+?)은/는 어디'),
    re.compile(r'(.+?)에 대해'),
]

# ── core.py: open items ───────────────────────────────────────────
_OPEN_ITEM_RE = re.compile(r'- \[ \] (.+?)(?:\n|$)')

# ── core.py: korean josa strip (used in _detect_regex) ────────────
# The verb-ending pattern used in _detect_regex is _KOREAN_VERB_SUFFIX_RE above.

# ── core.py: norms ────────────────────────────────────────────────
_KR_JOSA_STRIP_RE = re.compile(r'(이|을|를|은|는|에|로|의)$')

# ── core.py: hypothesis patterns ──────────────────────────────────
_HYPOTHESIS_TESTING_RE = re.compile(
    r'(### .+?: .+?\n- \*\*Status:\*\* )🧪 TESTING'
)

# ── core.py: debug timeline ───────────────────────────────────────
_DEBUG_HYPOTHESIS_RE = re.compile(
    r'### (H\d+): (.+?)\n- \*\*Status:\*\* (.+?)\n(?:- \*\*Rejected reason:\*\* .+?\n)?- \*\*Created:\*\* (.+?)\n',
)

# ── search.py: tokenization ───────────────────────────────────────
# (search.py uses the same pattern as _SEARCH_TOKEN_RE)

# ── multi_pass.py: entity extraction ──────────────────────────────
_EN_CAPITALIZED_RE = re.compile(r'\b[A-Z][a-zA-Z]{2,}\b')
_KO_NOUN_RE = re.compile(r'[\uac00-\ud7af]{2,}')
_MULTI_TOKEN_RE = re.compile(r'\w+')

# ── graph.py ──────────────────────────────────────────────────────
# _HANGUL_RE and _JOSA_PATTERN already precompiled at module level in graph.py

# ── hierarchical.py ───────────────────────────────────────────────
_HIER_EN_WORDS_RE = re.compile(r'\b[a-zA-Z]{3,}\b', re.IGNORECASE)
_HIER_QUERY_WORDS_RE = re.compile(r'\b\w{3,}\b')

# ── consolidation.py ──────────────────────────────────────────────
_ENTITY_HEADER_RE = re.compile(r'^#\s*Entity:\s*(.+)$', re.MULTILINE)

# ── temporal_chain.py ─────────────────────────────────────────────
_TEMPORAL_PAST_RE = re.compile(r'지난\s*(\d+)\s*(일|주|개월|년)')

# ── multimodal.py ─────────────────────────────────────────────────
_MULTI_NON_ALNUM_RE = re.compile(r'[^a-z0-9가-힣\-_ ]+')
_MULTI_WHITESPACE_RE = re.compile(r'\s+')
_MULTI_DASH_RE = re.compile(r'-+')

# ── context_compress.py ───────────────────────────────────────────
_WHITESPACE_COLLAPSE_RE = re.compile(r'\s+')

# ── preference_graph_sync.py ──────────────────────────────────────
_PREF_NON_ALNUM_RE = re.compile(r'[^a-z0-9]+')
_PREF_TOKEN_RE = re.compile(r'[a-z0-9]+')

# ── reasoning_bank.py ─────────────────────────────────────────────
_MULTI_DASH_STRIP_RE = re.compile(r'-+')

# ── lifecycle.py ──────────────────────────────────────────────────
_LIFECYCLE_LIST_RE = re.compile(r'^[-*]\s+(?:\[[\d\-]+\]\s*)?(.+)$')
_LIFECYCLE_KV_RE = re.compile(r'^\w[\w_]*\s*:')
_LAST_ACCESSED_RE = re.compile(
    r'\*\*Last Accessed:\*\*\s*(\d{4}-\d{2}-\d{2})'
)

# ── stats.py ──────────────────────────────────────────────────────
_WIKILINK_STATS_RE = re.compile(r'\[\[([^\[\]\n]+?)\]\]')

# ── runbook.py ────────────────────────────────────────────────────
# runbook uses re.search with user-provided patterns; no static precompile.

# ── routing.py ────────────────────────────────────────────────────
# routing uses re.escape(kw) dynamically; keep as-is.

# ── personamem.py ─────────────────────────────────────────────────
# personamem has many dynamic patterns with f-strings; keep as-is for now.
