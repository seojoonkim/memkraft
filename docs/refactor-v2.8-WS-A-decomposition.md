# WS-A: core.py Internal Helper Decomposition

**Branch:** `refactor/v2.8-comprehensive`
**Date:** 2026-05-03
**Baseline:** 4740 LOC / 115 methods / 1288 tests pass

## Decomposition Strategy

Extract private (`_`-prefixed) methods from core.py into 3 helper modules as free functions.
Public methods remain in core.py as thin wrappers calling helpers. No external API changes.

### Module 1: `_core_search_helpers.py` — Search internals
Free functions extracted from search-related private methods.

| Method | LOC | Extraction |
|--------|-----|-----------|
| `_search_tokens` | 3 | ✅ free function `search_tokens(text)` |
| `_bm25_score` | 60 | ✅ free function `bm25_score(...)` |
| `_get_corpus_stats` | 17 | ✅ free function `get_corpus_stats(all_md_files_fn, search_tokens_fn)` |
| `_best_token_snippet` | 16 | ✅ free function `best_token_snippet(query_tokens, lines, lines_orig, search_tokens_fn)` |
| `_first_meaningful_line` | 11 | ✅ free function `first_meaningful_line(content)` |
| `_extract_tags` | 6 | ✅ free function `extract_tags(content)` |
| `_load_stopwords` | 12 | ✅ free function `load_stopwords()` (module-level cache) |
| `_detect_regex` | 130 | ✅ free function `detect_regex(text, strip_korean_josa_fn, load_stopwords_fn)` |
| `_decompose_query` | 24 | ✅ free function `decompose_query(query)` |
| `_goal_weighted_rerank` | 60 | ✅ free function `goal_weighted_rerank(results, context, ...)` |
| `_compute_applicability_bonus` | 30 | ✅ free function `compute_applicability_bonus(content, context, search_tokens_fn)` |
| `_parse_applicability` | 12 | ✅ free function `parse_applicability(text)` |
| `_compute_confidence_bonus` | 14 | ✅ free function `compute_confidence_bonus(content)` |
| `_extract_fact_confidence` | 5 | ✅ free function `extract_fact_confidence(line)` |
| `_file_back_results` | 40 | ✅ free function `file_back_results(base_dir, query, results, safe_read_fn, ...)` |

**Estimated:** ~440 LOC

### Module 2: `_core_detection_helpers.py` — Detection/extraction/conflict
| Method | LOC | Extraction |
|--------|-----|-----------|
| `_is_opposing` | 40 | ✅ free function `is_opposing(old, new)` |
| `_extract_bullet_facts` | 16 | ✅ free function `extract_bullet_facts(content)` |
| `_tag_conflict` | 22 | ✅ free function `tag_conflict(filepath, old_fact, new_fact, source)` |
| `_write_conflicts_report` | 50 | ✅ free function `write_conflicts_report(conflicts, base_dir)` |
| `_extract_facts` | 15 | ✅ free function `extract_facts(text)` |
| `_resolve_extract_input` | 20 | ✅ free function `resolve_extract_input(input_text, safe_read_fn, base_dir)` |
| `_extract_registry_facts` | 15 | ✅ free function `extract_registry_facts(text)` |
| `_write_fact_registry` | 20 | ✅ free function `write_fact_registry(facts, base_dir, source)` |
| `_apply_state_changes` | 34 | ✅ free function `apply_state_changes(content, info)` |
| `_extract_state_candidates` | 18 | ✅ free function `extract_state_candidates(info)` |
| `_is_material_state_change` | 8 | ✅ free function `is_material_state_change(old_value, new_value)` |
| `_append_fact` | 30 | ✅ free function `append_fact(entity_name, fact, ...)` |
| `_classify_content` | 16 | ✅ free function `classify_content(content)` |
| `_route_to_dir` | 4 | ✅ free function `route_to_dir(route, base_dirs)` |
| `_compression_suggestion` | 22 | ✅ free function `compression_suggestion(md, size, safe_read_fn)` |

**Estimated:** ~330 LOC

### Module 3: `_core_lifecycle_helpers.py` — Lifecycle/utility/misc
| Method | LOC | Extraction |
|--------|-----|-----------|
| `_json_load` | 8 | ✅ free function `json_load(filepath)` |
| `_json_save` | 5 | ✅ free function `json_save(filepath, data)` |
| `_slugify` | 5 | ✅ free function `slugify(text)` |
| `_create_entity` | 30 | ✅ free function `create_entity(name, entity_type, source, entities_dir, slugify_fn)` |
| `_strip_korean_josa` | 6 | ✅ free function `strip_korean_josa(name, josa_list)` |
| `_extract_section` | 10 | ✅ free function `extract_section(content, section_name)` |
| `_all_md_files` | 14 | ✅ free function `all_md_files(dirs, base_dir)` |
| `_safe_read` | 7 | ✅ delegates to `_read_cache` (keep as thin wrapper) |
| `_touch_last_accessed` | 30 | ✅ free function `touch_last_accessed(base_dir, rel_path, timestamp)` |
| `_gather_memory_files` | 20 | ✅ free function `gather_memory_files(all_md_files_fn, recent, tag, date)` |
| `_get_version` | 6 | ✅ free function `get_version()` |
| `_file_hash` | 6 | ✅ free function `file_hash(path)` |
| `_get_debug_file` | 6 | ✅ free function `get_debug_file(bug_id, debug_dir)` |
| `_update_debug_status` | 8 | ✅ free function `update_debug_status(content, new_status)` |
| `_append_debug_timeline` | 8 | ✅ free function `append_debug_timeline(content, entry)` |

**Estimated:** ~170 LOC

## NOT Extracted (remain in core.py)

| Method | Reason |
|--------|--------|
| `_safe_read` | Thin wrapper over `_read_cache`, keep in class for `self` consistency |
| All public methods | External API — no signature changes |
| All mixin methods | Out of scope (separate files already) |

## Dependency Graph

Each helper module needs:
- `_regexes` imports (already shared)
- `math`, `json`, `re`, `hashlib`, `datetime` (stdlib)
- No cross-helper-module dependencies
- Functions that need `self` attributes → receive them as explicit parameters

## Expected Outcome

- **core.py:** 4740 → 3940 LOC (-17%)
- **3 helper modules:** 1135 LOC total
- **Public API:** 0 changes (188 public methods preserved)
- **Tests:** 1288 pass / 3 skip
- **Performance:** ~1660ms avg (no regression vs ~1411ms baseline)
