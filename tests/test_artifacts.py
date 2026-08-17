import json

from memkraft import MemKraft


TITLE = "기억이 책임의 장부가 되기까지: MemKraft v3.0에서 v3.5까지"


def test_artifact_search_prefers_scoped_direct_original_with_provenance(tmp_path):
    store = MemKraft(base_dir=str(tmp_path / "memory"))
    store.init(verbose=False)
    store.persist_artifact(
        "원문 본문: {}\n직접 작성한 고유 문단".format(TITLE),
        provenance={
            "session_id": "session-original", "parent_session_id": "session-parent",
            "lineage_key": "article-lineage", "platform": "telegram", "chat_id": "chat-42",
            "thread_id": "thread-7", "account_id": "account-a", "profile": "writer-profile",
            "agent_identity": "writer-agent", "user_platform_message_id": "user-100",
            "assistant_platform_message_id": "assistant-101", "timestamp": "2026-08-16T10:00:00Z",
            "origin_kind": "original",
        },
    )
    store.persist_artifact(
        "압축본: {}\n나중에 생성된 요약".format(TITLE),
        provenance={
            "session_id": "session-compacted", "lineage_key": "article-lineage",
            "platform": "telegram", "chat_id": "chat-42", "profile": "compactor-profile",
            "agent_identity": "compactor-agent", "timestamp": "2026-08-17T10:00:00Z",
            "origin_kind": "compaction",
        },
    )

    results = store.search_artifacts(
        TITLE, exact_phrase=True,
        provenance={"platform": "telegram", "chat_id": "chat-42", "lineage_key": "article-lineage"},
        order="newest", prefer_direct=True,
    )

    assert results[0]["content"].startswith("원문 본문")
    assert results[0]["provenance"]["session_id"] == "session-original"
    assert results[0]["provenance"]["profile"] == "writer-profile"
    assert results[0]["provenance"]["agent_identity"] == "writer-agent"
    assert results[0]["source_handle"].startswith("artifact:")


def test_artifact_search_supports_exact_phrase_order_and_scope(tmp_path):
    store = MemKraft(base_dir=str(tmp_path / "memory"))
    store.init(verbose=False)
    store.persist_artifact("Alpha Exact Title\nold", provenance={"session_id": "s", "timestamp": "2025-01-01T00:00:00Z", "origin_kind": "direct"})
    store.persist_artifact("Alpha related generic note", provenance={"session_id": "s", "timestamp": "2026-01-01T00:00:00Z", "origin_kind": "direct"})
    store.persist_artifact("Alpha Exact Title\nnew", provenance={"session_id": "other", "timestamp": "2027-01-01T00:00:00Z", "origin_kind": "direct"})

    scoped = store.search_artifacts("Alpha Exact Title", exact_phrase=True, provenance={"session_id": "s"}, order="oldest")
    assert [item["content"] for item in scoped] == ["Alpha Exact Title\nold"]

    ranked = store.search_artifacts("Alpha Exact Title", exact_phrase=False)
    assert ranked[0]["content"].startswith("Alpha Exact Title")
    assert ranked[0]["score"] > ranked[-1]["score"]
    assert len({item["score"] for item in ranked}) > 1


def test_new_artifact_is_also_visible_to_ordinary_search(tmp_path):
    store = MemKraft(base_dir=str(tmp_path / "memory"))
    store.init(verbose=False)
    store.persist_artifact("OrdinarySearchUniqueToken is retained.", provenance={"session_id": "ordinary", "origin_kind": "direct"})

    results = store.search("OrdinarySearchUniqueToken", cache=False)
    assert results
    assert "OrdinarySearchUniqueToken" in results[0]["snippet"]


def test_missing_provenance_is_not_fabricated_or_ranked_as_direct(tmp_path):
    store = MemKraft(base_dir=str(tmp_path / "memory"))
    store.init(verbose=False)
    unknown = store.persist_artifact("Shared Exact Title unknown", provenance={})
    compacted = store.persist_artifact(
        "Shared Exact Title compacted",
        provenance={"origin_kind": "compaction", "timestamp": "2026-01-01T00:00:00Z"},
    )

    assert "timestamp" not in unknown["provenance"]
    assert "origin_kind" not in unknown["provenance"]
    results = store.search_artifacts("Shared Exact Title", prefer_direct=True)
    assert {item["source_handle"] for item in results} == {
        unknown["source_handle"], compacted["source_handle"]
    }


def test_provenance_and_scope_filters_are_combined(tmp_path):
    store = MemKraft(base_dir=str(tmp_path / "memory"))
    store.init(verbose=False)
    store.persist_artifact(
        "Combined Scope Title right",
        provenance={"platform": "telegram", "chat_id": "right"},
    )
    store.persist_artifact(
        "Combined Scope Title wrong",
        provenance={"platform": "telegram", "chat_id": "wrong"},
    )

    results = store.search_artifacts(
        "Combined Scope Title",
        provenance={"platform": "telegram"},
        scope_filters={"chat_id": "right"},
    )
    assert [item["content"] for item in results] == ["Combined Scope Title right"]


def test_timestamp_order_normalizes_iso_offsets(tmp_path):
    store = MemKraft(base_dir=str(tmp_path / "memory"))
    store.init(verbose=False)
    store.persist_artifact(
        "Offset Ordering Title older",
        provenance={"timestamp": "2026-01-01T00:30:00+01:00"},
    )
    store.persist_artifact(
        "Offset Ordering Title newer",
        provenance={"timestamp": "2026-01-01T00:00:00Z"},
    )

    newest = store.search_artifacts("Offset Ordering Title", order="newest")
    oldest = store.search_artifacts("Offset Ordering Title", order="oldest")
    assert newest[0]["content"].endswith("newer")
    assert oldest[0]["content"].endswith("older")


def test_failed_metadata_write_leaves_no_visible_partial_record(tmp_path, monkeypatch):
    store = MemKraft(base_dir=str(tmp_path / "memory"))
    store.init(verbose=False)
    original_dump = json.dump

    def broken_dump(*args, **kwargs):
        raise OSError("simulated metadata write failure")

    monkeypatch.setattr(json, "dump", broken_dump)
    try:
        try:
            store.persist_artifact("Atomic Metadata Title")
        except OSError:
            pass
    finally:
        monkeypatch.setattr(json, "dump", original_dump)

    artifacts = tmp_path / "memory" / "artifacts"
    assert not list(artifacts.glob("*.json"))
    assert not list(artifacts.glob("*.md"))
    assert not list(artifacts.glob("*.tmp"))
    assert store.search_artifacts("Atomic Metadata Title") == []
    assert store.search("Atomic Metadata Title", cache=False) == []
