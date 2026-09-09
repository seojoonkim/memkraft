# MCP persistence and wire compatibility

Install `memkraft[mcp]`; the supported SDK range is `mcp>=1.0,<2.0`.

The remember tool tracks new entities before updating them. Search preserves its Python list return contract and numeric scores; ordinary MCP wire responses use JSON with non-finite numbers rejected. Execution-protocol responses retain strict canonical serialization. Tool diagnostics are redirected to stderr so stdout remains JSON-RPC.

Verified locally with an installed wheel and two isolated OpenClaw agent processes: the first calls remember, a file contains the random verification nonce, and a fresh session calls recall without being given the nonce. This validates memory persistence, not automatic WikiSkill injection.

Regression coverage: tests/test_v081_mcp.py and tests/test_execution_mcp.py.
