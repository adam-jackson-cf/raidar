"""A gate's base is the lab's starting commit, never authored context or the current tip."""

from __future__ import annotations

import json
import os
import shlex
import stat
from dataclasses import replace
from pathlib import Path

from raidar.subject.primitive.gate import GateRun, context_path
from tests.test_primitive_gate import PASS, Container, command, make_rig


async def test_base_overrides_authored_context_and_stays_at_start(tmp_path: Path) -> None:
    rig = make_rig(tmp_path, PASS)
    rig.script(
        "has-base",
        "import json, os, subprocess\n"
        "base = json.load(open(os.environ['RAIDAR_STAGE_CONTEXT']))['base']\n"
        "subprocess.check_call(['git', 'merge-base', '--is-ancestor', base, 'HEAD'])\n",
    )
    package = replace(
        rig.package([command("ok", "has-base")]), context={"base": "forged", "task": "x"}
    )
    run = GateRun(Container(dict(os.environ)), rig.tmp / "logs", [package], paths=rig.paths)
    await run.prepare()
    try:
        context = Path(context_path(rig.paths, package))
        assert json.loads(context.read_text()) == {"base": run.base, "task": "x"}
        assert run.base != "forged"
        assert stat.S_IMODE(context.stat().st_mode) == 0o644
        if os.geteuid() == 0:
            assert context.stat().st_uid == 0
        start = run.base
        (rig.workspace / "changed.txt").write_text("changed\n")
        git = f"git -C {shlex.quote(str(rig.workspace))}"
        await run._agent(
            f"{git} add changed.txt && {git} -c user.name=fixture "
            "-c user.email=fixture@example.invalid commit -m changed"
        )
        assert (await run._agent(f"{git} rev-parse HEAD")).strip() != start
        await run.seal("after-commit")
        assert json.loads(context.read_text())["base"] == start
    finally:
        await run.close()
