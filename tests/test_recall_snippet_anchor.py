"""Recall snippets must keep the line that actually matched the query.

The old window took three lines *before* the best match and cut the joined
text at 200 chars, so long preceding lines pushed the matching line (the
exact number or identifier the user asked about) out of the snippet even
though the right document ranked first.
"""
from memkraft import MemKraft
from memkraft._core_search_helpers import best_token_snippet, search_tokens


def _snip(query, text):
    lines_orig = text.split("\n")
    return best_token_snippet(
        search_tokens(query), [l.lower() for l in lines_orig], lines_orig, search_tokens
    )


LONG = "- " + "background detail that is not the answer " * 3


def test_matching_line_survives_long_preceding_lines():
    text = "\n".join([LONG, LONG, LONG, "- PR follow-up comment 34465 is merged", "- tail"])
    snippet = _snip("follow-up comment 34465", text)
    assert "34465" in snippet
    assert len(snippet) <= 200


def test_long_matching_line_is_cut_around_the_match():
    line = "- " + "x" * 260 + " port 5433 refused " + "y" * 80
    snippet = _snip("port 5433", "\n".join(["intro", line]))
    assert "5433" in snippet
    assert len(snippet) <= 200


def test_short_context_is_kept_in_document_order():
    text = "alpha line\nbeta has 777 here\ngamma line"
    assert _snip("beta 777", text) == "alpha line | beta has 777 here | gamma line"


def test_search_result_snippet_contains_needle(tmp_path):
    mk = MemKraft(base_dir=str(tmp_path))
    mk.init(verbose=False)
    note = tmp_path / "live-notes" / "turn.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "\n".join(["# turn", LONG, LONG, LONG, "- gateway restarted at 06:03 after watchdog", "- end"]),
        encoding="utf-8",
    )
    hits = mk.search("gateway restarted watchdog 06:03", top_k=3)
    assert hits, "expected the note to be found"
    assert "06:03" in str(hits[0].get("snippet") or "")
