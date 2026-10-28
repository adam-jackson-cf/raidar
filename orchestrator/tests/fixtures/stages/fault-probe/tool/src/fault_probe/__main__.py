"""Commands: ``setup`` exits with the ``setup_exit`` param; ``inspect`` parses the agent's JSON."""

import json
import os
import sys
from pathlib import Path


def main(argv):
    context = json.loads(Path(os.environ["RAIDAR_STAGE_CONTEXT"]).read_text(encoding="utf-8"))
    command = argv[0] if argv else ""
    if command == "setup":
        code = int(context["params"]["setup_exit"])
        if code:
            print("setup refused", file=sys.stderr)
            return code
        print(json.dumps({"tokens": {}}))
    elif command == "inspect":
        # unparseable agent data raises: a traceback and exit 1, no stop result
        json.loads((Path(context["state"]) / "data.json").read_text(encoding="utf-8"))
        print(json.dumps({"status": "done", "message": "", "tokens": {}, "carry": {}}))
    else:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
