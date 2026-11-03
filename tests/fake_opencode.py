"""PATH stand-in for the opencode command.

Contract tests point PATH at a wrapper that runs this file. It records every
invocation, including stdin, and answers --version and run. Later tickets
extend the answers; they do not grow a second fake.
"""

from __future__ import annotations

import json
import os
import sys


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
