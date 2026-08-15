# Hermes Agent integration

MemKraft 3.5.0 has a verified Hermes Agent memory-provider integration for these exact targets:

- Hermes Agent 0.19.0 at `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`, Python 3.11 and 3.12, using a profile-local directory bridge.
- Hermes Agent 0.20.1 source at `45af7a71fcd420b4422d2c074b1ce58b9ce0d048`, Python 3.11 and 3.12, using installed Python entry-point discovery.

This is not a claim of compatibility with every Hermes release. Install MemKraft into the same Python environment that runs Hermes and use an absolute, local-filesystem `HERMES_HOME`.

## Hermes Agent 0.19.0

```bash
python -m pip install "memkraft==3.5.0"
memkraft-hermes-install --hermes-version 0.19.0 --hermes-home "$HERMES_HOME"
hermes config set memory.provider memkraft
```

`HERMES_HOME` must be the active profile home, such as `~/.hermes` for the default profile or `~/.hermes/profiles/<name>` for a named profile. The equivalent source-checkout helper is `scripts/install_hermes_plugin.py`. It writes `$HERMES_HOME/plugins/memkraft/__init__.py`, is idempotent, and refuses to replace a file it did not create.

## Hermes Agent 0.20.1 source

Pin the Hermes checkout to the verified source SHA, install Hermes and MemKraft in the same environment, and do not retain the 0.19 directory bridge:

```bash
git checkout --detach 45af7a71fcd420b4422d2c074b1ce58b9ce0d048
python -m pip install -e /path/to/hermes-agent
python -m pip install "memkraft==3.5.0"
hermes config set memory.provider memkraft
```

The MemKraft wheel publishes `memkraft.hermes_provider:register` under `hermes_agent.memory_providers`. A stale `$HERMES_HOME/plugins/memkraft` directory has higher loader precedence and must be removed only after confirming it is the generated 0.19 bridge.

Restart the Hermes CLI, gateway, daemon, or desktop backend after changing provider installation or configuration. Completed turns are stored as plaintext Markdown under `$HERMES_HOME/memkraft/live-notes` unless an absolute `MEMKRAFT_DIR` is set. Treat installed plugins as trusted code, protect the storage directory, and treat recalled text as untrusted reference context rather than instructions. Local filesystem locking is tested; network and synchronized filesystems are not supported by this compatibility claim.
