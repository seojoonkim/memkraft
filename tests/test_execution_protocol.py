"""MKCJSON/1 canonicalization, digest, and time-profile conformance (plan §5).

Covers conformance cases CJ-01…CJ-06 and TM-01…TM-03. The canonical byte
strings below are hand-written literals, not recordings of what the
implementation happens to emit: a second-language runtime (§18.3) must
reproduce exactly these bytes, so they are the specification, not a snapshot.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

import pytest

from memkraft.execution_protocol import (
    ExecutionError,
    canonical_timestamp,
    digest,
    mkcjson,
    parse_timestamp,
)

# --------------------------------------------------------------------------
# CJ-03: the 20 pinned canonicalization vectors.
# --------------------------------------------------------------------------

VECTORS = [
    ("empty_object", {}, b"{}"),
    ("single_key", {"a": 1}, b'{"a":1}'),
    ("key_order", {"b": 1, "a": 2}, b'{"a":2,"b":1}'),
    ("key_order_underscored", {"z_1": 1, "a_9": 2}, b'{"a_9":2,"z_1":1}'),
    ("key_order_digit_before_underscore", {"a_b": 1, "a9": 2}, b'{"a9":2,"a_b":1}'),
    ("negative_integer", {"n": -42}, b'{"n":-42}'),
    ("zero", {"n": 0}, b'{"n":0}'),
    ("max_safe_integer", {"n": 9007199254740991}, b'{"n":9007199254740991}'),
    ("min_safe_integer", {"n": -9007199254740991}, b'{"n":-9007199254740991}'),
    ("booleans_and_null", {"f": False, "n": None, "t": True}, b'{"f":false,"n":null,"t":true}'),
    ("empty_array", {"a": []}, b'{"a":[]}'),
    ("array_order_preserved", {"a": [3, 1, 2]}, b'{"a":[3,1,2]}'),
    ("nested_object", {"a": {"c": 1, "b": 2}}, b'{"a":{"b":2,"c":1}}'),
    ("array_of_objects", {"a": [{"y": 1, "x": 2}]}, b'{"a":[{"x":2,"y":1}]}'),
    ("empty_string", {"s": ""}, b'{"s":""}'),
    ("quote_and_backslash", {"s": '"\\'}, b'{"s":"\\"\\\\"}'),
    ("short_form_escapes", {"s": "\b\f\n\r\t"}, b'{"s":"\\b\\f\\n\\r\\t"}'),
    ("other_control_chars_lowercase_hex", {"s": "\x00\x01\x1f"}, b'{"s":"\\u0000\\u0001\\u001f"}'),
    ("cjk_literal_utf8", {"s": "지식"}, '{"s":"지식"}'.encode("utf-8")),
    ("emoji_literal_utf8", {"s": "\U0001f600"}, '{"s":"\U0001f600"}'.encode("utf-8")),
]

assert len(VECTORS) == 20, "CJ-03 pins exactly 20 vectors"


@pytest.mark.parametrize("name,obj,expected", VECTORS, ids=[v[0] for v in VECTORS])
def test_cj03_canonical_bytes_match_pinned_vectors(name, obj, expected):
    assert mkcjson(obj) == expected


@pytest.mark.parametrize("name,obj,expected", VECTORS, ids=[v[0] for v in VECTORS])
def test_cj03_digest_matches_sha256_of_pinned_bytes(name, obj, expected):
    assert digest(obj) == hashlib.sha256(expected).hexdigest()


def test_digest_is_lowercase_hex_of_length_64():
    value = digest({"a": 1})
    assert len(value) == 64
    assert value == value.lower()
    assert all(char in "0123456789abcdef" for char in value)


# --------------------------------------------------------------------------
# CJ-01: key order and the ASCII-only key grammar.
# --------------------------------------------------------------------------


def test_cj01_keys_sort_ascending_by_byte_value():
    assert mkcjson({"b": 1, "a": 2, "a0": 3}) == b'{"a":2,"a0":3,"b":1}'


@pytest.mark.parametrize(
    "key",
    [
        "지",   # non-ASCII
        "café",
        "_z",       # leading underscore: rule 3 requires a leading lowercase letter
        "A",        # uppercase
        "9a",       # leading digit
        "a-b",      # hyphen
        "a b",      # space
        "",
        "a" * 65,   # over the 64-character limit
    ],
)
def test_cj01_keys_outside_the_grammar_are_e_pattern(key):
    with pytest.raises(ExecutionError) as info:
        mkcjson({key: 1})
    assert info.value.code == "E_PATTERN"


def test_top_level_must_be_an_object():
    with pytest.raises(ExecutionError) as info:
        mkcjson([1, 2])
    assert info.value.code == "E_TYPE"


# --------------------------------------------------------------------------
# CJ-02: integers only.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1.0, 1e3, -0.0, 0.5, float("inf"), float("nan")])
def test_cj02_floats_are_e_type(value):
    with pytest.raises(ExecutionError) as info:
        mkcjson({"n": value})
    assert info.value.code == "E_TYPE"


def test_cj02_bool_is_not_an_integer():
    """``True`` must serialize as a boolean, never as ``1``."""
    assert mkcjson({"n": True}) == b'{"n":true}'


@pytest.mark.parametrize("value", [2 ** 53, -(2 ** 53), 2 ** 64])
def test_cj02_integers_outside_the_safe_range_are_e_limit_exceeded(value):
    with pytest.raises(ExecutionError) as info:
        mkcjson({"n": value})
    assert info.value.code == "E_LIMIT_EXCEEDED"


def test_cj02_safe_range_boundaries_are_accepted():
    assert mkcjson({"n": 2 ** 53 - 1}) == b'{"n":9007199254740991}'
    assert mkcjson({"n": -(2 ** 53 - 1)}) == b'{"n":-9007199254740991}'


@pytest.mark.parametrize("value", [(1, 2), {1, 2}, object(), b"bytes"])
def test_unsupported_types_are_e_type(value):
    with pytest.raises(ExecutionError) as info:
        mkcjson({"n": value})
    assert info.value.code == "E_TYPE"


# --------------------------------------------------------------------------
# CJ-04: lone surrogates.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["\ud800", "a\udfffb", "\ud800\ud800"])
def test_cj04_lone_surrogate_is_e_type(text):
    with pytest.raises(ExecutionError) as info:
        mkcjson({"s": text})
    assert info.value.code == "E_TYPE"


def test_cj04_astral_pair_is_not_a_lone_surrogate():
    """U+1F600 is a single scalar value in Python and must be accepted."""
    assert mkcjson({"s": "\U0001f600"}) == '{"s":"\U0001f600"}'.encode("utf-8")


# --------------------------------------------------------------------------
# CJ-05: NFC normalization.
# --------------------------------------------------------------------------


def test_cj05_nfd_input_is_normalized_to_nfc():
    nfd = unicodedata.normalize("NFD", "café")
    nfc = unicodedata.normalize("NFC", "café")
    assert nfd != nfc
    assert mkcjson({"s": nfd}) == mkcjson({"s": nfc})
    assert mkcjson({"s": nfd}) == ('{"s":"%s"}' % nfc).encode("utf-8")


def test_cj05_digest_of_nfd_matches_the_nfc_golden():
    nfd = unicodedata.normalize("NFD", "지식")
    nfc = unicodedata.normalize("NFC", "지식")
    assert digest({"s": nfd}) == digest({"s": nfc})


# --------------------------------------------------------------------------
# CJ-06: the digest is over the canonical form, never the stored file line.
# --------------------------------------------------------------------------


def test_cj06_digest_is_not_sha256_of_the_store_file_line():
    """``store_core.append`` writes without ``sort_keys``, so its bytes differ."""
    record = {"b": 1, "a": 2}
    file_line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    assert file_line == '{"b":1,"a":2}'
    assert digest(record) != hashlib.sha256(file_line.encode("utf-8")).hexdigest()
    assert digest(record) == hashlib.sha256(b'{"a":2,"b":1}').hexdigest()


# --------------------------------------------------------------------------
# Structural limits (rule 9).
# --------------------------------------------------------------------------


def test_depth_limit_is_eight():
    def nest(levels):
        obj = {"a": 1}
        for _ in range(levels - 1):
            obj = {"a": obj}
        return obj

    mkcjson(nest(8))
    with pytest.raises(ExecutionError) as info:
        mkcjson(nest(9))
    assert info.value.code == "E_LIMIT_EXCEEDED"


def test_arrays_count_toward_depth():
    with pytest.raises(ExecutionError) as info:
        mkcjson({"a": [[[[[[[[1]]]]]]]]})
    assert info.value.code == "E_LIMIT_EXCEEDED"


def test_keys_per_object_limit_is_sixty_four():
    mkcjson({"k%d" % i: i for i in range(64)})
    with pytest.raises(ExecutionError) as info:
        mkcjson({"k%d" % i: i for i in range(65)})
    assert info.value.code == "E_LIMIT_EXCEEDED"


def test_array_length_limit_is_thirty_two():
    mkcjson({"a": list(range(32))})
    with pytest.raises(ExecutionError) as info:
        mkcjson({"a": list(range(33))})
    assert info.value.code == "E_LIMIT_EXCEEDED"


def test_canonical_form_has_no_trailing_newline_or_bom():
    raw = mkcjson({"a": 1})
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert not raw.endswith(b"\n")


# --------------------------------------------------------------------------
# TM-01…TM-03: the time profile.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["2026-08-04T11:22:33", "2026-08-04T11:22:33.123456", "2026-08-04T11:22:33.1"],
)
def test_tm01_naive_timestamp_is_e_time_naive(text):
    with pytest.raises(ExecutionError) as info:
        canonical_timestamp(text)
    assert info.value.code == "E_TIME_NAIVE"


def test_tm01_naive_datetime_object_is_e_time_naive():
    import datetime as _dt

    with pytest.raises(ExecutionError) as info:
        canonical_timestamp(_dt.datetime(2026, 8, 4, 11, 22, 33))
    assert info.value.code == "E_TIME_NAIVE"


def test_tm02_offsets_normalize_to_the_same_utc_instant():
    a = canonical_timestamp("2026-08-04T20:22:33+09:00")
    b = canonical_timestamp("2026-08-04T11:22:33Z")
    c = canonical_timestamp("2026-08-04T06:22:33-05:00")
    assert a == b == c == "2026-08-04T11:22:33Z"


def test_tm02_identical_instants_produce_identical_digests():
    east = {"emitted_at": canonical_timestamp("2026-08-04T20:22:33+09:00")}
    utc = {"emitted_at": canonical_timestamp("2026-08-04T11:22:33Z")}
    assert digest(east) == digest(utc)


def test_tm03_z_suffix_is_accepted_on_python39():
    """``datetime.fromisoformat`` rejects ``Z`` on 3.9; the profile must not."""
    parsed = parse_timestamp("2026-08-04T11:22:33Z")
    assert parsed.utcoffset().total_seconds() == 0
    assert canonical_timestamp("2026-08-04T11:22:33Z") == "2026-08-04T11:22:33Z"


def test_tm03_lowercase_z_is_rejected():
    with pytest.raises(ExecutionError) as info:
        canonical_timestamp("2026-08-04T11:22:33z")
    assert info.value.code == "E_TIME_FORMAT"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026-08-04T11:22:33.9Z", "2026-08-04T11:22:33Z"),
        ("2026-08-04T11:22:33.999999Z", "2026-08-04T11:22:33Z"),
        ("2026-08-04T11:22:33.5+00:00", "2026-08-04T11:22:33Z"),
    ],
)
def test_fractional_seconds_are_truncated_not_rounded(text, expected):
    assert canonical_timestamp(text) == expected


@pytest.mark.parametrize("digits", [1, 2, 3, 4, 5, 6])
def test_one_to_six_fractional_digits_are_accepted(digits):
    text = "2026-08-04T11:22:33.%sZ" % ("1" * digits)
    assert canonical_timestamp(text) == "2026-08-04T11:22:33Z"


@pytest.mark.parametrize(
    ("text", "microseconds"),
    [
        ("2026-08-04T11:22:33.5Z", 500000),
        ("2026-08-04T11:22:33.05Z", 50000),
        ("2026-08-04T11:22:33.123Z", 123000),
        ("2026-08-04T11:22:33.000001Z", 1),
    ],
)
def test_fraction_normalization_preserves_magnitude(text, microseconds):
    assert parse_timestamp(text).microsecond == microseconds


@pytest.mark.parametrize(
    "text",
    [
        "2026-08-04T11:22:60Z",          # leap second
        "2026-08-04T11:22:33.1234567Z",  # seven fractional digits
        "2026-08-04T11:22:33.Z",
        "2026-08-04T11:22:33+0900",      # offset needs a colon
        "2026-08-04T11:22Z",             # seconds are mandatory
        "2026-08-04 11:22:33",           # the date/time separator must be T
        "2026-08-04",
        "26-08-04T11:22:33Z",
        "2026-13-04T11:22:33Z",
        "not a timestamp",
        "",
    ],
)
def test_malformed_timestamps_are_e_time_format(text):
    with pytest.raises(ExecutionError) as info:
        canonical_timestamp(text)
    assert info.value.code == "E_TIME_FORMAT"


@pytest.mark.parametrize("text", ["1969-12-31T23:59:59Z", "10000-01-01T00:00:00Z"])
def test_years_outside_1970_9999_are_e_time_format(text):
    with pytest.raises(ExecutionError) as info:
        canonical_timestamp(text)
    assert info.value.code == "E_TIME_FORMAT"


@pytest.mark.parametrize("text", ["1970-01-01T00:00:00Z", "9999-12-31T23:59:59Z"])
def test_year_boundaries_are_accepted(text):
    assert canonical_timestamp(text) == text


def test_non_string_timestamp_is_e_type():
    with pytest.raises(ExecutionError) as info:
        canonical_timestamp(12345)
    assert info.value.code == "E_TYPE"


def test_canonical_timestamp_accepts_an_aware_datetime():
    import datetime as _dt

    value = _dt.datetime(2026, 8, 4, 20, 22, 33, 500000,
                         tzinfo=_dt.timezone(_dt.timedelta(hours=9)))
    assert canonical_timestamp(value) == "2026-08-04T11:22:33Z"


def test_canonical_output_round_trips():
    assert canonical_timestamp(canonical_timestamp("2026-08-04T20:22:33+09:00")) == (
        "2026-08-04T11:22:33Z"
    )


# --------------------------------------------------------------------------
# Error surface (§6.7, §11.3).
# --------------------------------------------------------------------------


def test_execution_error_is_a_value_error_with_the_registry_fields():
    with pytest.raises(ValueError) as info:
        mkcjson({"n": 1.5})
    error = info.value
    assert isinstance(error, ExecutionError)
    assert error.code == "E_TYPE"
    assert error.error_class == "input"
    assert error.retryable is False
    assert isinstance(error.details, dict)


def test_error_details_name_the_offending_path():
    with pytest.raises(ExecutionError) as info:
        mkcjson({"a": {"b": [1, 2.5]}})
    assert info.value.details.get("path") == "a.b[1]"
