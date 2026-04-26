"""Tests for v2.1 MultimodalMixin — attach / attachments / detach / search_multimodal."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from memkraft import MemKraft


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture()
def mk(tmp_path):
    """Fresh MemKraft instance per test."""
    inst = MemKraft(base_dir=str(tmp_path / "memory"))
    inst.init(verbose=False)
    return inst


@pytest.fixture()
def text_file(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text(
        "Hashed Vibe Labs hosts the bridgehead protocol. "
        "Simon plans to ship VibeKai next quarter. "
        "The sandbox demo runs on rust + wasm.",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def code_file(tmp_path):
    p = tmp_path / "agent.py"
    p.write_text(
        "def heartbeat():\n"
        "    # ping the supervisor every 30s\n"
        "    return 'alive'\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def fake_image(tmp_path):
    """A non-text file we'll pretend is an image."""
    p = tmp_path / "screenshot.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return p


# ----------------------------------------------------------------------
# attach — text/code direct read
# ----------------------------------------------------------------------
def test_attach_text_file_creates_record(mk, text_file):
    rec = mk.attach("HVL", str(text_file))
    assert rec["entity"] == "HVL"
    assert rec["modality"] == "text"
    assert rec["chunks"] >= 1
    assert Path(rec["stored_path"]).exists()
    assert rec["doc_id"].startswith("att__")


def test_attach_code_file_modality_detected_as_code(mk, code_file):
    rec = mk.attach("agent-runtime", str(code_file))
    assert rec["modality"] == "code"
    assert "heartbeat" in Path(rec["stored_path"]).read_text(encoding="utf-8")


def test_attach_writes_metadata_json(mk, text_file):
    mk.attach("HVL", str(text_file))
    meta_path = mk.base_dir / "attachments" / mk._slugify("HVL") / ".metadata.json"
    assert meta_path.exists()
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["modality"] == "text"


def test_attach_creates_entity_if_missing(mk, text_file):
    slug = mk._slugify("BrandNewEntity")
    live_path = mk.live_notes_dir / f"{slug}.md"
    assert not live_path.exists()
    mk.attach("BrandNewEntity", str(text_file))
    assert live_path.exists()


def test_attach_image_requires_transcribe_fn(mk, fake_image):
    with pytest.raises(ValueError, match="requires transcribe_fn"):
        mk.attach("Simon", str(fake_image))


def test_attach_image_with_transcribe_fn(mk, fake_image):
    captured = {}

    def fake_ocr(path: str) -> str:
        captured["called_with"] = path
        return "screenshot caption: deploy dashboard showing 99.7% uptime"

    rec = mk.attach("Simon", str(fake_image), transcribe_fn=fake_ocr)
    assert captured["called_with"].endswith("screenshot.png")
    assert rec["modality"] == "image"
    stored = Path(rec["stored_path"]).read_text(encoding="utf-8")
    assert "deploy dashboard" in stored


def test_attach_explicit_modality_override(mk, text_file):
    rec = mk.attach("Simon", str(text_file), modality="code")
    assert rec["modality"] == "code"


def test_attach_missing_file_raises(mk):
    with pytest.raises(FileNotFoundError):
        mk.attach("Simon", "/no/such/file.txt")


def test_attach_empty_entity_name_raises(mk, text_file):
    with pytest.raises(ValueError):
        mk.attach("", str(text_file))


def test_attach_empty_text_refused(mk, tmp_path):
    empty = tmp_path / "blank.txt"
    empty.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        mk.attach("Simon", str(empty))


def test_attach_transcribe_fn_must_return_string(mk, fake_image):
    def bad(_p):  # returns non-string
        return 42

    with pytest.raises(TypeError):
        mk.attach("Simon", str(fake_image), transcribe_fn=bad)


# ----------------------------------------------------------------------
# attachments
# ----------------------------------------------------------------------
def test_attachments_returns_records_in_order(mk, text_file, code_file):
    mk.attach("HVL", str(text_file))
    mk.attach("HVL", str(code_file))
    recs = mk.attachments("HVL")
    assert len(recs) == 2
    filenames = {r["filename"] for r in recs}
    assert filenames == {"notes.txt", "agent.py"}


def test_attachments_for_unknown_entity_is_empty(mk):
    assert mk.attachments("ghost-entity") == []


def test_attach_same_source_path_replaces_record(mk, text_file):
    mk.attach("HVL", str(text_file))
    mk.attach("HVL", str(text_file))  # same source → replace, not duplicate
    recs = mk.attachments("HVL")
    assert len(recs) == 1


# ----------------------------------------------------------------------
# detach
# ----------------------------------------------------------------------
def test_detach_removes_record_and_transcript(mk, text_file):
    rec = mk.attach("HVL", str(text_file))
    stored = Path(rec["stored_path"])
    assert stored.exists()

    ok = mk.detach("HVL", str(text_file))
    assert ok is True
    assert not stored.exists()
    assert mk.attachments("HVL") == []


def test_detach_unknown_file_returns_false(mk, text_file):
    mk.attach("HVL", str(text_file))
    assert mk.detach("HVL", "/no/such/file.txt") is False


def test_detach_unknown_entity_returns_false(mk):
    assert mk.detach("ghost", "/whatever.txt") is False


# ----------------------------------------------------------------------
# search_multimodal
# ----------------------------------------------------------------------
def test_search_multimodal_finds_attached_text(mk, text_file):
    mk.attach("HVL", str(text_file))
    hits = mk.search_multimodal("bridgehead protocol", top_k=5)
    assert isinstance(hits, list)
    assert len(hits) >= 1
    assert hits[0]["entity"] == "HVL"
    assert hits[0]["modality"] == "text"


def test_search_multimodal_modality_filter(mk, text_file, fake_image):
    mk.attach("HVL", str(text_file))

    def ocr(_p):
        return "screenshot caption: bridgehead deployment status"

    mk.attach("Simon", str(fake_image), transcribe_fn=ocr)

    text_only = mk.search_multimodal("bridgehead", modality="text", top_k=10)
    image_only = mk.search_multimodal("bridgehead", modality="image", top_k=10)
    assert all(h["modality"] == "text" for h in text_only)
    assert all(h["modality"] == "image" for h in image_only)
    # Both should have at least one hit
    assert text_only and image_only


def test_search_multimodal_empty_query_returns_empty(mk, text_file):
    mk.attach("HVL", str(text_file))
    assert mk.search_multimodal("", top_k=5) == []
    assert mk.search_multimodal("   ", top_k=5) == []


def test_search_multimodal_no_attachments_returns_empty(mk):
    assert mk.search_multimodal("anything", top_k=5) == []


def test_search_multimodal_respects_top_k(mk, text_file, code_file, tmp_path):
    # Multiple attachments, each chunked → many candidate hits
    mk.attach("HVL", str(text_file))
    mk.attach("agent-runtime", str(code_file))

    extra = tmp_path / "more.txt"
    extra.write_text("bridgehead bridgehead bridgehead more notes", encoding="utf-8")
    mk.attach("HVL", str(extra))

    hits = mk.search_multimodal("bridgehead", top_k=2)
    assert len(hits) <= 2
