from memkraft.hermes_provider import MemKraftMemoryProvider


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


def test_completed_turn_remains_recallable_after_provider_reinitialization(tmp_path):
    first = MemKraftMemoryProvider()
    first.initialize("durable", hermes_home=str(tmp_path))
    first.sync_turn("The durable token is CobaltHeron.", "Recorded.", session_id="durable")

    second = MemKraftMemoryProvider()
    second.initialize("durable", hermes_home=str(tmp_path))

    assert "CobaltHeron" in second.prefetch("CobaltHeron", session_id="durable")
