# Hermes Agent integration

MemKraft (4.1.0 and later; current 4.3.0) exposes one default-on auxiliary-feature bundle through the
`hermes_agent.memory_providers` entry point. Install it into the same Python
environment that runs Hermes, select `memory.provider: memkraft`, and restart the
Hermes process. No copied plugin bridge or per-feature toggle is required on
current Hermes releases.

```bash
python -m pip install "memkraft==4.3.0"
hermes config set memory.provider memkraft
hermes gateway restart   # when a gateway is running
```

## Verified release matrix

The MemKraft release gates (unchanged since 4.1.0, re-run for 4.3.0) test Hermes Agent 0.19.0 at commit
`3ef6bbd201263d354fd83ec55b3c306ded2eb72a` and Hermes Agent 0.20.1 source at
commit `45af7a71fcd420b4422d2c074b1ce58b9ce0d048`, each on Python 3.11 and 3.12.
This pinned matrix is not a claim of compatibility with every Hermes release.

A generated directory bridge remains available only for the verified legacy
0.19.0 target. From a source checkout, install it with
`scripts/install_hermes_plugin.py`:

```bash
python scripts/install_hermes_plugin.py \
  --hermes-version 0.19.0 \
  --hermes-home "$HERMES_HOME"
```

## Automatic feature bundle

When the provider is active, `feature_capabilities()` advertises:

- persistent recall and completed-turn retention;
- adaptive ETA from completed runs;
- remaining-time updates after meaningful progress;
- active, provider-wait, and rework phase learning;
- conservative development-detour learning;
- installation-integrity diagnostics.

Hermes owns lifecycle observation, task classification, clocks, status delivery,
and execution. MemKraft records only private aggregate evidence and never starts,
schedules, retries, or deploys work itself.

ETA becomes available after five successful samples for the exact
`platform.task-class` cohort. Failed, interrupted, incomplete, and aborted runs
remain audit evidence but never train ETA. Hermes continues normally when timing
or any other optional provider hook fails.

Development-experience learning inspects only completed current-turn messages. It
requires a failed tool route followed by a final successful verifier and stores
only coarse routes and normalized error classes. Raw arguments and outputs are
not retained. Set `MEMKRAFT_HERMES_DEV_EXPERIENCE=off` before starting Hermes for
an emergency opt-out.

## Prefetch recall

Each turn, prefetch returns up to five recalled items. Entity pages that only
record detections are dropped, and chat-derived template entity pages (whose
every timeline row came from chat auto-extraction) are used only when nothing
else matches. Since 4.2.1, when those pages fill the first 12 search results,
prefetch widens the candidate window to 80 and then 240 results until five real
documents surface. Up to two matching owner sayings are added verbatim.

Since 4.2.2 the provider keeps a disposable token cache in
`$HERMES_HOME/memkraft/.memkraft/index/`, so the first recall after a gateway
restart re-reads only changed files. Between turns it reuses a verified store
scan for 15 seconds (`MEMKRAFT_HERMES_STAT_TTL`, `0` disables); writes made by
the provider invalidate immediately, and edits from other processes appear
within that window.

## Privacy and storage

The timing ledger stores closed-domain platform, task class, phase, outcome,
elapsed milliseconds, and opaque identifiers. It must not contain raw prompts,
credentials, tool arguments, full output, or absolute paths. Data stays under
`$HERMES_HOME/memkraft` unless `MEMKRAFT_DIR` is set to another absolute local
path. Protect that directory as private agent data.

## Integrity and smoke verification

The provider exposes `integration_report(run_smoke=True)`. It checks package
installation consistency, reports the complete capability bundle, and performs
an aborted append-only timing round trip that cannot enter ETA cohorts.

The installed-wheel compatibility workflow verifies entry-point discovery,
capabilities, recall/retain, lifecycle timing, session rotation, shutdown, and
restart recall against pinned Hermes targets. A stale
`$HERMES_HOME/plugins/memkraft` directory can mask the package entry point and
must be removed only after verifying that it is the old generated 0.19 bridge.
