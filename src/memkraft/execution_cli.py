"""One-request/one-response JSON-over-stdio transport for MKEP/0 (§12)."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, NoReturn, Optional, Sequence, TextIO

from . import MemKraft
from . import execution_state
from .execution_dispatch import dispatch


class _UsageParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(64, "%s: error: %s\n" % (self.prog, message))


def exit_code(response: Dict[str, Any]) -> int:
    """Map a response's stable error class to the §12.2 process status."""
    if response.get("ok") is True:
        return 0
    error_class = (response.get("error") or {}).get("class")
    if error_class in ("input", "negotiation", "limits"):
        return 1
    if error_class in ("state", "evidence", "idempotency", "lease", "integrity"):
        return 2
    if error_class == "io":
        return 3
    return 70


def _parser() -> argparse.ArgumentParser:
    parser = _UsageParser(prog="memkraft exec call", add_help=True)
    parser.add_argument("--base-dir", default=None)
    parser.add_argument("--lock-timeout", type=float, default=2.0)
    return parser


def main(argv: Optional[Sequence[str]] = None, *, stdin: Optional[TextIO] = None,
         stdout: Optional[TextIO] = None, stderr: Optional[TextIO] = None) -> int:
    """Read one JSON value, dispatch it, and emit exactly one JSON response."""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    parser = _parser()
    # argparse writes through process globals; temporarily route usage diagnostics.
    old_stderr = sys.stderr
    sys.stderr = error_stream
    try:
        try:
            options = parser.parse_args(argv)
        except SystemExit as error:
            return 64 if error.code is None else int(error.code)
    finally:
        sys.stderr = old_stderr

    store = MemKraft(base_dir=options.base_dir)
    try:
        request = json.loads(input_stream.read())
    except (UnicodeError, json.JSONDecodeError) as error:
        response = dispatch(store, None)
        print("invalid JSON input: %s" % error, file=error_stream)
    else:
        previous = execution_state.EXECUTION_LOCK_TIMEOUT_S
        execution_state.EXECUTION_LOCK_TIMEOUT_S = options.lock_timeout
        try:
            response = dispatch(store, request)
        except Exception as error:  # no response can be promised after an internal crash
            print("internal transport failure: %s" % error, file=error_stream)
            return 70
        finally:
            execution_state.EXECUTION_LOCK_TIMEOUT_S = previous

    code = exit_code(response)
    output_stream.write(json.dumps(response, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")) + "\n")
    if code:
        print("MKEP request rejected", file=error_stream)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
