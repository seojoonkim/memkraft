"""v2.1 Multimodal Memory — additive, non-breaking.

Lets MemKraft attach image / audio / code files to entities by funneling
*pre-transcribed text* through the existing chunking + search pipeline.
External tools (OCR, ASR, captioners) handle the heavy lifting; MemKraft
itself stays zero-dependency.

Design:
    * Caller passes either:
        - a text/code file (read directly), OR
        - any binary file + a ``transcribe_fn`` callback that returns text.
    * Transcribed text is written under
      ``{base_dir}/attachments/{entity-slug}/{filename}.txt``.
    * The text is then ingested via ``self.track_document`` so all
      existing search APIs (``search``, ``search_precise``) pick it up.
    * Per-entity ``.metadata.json`` records every attachment with its
      modality, source path, doc_id, and chunk count.

Honoured constraints:
    * No external lib imports (PIL / easyocr / etc. are off-limits).
    * Does not modify ``core.py`` — installed as a mixin from
      ``__init__.py``.
    * Existing API signatures unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ----------------------------------------------------------------------
# Modality detection by file extension
# ----------------------------------------------------------------------
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic", ".svg"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java", ".c",
    ".cc", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".zsh", ".lua", ".pl", ".r", ".sql", ".html", ".css",
    ".scss", ".vue", ".svelte", ".dart", ".ex", ".exs", ".clj", ".hs",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".ini", ".cfg",
}
_TEXT_EXTS = {".txt", ".md", ".rst", ".log", ".csv", ".tsv"}


def _detect_modality(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _CODE_EXTS:
        return "code"
    if ext in _TEXT_EXTS:
        return "text"
    return "binary"


def _is_text_like(modality: str) -> bool:
    """Modalities whose source file can be read as text directly."""
    return modality in ("text", "code")


def _safe_slug(text: str) -> str:
    """Local slugify — keeps multimodal independent of core internals.

    Falls back to a simple ASCII-friendly form if ``self._slugify`` is
    unavailable for some reason.
    """
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9가-힣\-_ ]+", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return text or "untitled"


class MultimodalMixin:
    """v2.1 Multimodal attachment API."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _attachments_root(self) -> Path:
        root = self.base_dir / "attachments"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _attachment_dir(self, entity_name: str) -> Path:
        slug = self._slugify(entity_name) if hasattr(self, "_slugify") else _safe_slug(entity_name)
        d = self._attachments_root() / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _attachment_meta_path(self, entity_name: str) -> Path:
        return self._attachment_dir(entity_name) / ".metadata.json"

    def _read_attachment_meta(self, entity_name: str) -> List[Dict[str, Any]]:
        path = self._attachment_meta_path(entity_name)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _write_attachment_meta(self, entity_name: str, records: List[Dict[str, Any]]) -> None:
        path = self._attachment_meta_path(entity_name)
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ensure_entity(self, entity_name: str, source: str = "") -> None:
        """Make sure a live-note for the entity exists (best-effort)."""
        slug = self._slugify(entity_name) if hasattr(self, "_slugify") else _safe_slug(entity_name)
        live_dir = getattr(self, "live_notes_dir", self.base_dir / "live-notes")
        live_dir.mkdir(parents=True, exist_ok=True)
        if not (live_dir / f"{slug}.md").exists():
            try:
                self.track(entity_name, entity_type="document", source=source or "multimodal")
            except Exception:
                # Best-effort: attaching should still succeed even if track fails.
                pass

    # ------------------------------------------------------------------
    # attach
    # ------------------------------------------------------------------
    def attach(
        self,
        entity_name: str,
        file_path: str,
        *,
        modality: str = "auto",
        source: str = "",
        transcribe_fn: Optional[Callable[[str], str]] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> Dict[str, Any]:
        """Attach a file to ``entity_name``.

        For text/code files the file is read directly. For other modalities
        a ``transcribe_fn(file_path) -> str`` callback must be supplied;
        MemKraft never imports a transcoder itself.

        The transcribed text is saved under
        ``{base_dir}/attachments/{entity-slug}/{filename}.txt`` and
        ingested via ``track_document`` so existing search APIs pick it up.

        Returns:
            dict: ``{"entity", "modality", "source_path", "stored_path",
            "doc_id", "chunks", "attached_at"}``.
        """
        if not entity_name or not str(entity_name).strip():
            raise ValueError("entity_name must be a non-empty string")
        if not file_path:
            raise ValueError("file_path must be a non-empty string")

        src = Path(file_path).expanduser()
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"file_path not found: {src}")

        # 1. Detect modality
        actual_modality = _detect_modality(src) if modality in (None, "", "auto") else modality

        # 2. Extract text
        if transcribe_fn is not None:
            try:
                text = transcribe_fn(str(src))
            except Exception as exc:  # surface the cause to the caller
                raise RuntimeError(f"transcribe_fn failed: {exc}") from exc
            if not isinstance(text, str):
                raise TypeError("transcribe_fn must return str")
        elif _is_text_like(actual_modality):
            try:
                text = src.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise RuntimeError(f"failed to read text file: {exc}") from exc
        else:
            raise ValueError(
                f"modality='{actual_modality}' requires transcribe_fn (no text extraction available)"
            )

        text = (text or "").strip()
        if not text:
            raise ValueError("extracted text is empty — refusing to index empty attachment")

        # 3. Persist transcript
        att_dir = self._attachment_dir(entity_name)
        stored = att_dir / f"{src.name}.txt"
        stored.write_text(text, encoding="utf-8")

        # 4. Ensure entity exists, then ingest as a chunked document
        self._ensure_entity(entity_name, source=source)
        entity_slug = self._slugify(entity_name) if hasattr(self, "_slugify") else _safe_slug(entity_name)
        # doc_id is namespaced so chunks don't collide with normal entities.
        safe_filename = _safe_slug(src.stem) or "file"
        doc_id = f"att__{entity_slug}__{safe_filename}"

        try:
            chunks = self.track_document(
                doc_id,
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                entity_type=f"attachment:{actual_modality}",
                source=source or f"attachment:{actual_modality}",
            )
        except Exception as exc:
            # Roll back the stored transcript so we don't leave orphans.
            try:
                stored.unlink()
            except OSError:
                pass
            raise RuntimeError(f"track_document failed: {exc}") from exc

        # 5. Update metadata
        record = {
            "entity": entity_name,
            "entity_slug": entity_slug,
            "modality": actual_modality,
            "source_path": str(src.resolve()),
            "stored_path": str(stored),
            "filename": src.name,
            "doc_id": doc_id,
            "chunks": int(chunks),
            "source": source or "",
            "attached_at": datetime.now().isoformat(timespec="seconds"),
        }
        records = self._read_attachment_meta(entity_name)
        # Replace if same source_path already attached
        records = [r for r in records if r.get("source_path") != record["source_path"]]
        records.append(record)
        self._write_attachment_meta(entity_name, records)

        return record

    # ------------------------------------------------------------------
    # attachments
    # ------------------------------------------------------------------
    def attachments(self, entity_name: str) -> List[Dict[str, Any]]:
        """Return the attachment records for ``entity_name`` (newest last)."""
        if not entity_name:
            return []
        return list(self._read_attachment_meta(entity_name))

    # ------------------------------------------------------------------
    # detach
    # ------------------------------------------------------------------
    def detach(self, entity_name: str, file_path: str) -> bool:
        """Remove a previously attached file.

        Matches on ``source_path`` (resolved) first, then on filename or
        ``stored_path``. Removes the stored transcript and the chunk
        entities created during ``attach``. Returns True if anything was
        removed.
        """
        if not entity_name or not file_path:
            return False
        records = self._read_attachment_meta(entity_name)
        if not records:
            return False

        target = Path(file_path).expanduser()
        try:
            target_resolved = str(target.resolve())
        except OSError:
            target_resolved = str(target)
        target_name = target.name

        kept: List[Dict[str, Any]] = []
        removed_any = False
        for rec in records:
            match = (
                rec.get("source_path") == target_resolved
                or rec.get("source_path") == str(target)
                or rec.get("stored_path") == str(target)
                or rec.get("filename") == target_name
            )
            if not match:
                kept.append(rec)
                continue
            removed_any = True

            # Remove transcript file
            stored = Path(rec.get("stored_path", ""))
            if stored.exists():
                try:
                    stored.unlink()
                except OSError:
                    pass

            # Remove chunk entities + parent doc note (best-effort)
            doc_id = rec.get("doc_id", "")
            chunks = int(rec.get("chunks", 0) or 0)
            live_dir = getattr(self, "live_notes_dir", self.base_dir / "live-notes")
            if doc_id:
                slug_fn = self._slugify if hasattr(self, "_slugify") else _safe_slug
                # parent doc
                parent = live_dir / f"{slug_fn(doc_id)}.md"
                if parent.exists():
                    try:
                        parent.unlink()
                    except OSError:
                        pass
                # chunks
                for i in range(max(chunks + 5, 50)):  # over-scan a few extra
                    chunk_file = live_dir / f"{slug_fn(f'{doc_id}__c{i}')}.md"
                    if chunk_file.exists():
                        try:
                            chunk_file.unlink()
                        except OSError:
                            pass
                    elif i >= chunks:
                        # Past the recorded count and nothing to delete — stop scanning.
                        break

        self._write_attachment_meta(entity_name, kept)
        return removed_any

    # ------------------------------------------------------------------
    # search_multimodal
    # ------------------------------------------------------------------
    def search_multimodal(
        self,
        query: str,
        modality: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search across attached transcripts.

        Wraps ``self.search`` (or ``search_precise`` when available) and
        keeps only hits that originate from attachment-tracked chunks.
        Each result is enriched with the matching attachment metadata.

        Args:
            query: free-text query.
            modality: optional filter ("image", "audio", "code", "text",
                "video", "binary").
            top_k: cap on returned results.

        Returns:
            list[dict]: search hits annotated with ``modality``,
            ``entity``, ``source_path``, ``stored_path``, ``doc_id``.
        """
        if not query or not str(query).strip() or top_k <= 0:
            return []

        # Build (doc_id_prefix -> attachment record) lookup across all entities
        att_root = self.base_dir / "attachments"
        index: Dict[str, Dict[str, Any]] = {}
        if att_root.exists():
            for ent_dir in att_root.iterdir():
                if not ent_dir.is_dir():
                    continue
                meta = ent_dir / ".metadata.json"
                if not meta.exists():
                    continue
                try:
                    records = json.loads(meta.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if not isinstance(records, list):
                    continue
                for rec in records:
                    doc_id = rec.get("doc_id")
                    if doc_id:
                        index[doc_id] = rec

        if not index:
            return []

        # Run the underlying search (precision-first if available)
        try:
            if hasattr(self, "search_precise"):
                hits = self.search_precise(query, top_k=max(top_k * 4, 20)) or []
            else:
                hits = self.search(query, fuzzy=False) or []
                if not hits:
                    hits = self.search(query, fuzzy=True) or []
        except Exception:
            hits = []

        annotated: List[Dict[str, Any]] = []
        for hit in hits:
            # Each hit has a path or name we can map back to a doc_id.
            candidate_strings = []
            for key in ("path", "file", "name", "entity", "id"):
                val = hit.get(key) if isinstance(hit, dict) else None
                if val:
                    candidate_strings.append(str(val))
            joined = " ".join(candidate_strings)

            matched_rec: Optional[Dict[str, Any]] = None
            for doc_id, rec in index.items():
                if doc_id in joined:
                    matched_rec = rec
                    break
            if matched_rec is None:
                continue
            if modality and matched_rec.get("modality") != modality:
                continue

            enriched = dict(hit) if isinstance(hit, dict) else {"raw": hit}
            enriched.update(
                {
                    "modality": matched_rec.get("modality"),
                    "entity": matched_rec.get("entity"),
                    "source_path": matched_rec.get("source_path"),
                    "stored_path": matched_rec.get("stored_path"),
                    "doc_id": matched_rec.get("doc_id"),
                    "filename": matched_rec.get("filename"),
                }
            )
            annotated.append(enriched)
            if len(annotated) >= top_k:
                break

        return annotated
