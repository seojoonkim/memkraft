"""Regression: wording-only edits must not be logged as fact conflicts.

Real CONFLICTS.md from a Korean-language deployment had 96/96 unresolved
entries that were paraphrases ("바로 알려줄게" vs "알려줄게"). Value swaps must
still be caught.
"""
import pytest

from memkraft._core_detection_helpers import is_opposing


PARAPHRASES = [
    ("끝나면 내가 바로 알려줄게", "끝나면 내가 알려줄게"),
    ("정상에서 위험으로 바뀌었어", "정상에서 위험으로 올라갔어"),
    ("경고 수준까지 올라갔어", "경고 수준까지 올라왔어"),
    ("내가 줄여서 부하부터 낮출게", "내가 줄여서 부하를 낮출게"),
    ("실패해서 이번 변경과는 무관해", "실패해서 이번 수정과 상관없어"),
    ("the build is done now", "the build is now done"),
    ("느려진 비율:** 12", "느려진 비율: 12"),
]

VALUE_CHANGES = [
    ("마감일은 3월 3일이야", "마감일은 4월 20일이야"),
    ("배포 버전은 v4.5.0이야", "배포 버전은 v4.6.1이야"),
    ("기본 모델은 opus-5-5야", "기본 모델은 fable-5-2야"),
    ("회의 시간은 오후 2시야", "회의 시간은 오후 4시야"),
    ("포트는 8080을 써", "포트는 9090을 써"),
    ("the deploy target is staging", "the deploy target is production"),
    ("dave joined google", "dave left google"),
    ("role: ceo", "role: cto"),
]


@pytest.mark.parametrize("old,new", PARAPHRASES)
def test_paraphrase_is_not_conflict(old, new):
    assert is_opposing(old.lower(), new.lower()) is False


@pytest.mark.parametrize("old,new", VALUE_CHANGES)
def test_value_change_is_conflict(old, new):
    assert is_opposing(old.lower(), new.lower()) is True
