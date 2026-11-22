"""Lesson recall ignores the notes a chat host wraps around the user's words.

Real Sano replay (303 owner turns, 2026-11): 44 of 143 auto-captured lessons
were injected only because the turn and the stored task shared host boilerplate
such as "[The user sent an image but something went wrong ...]", a speaker tag
"[Simon Kim (김서준) | Slack user <@U..>]", or a cron preamble.
"""
from memkraft import MemKraft
from memkraft.reasoning_bank import _strip_quoted_context


def _auto(mk, tid, title, lesson):
    tags = ["development-experience", "auto-captured", "success"]
    mk.trajectory_start(tid, title=title, tags=tags)
    mk.trajectory_complete(tid, status="success", lesson=lesson,
                           pattern_signature=f"{tid}::ok", tags=tags)


IMG_FAIL = ("[The user sent an image but something went wrong when I tried to look at it~ "
            "You can try examining it yourself with vision_analyze using image_url: /tmp/a.jpg]")
SLACK = "[Simon Kim (김서준) | Slack user <@U0B02DFPYQJ>]"


def test_host_notes_are_stripped_but_user_words_and_descriptions_stay():
    q = (SLACK + " 레이아웃 깨진 부분 고쳐줘\n\n[Image attached at: /tmp/img_1.jpg]\n" + IMG_FAIL)
    out = _strip_quoted_context(q)
    for word in ("Slack", "vision_analyze", "something", "attached"):
        assert word not in out
    assert "레이아웃" in out
    seen = _strip_quoted_context("[The user sent an image~ Here's what I can see: 지도 화면]\n지도 키워줘")
    assert "지도" in seen and "Here" not in seen
    doc = _strip_quoted_context("[The user sent a document: 'pitch_deck.pdf'. It is saved at: /tmp/x. "
                                "Its text is not inlined here.]\n\n요약해줘")
    assert "pitch_deck" in doc and "inlined" not in doc


def test_shared_boilerplate_alone_does_not_recall_a_lesson(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    _auto(mk, "logo", IMG_FAIL + "\n\n로고 세리프체로 바꾸자", "Reuse terminal:npm for the logo swap.")
    hits = mk.reasoning_recall(SLACK + " 이메일 초안 보내줘\n\n" + IMG_FAIL, top_k=3)
    assert [h["task_id"] for h in hits] == []


def test_same_task_words_still_recall_through_host_notes(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    _auto(mk, "logo", IMG_FAIL + "\n\n로고 세리프체로 바꾸자", "Reuse terminal:npm for the logo swap.")
    hits = mk.reasoning_recall(SLACK + " 로고 세리프체 다시 바꾸자", top_k=3)
    assert [h["task_id"] for h in hits] == ["logo"]
