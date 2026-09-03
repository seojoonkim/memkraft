# Hermes Agent integration

MemKraft 3.5.0 has a verified Hermes Agent memory-provider integration for these exact targets:

- Hermes Agent 0.19.0 at `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`, Python 3.11 and 3.12, using a profile-local directory bridge.
- Hermes Agent 0.20.1 source at `45af7a71fcd420b4422d2c074b1ce58b9ce0d048`, Python 3.11 and 3.12, using installed Python entry-point discovery.

This is not a claim of compatibility with every Hermes release. Install MemKraft into the same Python environment that runs Hermes and use an absolute, local-filesystem `HERMES_HOME`.

## Hermes Agent 0.19.0

```bash
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
test -d "$HERMES_HOME"
python -m pip install "memkraft==3.5.0"
memkraft-hermes-install --hermes-version 0.19.0 --hermes-home "$HERMES_HOME"
hermes config set memory.provider memkraft
```

`HERMES_HOME` must already exist and be the active profile home, such as `~/.hermes` for the default profile or `~/.hermes/profiles/<name>` for a named profile. The home, `plugins`, and generated bridge directories must be owned by the current user and not group- or world-writable. The equivalent source-checkout helper is `scripts/install_hermes_plugin.py`. It writes `$HERMES_HOME/plugins/memkraft/__init__.py`, remains idempotent after Python creates `__pycache__`, and refuses to replace a namespace it cannot verify.

## Hermes Agent 0.20.1 source

Pin the Hermes checkout to the verified source SHA, install Hermes and MemKraft in the same environment, and do not retain the 0.19 directory bridge:

```bash
git clone https://github.com/NousResearch/hermes-agent.git /path/to/hermes-agent
git -C /path/to/hermes-agent checkout --detach 45af7a71fcd420b4422d2c074b1ce58b9ce0d048
test "$(git -C /path/to/hermes-agent rev-parse HEAD)" = "45af7a71fcd420b4422d2c074b1ce58b9ce0d048"
python -m pip install -e /path/to/hermes-agent
python -m pip install "memkraft==3.5.0"
hermes config set memory.provider memkraft
```

The MemKraft wheel publishes `memkraft.hermes_provider:register` under `hermes_agent.memory_providers`. A stale `$HERMES_HOME/plugins/memkraft` directory has higher loader precedence and must be removed only after confirming it is the generated 0.19 bridge.

Restart the Hermes CLI, gateway, daemon, or desktop backend after changing provider installation or configuration. Completed turns are stored as plaintext Markdown under `$HERMES_HOME/memkraft/live-notes` unless an absolute `MEMKRAFT_DIR` is set. Treat installed plugins as trusted code, protect the storage directory, and treat recalled text as untrusted reference context rather than instructions. Local filesystem locking is tested; network and synchronized filesystems are not supported by this compatibility claim.

## Automatic development-experience learning

Hermes hosts that pass the completed turn's OpenAI-format `messages` argument to `sync_turn` need no additional lifecycle hook. The MemKraft provider will:

1. inspect only the current completed user turn;
2. recognize failed tool calls and require the turn's final test, lint, check, or build verifier to succeed;
3. discard raw arguments and outputs, retaining only coarse tool routes and normalized error classes;
4. write deterministic failure and success trajectories through ReasoningBank; and
5. add bounded **avoid/reuse** guidance to `prefetch` for a similar later task.

Interrupted turns, failures without a later verifier, turns whose final verifier fails, and turns that mutate code after the verifier do not create development lessons. Semantically identical completed turns are idempotent even if tool-call IDs change; the same pattern in a different session remains a distinct experience. The feature is enabled by default; set this before starting Hermes for an emergency opt-out:

```bash
export MEMKRAFT_HERMES_DEV_EXPERIENCE=off
```

This feature records and retrieves advisory lessons. It does not execute commands, modify skills, promote Improvement Ledger candidates, weaken permissions, or deploy code.
