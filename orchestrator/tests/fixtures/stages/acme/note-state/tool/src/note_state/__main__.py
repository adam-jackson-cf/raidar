"""Commands: ``setup``, ``check`` and ``inspect`` read the root-owned stage context."""

import json
import os
import sys
from pathlib import Path


def _context(argv):
    if "--context" in argv:
        path = argv[argv.index("--context") + 1]
    else:
        path = os.environ["RAIDAR_STAGE_CONTEXT"]
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _words(context):
    try:
        text = (Path(context["state"]) / "note.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(text.split())


def _stop(context):
    state = Path(context["state"])
    needed = context["params"]["min_words"]
    words = _words(context)
    if (state / "abort").exists():
        status, message = "closed", ""
    elif words < needed:
        status, message = "resume", f"Write a note of at least {needed} words."
    else:
        status, message = "done", ""
    return {"status": status, "message": message, "tokens": {}, "carry": {"words": words}}


def main(argv):
    command = argv[0] if argv else ""
    context = _context(argv)
    if command == "setup":
        guidance = "Say what you changed."
        print(json.dumps({"tokens": {"GUIDANCE": guidance}}))
    elif command == "check":
        return 0 if _words(context) >= context["params"]["min_words"] else 1
    elif command == "inspect":
        print(json.dumps(_stop(context)))
    else:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
