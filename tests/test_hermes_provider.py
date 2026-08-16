import hashlib
import multiprocessing
from unittest import mock

import pytest

pytest.importorskip("agent.memory_provider")

from memkraft.hermes_provider import MemKraftMemoryProvider


@pytest.fixture(autouse=True)
def _isolate_memkraft_dir(monkeypatch):
    monkeypatch.delenv("MEMKRAFT_DIR", raising=False)


def _sync_turn_in_process(home, token, start):
    provider = MemKraftMemoryProvider()
    provider.initialize("shared", hermes_home=home)
    start.wait(timeout=10)
    provider.sync_turn(
        "Concurrent token {} {}".format(token, "x" * 220),
        "Recorded.",
        session_id="shared",
    )


def test_completed_turn_is_persisted_and_recalled_on_next_turn(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    provider = MemKraftMemoryProvider()
    provider.initialize(
        "session-a",
        hermes_home=str(hermes_home),
        platform="cli",
        agent_context="primary",
        agent_identity="test",
    )

    provider.sync_turn(
        "My compatibility codename is QuartzFalcon.",
        "I will remember that.",
        session_id="session-a",
        messages=[
            {"role": "user", "content": "My compatibility codename is QuartzFalcon."},
            {"role": "assistant", "content": "I will remember that."},
        ],
    )

    recall = provider.prefetch("QuartzFalcon", session_id="session-a")

    assert "QuartzFalcon" in recall
    assert any(path.is_file() for path in (hermes_home / "memkraft").rglob("*.md"))


def test_sync_turn_extracts_user_and_assistant_as_role_specific_evidence(tmp_path):
    provider = MemKraftMemoryProvider()
    provider.initialize("role-separated", hermes_home=str(tmp_path))

    with mock.patch.object(provider._store, "extract") as extract:
        provider.sync_turn(
            "The user evidence is QuartzFalcon.",
            "The assistant evidence is EmberOtter.",
            session_id="role-separated",
            messages=[
                {"role": "user", "content": "The user evidence is QuartzFalcon."},
                {"role": "assistant", "content": "The assistant evidence is EmberOtter."},
            ],
        )

    assert extract.call_args_list == [
        mock.call(
            "The user evidence is QuartzFalcon.",
            source="hermes:role-separated#user",
        ),
        mock.call(
            "The assistant evidence is EmberOtter.",
            source="hermes:role-separated#assistant",
        ),
    ]


def test_sync_turn_preserves_legacy_combined_extraction_without_role_messages(tmp_path):
    provider = MemKraftMemoryProvider()
    provider.initialize("legacy-combined", hermes_home=str(tmp_path))

    with mock.patch.object(provider._store, "extract") as extract:
        provider.sync_turn("User evidence.", "Assistant evidence.",
                           session_id="legacy-combined")

    extract.assert_called_once_with(
        "User: User evidence.\nAssistant: Assistant evidence.",
        source="hermes:legacy-combined",
    )


def test_sync_turn_preserves_completed_turn_chunk_bytes(tmp_path):
    provider = MemKraftMemoryProvider()
    provider.initialize("persisted-bytes", hermes_home=str(tmp_path))

    with mock.patch.object(provider._store, "extract"):
        provider.sync_turn("User evidence.", "Assistant evidence.", session_id="persisted-bytes")

    digest = hashlib.sha256(b"persisted-bytes").hexdigest()[:16]
    note = tmp_path / "memkraft" / "live-notes" / (
        "hermes-session-{}-000001.md".format(digest)
    )
    assert note.read_bytes() == (
        "# Hermes session {0} chunk 1\n\n"
        "## Completed turn\n\n"
        "User: User evidence.\nAssistant: Assistant evidence.\n\n"
    ).format(digest).encode("utf-8")


@pytest.mark.parametrize(
    ("user_content", "assistant_content", "expected_calls"),
    [
        (
            "",
            "Assistant only.",
            [mock.call("User: \nAssistant: Assistant only.", source="hermes:empty-side")],
        ),
        (
            "User only.",
            "",
            [mock.call("User: User only.\nAssistant: ", source="hermes:empty-side")],
        ),
        ("", "", []),
    ],
)
def test_sync_turn_skips_extraction_for_empty_side(
    tmp_path, user_content, assistant_content, expected_calls
):
    provider = MemKraftMemoryProvider()
    provider.initialize("empty-side", hermes_home=str(tmp_path))

    with mock.patch.object(provider._store, "extract") as extract:
        provider.sync_turn(user_content, assistant_content, session_id="empty-side")

    assert extract.call_args_list == expected_calls


def test_sync_turn_uses_switched_session_when_caller_omits_session_id(tmp_path):
    hermes_home = tmp_path / "hermes-home"
    provider = MemKraftMemoryProvider()
    provider.initialize("session-a", hermes_home=str(hermes_home))
    provider.on_session_switch("session-b", parent_session_id="session-a")

    provider.sync_turn(
        "The second compatibility codename is EmberOtter.",
        "Recorded.",
    )

    recall = provider.prefetch("EmberOtter", session_id="session-b")
    assert "EmberOtter" in recall


def test_completed_turn_is_retained_even_when_structured_extraction_succeeds(tmp_path):
    provider = MemKraftMemoryProvider()
    provider.initialize("mixed", hermes_home=str(tmp_path))

    provider.sync_turn(
        "My mixed-turn codename is CopperBadger and the budget is $10.",
        "Recorded.",
        session_id="mixed",
    )

    assert "CopperBadger" in provider.prefetch("CopperBadger", session_id="mixed")


def test_lossy_slug_equivalent_session_ids_remain_isolated(tmp_path):
    provider = MemKraftMemoryProvider()
    provider.initialize("a:b", hermes_home=str(tmp_path))
    provider.sync_turn("The colon session token is IndigoMoth.", "Recorded.", session_id="a:b")
    provider.on_session_switch("ab", parent_session_id="a:b")
    provider.sync_turn("The plain session token is ScarletPuma.", "Recorded.", session_id="ab")

    notes = sorted((tmp_path / "memkraft" / "live-notes").glob("hermes-session-*.md"))
    assert len(notes) == 2
    contents = [path.read_text(encoding="utf-8") for path in notes]
    assert sum("IndigoMoth" in content for content in contents) == 1
    assert sum("ScarletPuma" in content for content in contents) == 1


def test_session_turn_files_rotate_before_exceeding_configured_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_HERMES_TURN_FILE_BYTES", "512")
    provider = MemKraftMemoryProvider()
    provider.initialize("large", hermes_home=str(tmp_path))

    for index in range(6):
        provider.sync_turn(
            "Rotation token {} {}".format(index, "x" * 180),
            "Recorded.",
            session_id="large",
        )

    notes = sorted((tmp_path / "memkraft" / "live-notes").glob("hermes-session-*.md"))
    assert len(notes) >= 2
    assert all(path.stat().st_size <= 512 for path in notes)


def test_large_multibyte_turn_stays_bounded_after_chunk_index_grows(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_HERMES_TURN_FILE_BYTES", "512")
    provider = MemKraftMemoryProvider()
    provider.initialize("many-chunks", hermes_home=str(tmp_path))

    provider.sync_turn(
        "ASCII payload {}".format("x" * 6000),
        "Recorded.",
        session_id="many-chunks",
    )

    notes = sorted((tmp_path / "memkraft" / "live-notes").glob("hermes-session-*.md"))
    assert len(notes) >= 10
    assert all(path.stat().st_size <= 512 for path in notes)
    joined = b"".join(path.read_bytes().split(b"\n\n", 1)[1] for path in notes)
    assert joined.decode("utf-8").count("x") == 6000


def test_multibyte_character_rolls_to_next_chunk_when_one_byte_remains(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_HERMES_TURN_FILE_BYTES", "512")
    provider = MemKraftMemoryProvider()
    provider.initialize("utf8-boundary", hermes_home=str(tmp_path))

    digest = hashlib.sha256(b"utf8-boundary").hexdigest()[:16]
    directory = tmp_path / "memkraft" / "live-notes"
    directory.mkdir(parents=True, exist_ok=True)
    first = directory / "hermes-session-{}-000001.md".format(digest)
    header = "# Hermes session {} chunk 1\n\n".format(digest).encode("utf-8")
    first.write_bytes(header + b"x" * (511 - len(header)))

    provider.sync_turn("Boundary token 가나.", "Recorded.", session_id="utf8-boundary")

    notes = sorted(directory.glob("hermes-session-*.md"))
    contents = [path.read_text(encoding="utf-8") for path in notes]
    assert len(notes) >= 2
    assert all(path.stat().st_size <= 512 for path in notes)
    assert any("Boundary token 가나." in content for content in contents)


def test_concurrent_processes_preserve_all_turns_within_byte_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_HERMES_TURN_FILE_BYTES", "512")
    home = str(tmp_path)
    tokens = ["ProcessToken{:02d}".format(index) for index in range(12)]
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    processes = [
        ctx.Process(target=_sync_turn_in_process, args=(home, token, start))
        for token in tokens
    ]

    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    notes = sorted((tmp_path / "memkraft" / "live-notes").glob("hermes-session-*.md"))
    assert notes
    assert all(path.stat().st_size <= 512 for path in notes)
    content = "\n".join(path.read_text(encoding="utf-8") for path in notes)
    assert all(content.count(token) == 1 for token in tokens)


def test_completed_turn_remains_recallable_after_provider_reinitialization(tmp_path):
    first = MemKraftMemoryProvider()
    first.initialize("durable", hermes_home=str(tmp_path))
    first.sync_turn("The durable token is CobaltHeron.", "Recorded.", session_id="durable")

    second = MemKraftMemoryProvider()
    second.initialize("durable", hermes_home=str(tmp_path))

    assert "CobaltHeron" in second.prefetch("CobaltHeron", session_id="durable")


@pytest.mark.parametrize("mode", ["", "warn"])
def test_install_check_default_and_warn_expose_report_without_failing(tmp_path, monkeypatch, mode):
    if mode:
        monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", mode)
    report = {"consistent": False, "reasons": ["editable_redirect"]}
    with mock.patch("memkraft.hermes_provider.installation_report", return_value=report):
        provider = MemKraftMemoryProvider()
        provider.initialize("warn", hermes_home=str(tmp_path))
    assert provider._installation_report == report


def test_install_check_strict_rejects_inconsistent_install(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", "strict")
    report = {"consistent": False, "reasons": ["version_mismatch"]}
    with mock.patch("memkraft.hermes_provider.installation_report", return_value=report):
        with pytest.raises(RuntimeError, match="version_mismatch"):
            MemKraftMemoryProvider().initialize("strict", hermes_home=str(tmp_path))


def test_install_check_off_skips_probe(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", "off")
    with mock.patch("memkraft.hermes_provider.installation_report") as probe:
        provider = MemKraftMemoryProvider()
        provider.initialize("off", hermes_home=str(tmp_path))
    probe.assert_not_called()
    assert provider._installation_report is None


def test_installation_report_method_returns_fresh_report():
    report = {"consistent": True, "reasons": []}
    with mock.patch("memkraft.hermes_provider.installation_report", return_value=report):
        assert MemKraftMemoryProvider().installation_report() == report


def test_install_check_warn_catches_probe_exception_and_initializes(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", "warn")
    with mock.patch("memkraft.hermes_provider.installation_report", side_effect=OSError("metadata unavailable")):
        provider = MemKraftMemoryProvider()
        provider.initialize("warn-exception", hermes_home=str(tmp_path))
    assert provider._store is not None
    assert provider._installation_report["reasons"] == ["probe_exception"]
    assert provider._installation_report["errors"] == ["OSError: metadata unavailable"]


def test_install_check_strict_wraps_probe_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", "strict")
    with mock.patch("memkraft.hermes_provider.installation_report", side_effect=OSError("metadata unavailable")):
        with pytest.raises(RuntimeError, match="probe_exception"):
            MemKraftMemoryProvider().initialize("strict-exception", hermes_home=str(tmp_path))


def test_hermes_manager_loads_entrypoint_despite_warn_duplicate_report(tmp_path, monkeypatch):
    from importlib.metadata import EntryPoint
    from agent.memory_manager import MemoryManager

    monkeypatch.setenv("MEMKRAFT_INSTALL_CHECK", "warn")
    manager = MemoryManager()

    class Context:
        def register_memory_provider(self, provider):
            manager.add_provider(provider)

    report = {"consistent": False, "reasons": ["duplicate_distributions"]}
    register = EntryPoint("memkraft", "memkraft.hermes_provider:register", "hermes_agent.memory_providers").load()
    with mock.patch("memkraft.hermes_provider.installation_report", return_value=report):
        register(Context())
        provider = manager.get_provider("memkraft")
        assert provider is not None
        provider.initialize("manager", hermes_home=str(tmp_path))
    assert provider._installation_report == report
