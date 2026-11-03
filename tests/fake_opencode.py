"""PATH stand-in for the opencode command.

Contract tests point PATH at a wrapper that runs this file. It records every
invocation, including stdin, and answers --version and run. Later tickets
extend the answers; they do not grow a second fake.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _emit_configured_error() -> bool:
    """Print a scripted error for the first N runs, then let the call succeed.

    `OPENCODE_FAKE_FAILS` is how many leading runs fail. Unset means every
    run fails when an error body is configured, which is the one-shot case.
    """
    body = os.environ.get("OPENCODE_FAKE_ERROR", "")
    if not body:
        return False
    fails = os.environ.get("OPENCODE_FAKE_FAILS", "")
    if fails:
        state = Path(os.environ["OPENCODE_FAKE_STATE"])
        seen = int(state.read_text(encoding="utf-8") or "0") if state.is_file() else 0
        state.write_text(str(seen + 1), encoding="utf-8")
        if seen >= int(fails):
            return False
    sys.stdout.write(body if body.endswith("\n") else body + "\n")
    sys.exit(int(os.environ.get("OPENCODE_FAKE_RC", "1")))


def main() -> None:
    argv = sys.argv[1:]
    stdin = sys.stdin.read()
    log_path = os.environ.get("OPENCODE_FAKE_LOG", "")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"argv": argv, "stdin": stdin}, ensure_ascii=False) + "\n"
            )
    if argv[:1] == ["--version"]:
        sys.stdout.write(os.environ.get("OPENCODE_FAKE_VERSION", "1.2.24"))
        return
    if _emit_configured_error():
        return
    session = os.environ.get("OPENCODE_FAKE_SESSION", "ses_captured")
    if "--session" in argv:
        index = argv.index("--session")
        if index + 1 < len(argv):
            session = argv[index + 1]
    sys.stdout.write(
        json.dumps(
            {
                "type": "text",
                "sessionID": session,
                "part": {"text": "ok"},
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
