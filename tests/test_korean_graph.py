"""한국어 graph extraction 테스트 (v2.1)

커버리지:
1. 조사(Josa) strip — 정상 / 엣지 / 2-char guard
2. 한국어 관계 추출 (relation 타입별)
3. 한·영 혼합 텍스트
4. graph_extract 통합 동작
"""
import pytest
from memkraft import MemKraft
from memkraft.graph import _strip_josa, _KO_RELATION_PATTERNS


@pytest.fixture
def mk(tmp_path):
    return MemKraft(base_dir=str(tmp_path))


# ====================================================================
# 1. 조사 (Josa) strip 테스트
# ====================================================================

class TestStripJosa:
    def test_basic_subject_josa(self):
        """기본 주격 조사 (이/가/은/는)"""
        assert _strip_josa("서준이") == "서준"
        assert _strip_josa("철수가") == "철수"
        assert _strip_josa("영희는") == "영희"
        assert _strip_josa("민수은") == "민수"  # 비문이지만 패턴 차원

    def test_object_josa(self):
        """목적격 조사 (을/를)"""
        assert _strip_josa("김치를") == "김치"
        assert _strip_josa("영화을") == "영화"  # 비문 OK

    def test_locative_josa(self):
        """처소격 조사 (에/에서/으로/로)"""
        assert _strip_josa("서울에") == "서울"
        assert _strip_josa("학교에서") == "학교"
        assert _strip_josa("부산으로") == "부산"
        assert _strip_josa("회사로") == "회사"

    def test_possessive_josa(self):
        """소유격 조사 (의)"""
        assert _strip_josa("해시드의") == "해시드"

    def test_two_char_guard(self):
        """2-char guard: '이은'(이름) — 조사 strip 했을 때 1글자 미만이면 원본 유지"""
        # '이은'에서 '은' strip → '이' 한 글자 → 원본 유지
        assert _strip_josa("이은") == "이은"
        # '의' strip → 빈 문자열 → 원본 유지
        assert _strip_josa("의") == "의"

    def test_no_josa(self):
        """조사가 없으면 원본 그대로"""
        assert _strip_josa("해시드") == "해시드"
        assert _strip_josa("서준") == "서준"

    def test_non_korean_unchanged(self):
        """한글이 아닌 단어는 원본 그대로 (영어/숫자)"""
        assert _strip_josa("Google") == "Google"
        assert _strip_josa("Sarah") == "Sarah"
        assert _strip_josa("123") == "123"
        assert _strip_josa("") == ""

    def test_long_josa(self):
        """긴 조사 (으로서, 부터, 까지 등)"""
        assert _strip_josa("대표로서") == "대표"
        assert _strip_josa("어제부터") == "어제"
        assert _strip_josa("내일까지") == "내일"


# ====================================================================
# 2. 한국어 관계 추출 (relation 타입별)
# ====================================================================

class TestKoreanRelationExtraction:
    def test_works_at_verb(self, mk):
        result = mk.graph_extract("서준이는 해시드에서 일한다")
        assert result["edges_added"] >= 1
        # neighbors of 서준
        neighbors = mk.graph_neighbors("서준")
        targets = [(n["relation"], n["target"]) for n in neighbors]
        assert ("works_at", "해시드") in targets

    def test_works_at_title(self, mk):
        """직책 명사 패턴 — CEO, 대표 등"""
        mk.graph_extract("서준은 해시드 CEO")
        neighbors = mk.graph_neighbors("서준")
        targets = [(n["relation"], n["target"]) for n in neighbors]
        assert any(r == "works_at" and t == "해시드" for r, t in targets)

    def test_works_at_with_possessive(self, mk):
        """X는 Y의 대표"""
        mk.graph_extract("서준은 해시드의 대표")
        neighbors = mk.graph_neighbors("서준")
        assert any(n["relation"] == "works_at" and n["target"] == "해시드" for n in neighbors)

    def test_lives_in(self, mk):
        mk.graph_extract("철수는 서울에 산다")
        neighbors = mk.graph_neighbors("철수")
        assert any(n["relation"] == "lives_in" and n["target"] == "서울" for n in neighbors)

    def test_lives_in_moved(self, mk):
        mk.graph_extract("영희는 부산으로 이사했다")
        neighbors = mk.graph_neighbors("영희")
        assert any(n["relation"] == "lives_in" and n["target"] == "부산" for n in neighbors)

    def test_likes(self, mk):
        mk.graph_extract("영희는 김치를 좋아해")
        neighbors = mk.graph_neighbors("영희")
        assert any(n["relation"] == "likes" and n["target"] == "김치" for n in neighbors)

    def test_likes_love(self, mk):
        mk.graph_extract("철수는 영화를 사랑한다")
        neighbors = mk.graph_neighbors("철수")
        assert any(n["relation"] == "likes" and n["target"] == "영화" for n in neighbors)

    def test_knows_meet(self, mk):
        mk.graph_extract("철수는 영희를 만났다")
        neighbors = mk.graph_neighbors("철수")
        assert any(n["relation"] == "knows" and n["target"] == "영희" for n in neighbors)

    def test_knows_friend(self, mk):
        mk.graph_extract("서준은 영수와 친구")
        neighbors = mk.graph_neighbors("서준")
        assert any(n["relation"] == "knows" and n["target"] == "영수" for n in neighbors)

    def test_is_a(self, mk):
        mk.graph_extract("철수는 의사다.")
        neighbors = mk.graph_neighbors("철수")
        assert any(n["relation"] == "is_a" and n["target"] == "의사" for n in neighbors)

    def test_is_a_polite(self, mk):
        mk.graph_extract("영희는 변호사입니다")
        neighbors = mk.graph_neighbors("영희")
        assert any(n["relation"] == "is_a" and n["target"] == "변호사" for n in neighbors)

    def test_partner_of(self, mk):
        mk.graph_extract("철수는 영희와 결혼했다")
        neighbors = mk.graph_neighbors("철수")
        assert any(n["relation"] == "partner_of" and n["target"] == "영희" for n in neighbors)

    def test_partner_of_couple(self, mk):
        mk.graph_extract("철수와 영희는 부부")
        # 어느 방향이든 edge 하나라도 있으면 통과
        all_edges = mk.graph_neighbors("철수") + mk.graph_neighbors("영희")
        assert any(e["relation"] == "partner_of" for e in all_edges)

    def test_studied_at(self, mk):
        mk.graph_extract("서준은 서울대에서 공부한다")
        neighbors = mk.graph_neighbors("서준")
        assert any(n["relation"] == "studied_at" and n["target"] == "서울대" for n in neighbors)

    def test_graduated_from(self, mk):
        mk.graph_extract("서준은 서울대를 졸업했다")
        neighbors = mk.graph_neighbors("서준")
        assert any(n["relation"] == "graduated_from" and n["target"] == "서울대" for n in neighbors)

    def test_graduated_alumni(self, mk):
        mk.graph_extract("영희는 카이스트 출신")
        neighbors = mk.graph_neighbors("영희")
        assert any(n["relation"] == "graduated_from" and n["target"] == "카이스트" for n in neighbors)

    def test_born_in(self, mk):
        mk.graph_extract("철수는 부산에서 태어났다")
        neighbors = mk.graph_neighbors("철수")
        assert any(n["relation"] == "born_in" and n["target"] == "부산" for n in neighbors)

    def test_hobby_is(self, mk):
        mk.graph_extract("철수의 취미는 독서")
        neighbors = mk.graph_neighbors("철수")
        assert any(n["relation"] == "hobby_is" and n["target"] == "독서" for n in neighbors)

    def test_founded(self, mk):
        mk.graph_extract("서준이는 해시드를 창업했다")
        neighbors = mk.graph_neighbors("서준")
        assert any(n["relation"] == "founded" and n["target"] == "해시드" for n in neighbors)

    def test_part_of(self, mk):
        mk.graph_extract("해시드는 트리플스의 자회사")
        neighbors = mk.graph_neighbors("해시드")
        assert any(n["relation"] == "part_of" and n["target"] == "트리플스" for n in neighbors)

    def test_invested_in(self, mk):
        mk.graph_extract("서준은 트리플스에 투자했다")
        neighbors = mk.graph_neighbors("서준")
        assert any(n["relation"] == "invested_in" and n["target"] == "트리플스" for n in neighbors)

    def test_advised_by(self, mk):
        mk.graph_extract("서준의 멘토는 영수")
        neighbors = mk.graph_neighbors("서준")
        assert any(n["relation"] == "advised_by" and n["target"] == "영수" for n in neighbors)


# ====================================================================
# 3. 한·영 혼합 텍스트
# ====================================================================

class TestMixedLanguage:
    def test_mixed_korean_english(self, mk):
        """한·영 혼합 텍스트에서 양쪽 모두 추출"""
        text = "Sarah works at Google. 서준은 해시드 CEO."
        result = mk.graph_extract(text)
        assert result["edges_added"] >= 2
        stats = mk.graph_stats()
        # sarah, google, 서준, 해시드 4 노드 이상
        assert stats["nodes"] >= 4

    def test_korean_search_via_query(self, mk):
        """한국어 쿼리로 graph_search"""
        mk.graph_extract("서준은 해시드 CEO. 해시드는 서울에 위치한다.")
        # 서준 → 해시드 → 서울 (multi-hop)
        results = mk.graph_search("서준이는 어디서 일해?")
        assert len(results) > 0
        # 결과 어딘가에 '서준' 또는 '해시드' 포함
        joined = " ".join(results).lower()
        assert "서준" in joined or "해시드" in joined


# ====================================================================
# 4. graph_extract 통합 / 회귀
# ====================================================================

class TestGraphExtractIntegration:
    def test_multi_relation_single_text(self, mk):
        """한 문장에 여러 관계"""
        text = "서준은 해시드 CEO. 서준은 서울에 산다. 서준은 카이스트 출신."
        result = mk.graph_extract(text)
        assert result["edges_added"] >= 3

        neighbors = mk.graph_neighbors("서준")
        relations = [n["relation"] for n in neighbors]
        assert "works_at" in relations
        assert "lives_in" in relations
        assert "graduated_from" in relations

    def test_no_duplicates_korean(self, mk):
        """동일 텍스트 두 번 extract — 중복 edge 없음"""
        text = "서준이는 해시드에서 일한다"
        mk.graph_extract(text)
        mk.graph_extract(text)
        stats = mk.graph_stats()
        # works_at edge 하나만
        rel_count = stats["top_relations"].get("works_at", 0)
        assert rel_count == 1

    def test_stopwords_skipped(self, mk):
        """한국어 stopword 단독 매치는 skip"""
        # '그는'은 stopword라 추출 안 되어야 함
        text = "그는 그것을 좋아한다"
        mk.graph_extract(text)
        stats = mk.graph_stats()
        # '그' 등이 추출되더라도 stopword 필터링으로 skip
        # 적어도 잘못된 노드 폭증은 없음
        assert stats["nodes"] <= 2  # 최악 ('그것' 같은 약한 매치 1~2개)

    def test_empty_text(self, mk):
        result = mk.graph_extract("")
        assert result["edges_added"] == 0
        assert result["nodes_added"] == 0

    def test_return_value_format(self, mk):
        result = mk.graph_extract("서준은 해시드 CEO")
        assert "nodes_added" in result
        assert "edges_added" in result
        assert isinstance(result["nodes_added"], int)
        assert isinstance(result["edges_added"], int)
