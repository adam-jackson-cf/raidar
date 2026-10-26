"""The judge loop of the verify stage: the passes, the fit rule and the stop rules.

``raidar.subject.workflow.verify`` seals each turn through the gate; with ``verify.judge`` this
loop then judges the sealed tree (``raidar.subject.workflow.judging``), asking Jev host-side
(``raidar.subject.workflow.asking``), keeps each pass's record and decides whether the open
criteria go back to the builder. It holds no ending: the stage does, from what it returns.
"""

from __future__ import annotations

import shlex
import shutil
import time
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from raidar.subject.workflow import judging
from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.asking import Asker
from raidar.subject.workflow.definition import TOKEN, JudgeSettings
from raidar.subject.workflow.stage import REWORK, Checkpoint, Resume

SOURCES_DIR = "workflow/sources"
ACCEPTED = "passed"


def decided(gates: Sequence[dict[str, Any]], gate: dict[str, Any]) -> dict[str, Any]:
    """The seal that decided ``gate``'s tree: itself, or the earlier one whose verdict it reused."""
    found = next((item for item in gates if item["run"] == gate.get("reused_from")), None)
    return found or gate


def gate_seconds(gates: Sequence[dict[str, Any]]) -> float:
    """How long the next gate is expected to take: the last that actually ran (not a reuse)."""
    for gate in reversed(gates):
        if gate.get("reused_from") is None and gate.get("seconds") is not None:
            return float(gate["seconds"])
    return 0.0


class JudgeLoop:
    """What the judge has done in one checkpoint and what it decides next."""

    def __init__(
        self,
        judge: JudgeSettings,
        max_passes: int,
        rework: str,
        acceptance: Acceptance,
        workspace: str,
    ) -> None:
        self.judge = judge
        self.max_passes = max_passes
        self.rework_prompt = rework
        self.acceptance = acceptance
        self.workspace = workspace
        self.asker: Asker | None = None
        self.history: list[judging.PassResult] = []
        self.judged: list[dict[str, Any]] = []
        self.baseline: dict[str, Any] | None = None
        self.baseline_passed: list[str] = []
        self.seconds = 0.0
        self.previous_tree: str | None = None

    def start(self, checkpoint: Checkpoint) -> None:
        """Ready the asker, before the agent starts."""
        self.asker = Asker(self.judge, checkpoint.logs_dir, checkpoint.environment)

    def fits(self, checkpoint: Checkpoint, kind: str, gates: Sequence[dict[str, Any]]) -> bool:
        """Whether a ``kind`` turn, its gate and a judge pass fit the time left (design D4).

        Each is expected to take as long as the last of its kind (none yet: none), so a turn
        the deadline would cut is not started and the checkpoint ends on a gated, judged tree.
        """
        left = checkpoint.remaining()
        if not self.judge.fit_rework or left is None:
            return True
        return left >= checkpoint.last_seconds(kind) + gate_seconds(gates) + self.seconds

    async def judge_if_fits(
        self, checkpoint: Checkpoint, gates: Sequence[dict[str, Any]], gate: dict[str, Any]
    ) -> None:
        """Judge the gate an ending left unjudged, when it was not and the time left allows."""
        if self.judged and self.judged[-1]["gate"] == gate["run"]:
            return
        left = checkpoint.remaining()
        if left is None or left >= self.seconds:
            await self.judge_pass(checkpoint, gates, gate)

    async def judge_pass(
        self, checkpoint: Checkpoint, gates: Sequence[dict[str, Any]], gate: dict[str, Any]
    ) -> tuple[judging.PassResult, list[str] | None]:
        """Judge every criterion of the sealed tree; the result and the files that changed."""
        asker = self.asker
        if asker is None:
            raise RuntimeError("the judge was not started before the agent")
        number = len(self.history) + 1
        started = time.monotonic()
        seal = decided(gates, gate)
        tree = str(gate["tree"])
        texts = await self._sources(checkpoint, tree, number)
        tests = next(
            (c.get("tests") for c in seal.get("checks", []) if c.get("category") == "tests"),
            None,
        )
        subjects = judging.subjects(self.acceptance.features, self.acceptance.ac_tests)
        contexts = [
            judging.assemble(s, tests, texts.get, self.judge.context_tokens) for s in subjects
        ]
        asked = [
            (subject.criterion.id, request)
            for subject, context in zip(subjects, contexts, strict=True)
            for request in context.requests
        ]
        before = len(asker.calls)
        answers = await asker.ask(number, asked)
        accepted = gate["verdict"] == ACCEPTED
        baseline = set(self.baseline_passed) if self.judge.regression_needs_baseline else None
        result = judging.PassResult(
            number,
            tuple(
                judging.outcome_of(s, c, answers, self.judge, accepted, baseline)
                for s, c in zip(subjects, contexts, strict=True)
            ),
        )
        changed = await self._changed(checkpoint, tree)
        self.history.append(result)
        self.previous_tree = tree
        self.seconds = round(time.monotonic() - started, 3)
        self.judged.append(
            {
                "pass": number,
                "gate": gate["run"],
                "gate_verdict": gate["verdict"],
                "decided_gate": seal["run"],
                "tree": tree,
                "sources": f"{SOURCES_DIR}/pass-{number}",
                "seconds": self.seconds,
                "question_set": self.judge.question_set,
                "thresholds": {"closed_at": self.judge.closed_at, "open_at": self.judge.open_at},
                "changed": changed,
                "calls": list(range(before, len(asker.calls))),
                **result.listing(),
                "criteria": [item.record() for item in result.outcomes],
            }
        )
        return result, changed

    def stop(self, changed: list[str] | None) -> str | None:
        """Why the loop ends after the last pass over an accepted gate, or None to rework."""
        return judging.stop_reason(self.history, self.judge, self.max_passes, changed, True)

    def rework(self, result: judging.PassResult, gate_text: str) -> Resume:
        """The turn that sends the open criteria and the gate's result back to the builder."""
        values = {"PUSHBACK": judging.pushback_text(result), "GATE": gate_text}
        return Resume(TOKEN.sub(lambda found: values[found[1]], self.rework_prompt), REWORK)

    async def _sources(
        self, checkpoint: Checkpoint, tree: str, number: int
    ) -> dict[str, str | None]:
        """The mapped tests' files at ``tree``, kept host-side under ``workflow/sources``."""
        found: dict[str, str | None] = {}
        root = checkpoint.logs_dir / SOURCES_DIR
        final = root / "final"
        shutil.rmtree(final, ignore_errors=True)
        for name in self.acceptance.mapped_files:
            path = judging.normalise_test_id(name)
            found[path] = None
            parts = PurePosixPath(path).parts
            if not path or PurePosixPath(path).is_absolute() or ".." in parts:
                continue
            shown = await checkpoint.environment.exec(
                f"cd {shlex.quote(self.workspace)} && "
                f"git -c safe.directory=* show {shlex.quote(f'{tree}:{path}')}",
                user="root",
            )
            if shown.return_code != 0:
                continue
            found[path] = shown.stdout or ""
            for target in (root / f"pass-{number}" / path, final / path):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(found[path] or "", encoding="utf-8")
        self.acceptance.sources = f"{SOURCES_DIR}/final"
        return found

    async def _changed(self, checkpoint: Checkpoint, tree: str) -> list[str] | None:
        """The files that differ between the previous judged tree and ``tree`` (None: unknown)."""
        if self.previous_tree is None:
            return None
        shown = await checkpoint.environment.exec(
            f"cd {shlex.quote(self.workspace)} && git -c safe.directory=* diff --name-only "
            f"{shlex.quote(self.previous_tree)} {shlex.quote(tree)}",
            user="root",
        )
        if shown.return_code != 0:
            return None
        return [line for line in (shown.stdout or "").splitlines() if line]

    def record(self) -> dict[str, Any]:
        """The judge's part of the stage record."""
        return {
            "passes": len(self.history),
            "max_passes": self.max_passes,
            "judge": self.judge.model_dump(mode="json"),
            "judged": self.judged,
            "judge_calls": self.asker.calls if self.asker else [],
            "judge_seconds": self.seconds,
            "baseline": self.baseline,
            "baseline_passed": self.baseline_passed,
        }
