"""``jev-judge/1``: the host-side aspect judge of a checkpoint, over its sealed gate.

Raidar owns this primitive (a workflow opts in with its ``judge`` block); no package runs code for
it. After a seal the checkpoint's ``gate/1`` has recorded, the judge reads the criteria a package
produced (``judge.criteria`` = ``<package>.<output>``: the newest valid ``outputs/1`` copy of a
``criteria/1`` output, re-hashed and re-validated against ``raidar_contract.criteria`` here, and
fixed at the first valid one) and judges each criterion on the sealed tree
(``raidar.subject.workflow.judging``): Jev is asked, host-side and never from the sandbox
(``raidar.subject.workflow.asking``), one question per aspect over the mapped tests' source (read
from the work tree, checked against the seal's digest and kept under ``workflow/sources``). The
tests that pass on the base are the gate's ``baseline`` run. A criterion still open goes back to
the builder as a ``rework`` turn (the pushback and the gate's result) and the work is sealed and
judged again, up to ``max_passes``.

Endings: ``verified``, ``pass_limit``, ``no_progress``, ``budget_exhausted`` (the fit rule: a turn
starts only when the last turn of its kind, the last gate and the last judge pass fit the time
left; the tree is then gated and judged), ``deadline`` (the phase was cut, or no time was left to
judge), ``gate_refused`` (the fix rounds ran out; that tree is judged, not reworked) and
``gate_error``. ``no_criteria`` is the one excused terminal state: the criteria producer's stop
closed for good (a legitimate agent outcome that owes no output), so nothing is judged. A
producer that answered ``done`` owes a valid ``criteria/1`` copy: a missing, invalid,
digest-mismatching or later-changed copy is ``TrialInvalid("integrity")``, never an ending. A
producer neither done nor closed leaves zero criteria, judged as such (never ``verified``).
``gate_ending`` is the last seal's own verdict.

``record`` is the primitive's evidence at ``workflow.json["jev-judge/1"]``::

    {"criteria": "define.criteria", "criteria_sha256": str | None, "model": str,
     "ending", "gate_ending", "errors": [str],
     "passes", "max_passes", "judge": {model, closed_at, open_at, question_set,
         regression_needs_baseline, retries, timeout_s, context_tokens, stall_repeats, fit_rework},
     "judged": [{"pass", "gate" (seal run no.), "gate_verdict", "source_mismatch", "tree",
         "sources" ("workflow/sources/pass-N"), "seconds", "question_set", "thresholds", "changed",
         "calls" (indices into judge_calls), "closed", "open", "undecided", "criteria": [...]}],
     "judge_calls": [{"index", "pass", "criterion", "aspect", "attempt", "probability", "result",
         "seconds", "model", "resolved_model", "http_status", "transport_error",
         "request_file", "response_file", "result_file"}],
     "judge_seconds", "baseline": {...} | None, "baseline_passed": [test ids]}

All paths are relative to the agent logs directory (``workflow/judge/w<N>/...`` per attempt).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, NoReturn, Protocol

from raidar_contract.criteria import validate_criteria
from raidar_contract.workflow import PackageJudge

from raidar.subject.primitive.gate import GateRun
from raidar.subject.primitive.judge import PRIMITIVE, config_of
from raidar.subject.primitive.site import TrialInvalid
from raidar.subject.workflow import judging
from raidar.subject.workflow.acceptance import Acceptance
from raidar.subject.workflow.judgeloop import JudgeLoop
from raidar.subject.workflow.package import GATE_BUDGET, GATE_ERROR, GATE_PASSED
from raidar.subject.workflow.stage import DEADLINE, REWORK, Checkpoint, Resume

BUDGET_EXHAUSTED = "budget_exhausted"
NO_CRITERIA = "no_criteria"
GATE_REFUSED_ENDING = "gate_refused"
GATE_ERROR_ENDING = "gate_error"
GATE_PASSED_ENDING = "gate_passed"
TRUNCATED = "\n... gate output truncated to fit the prompt limit ...\n"
CLIP = 20_000
ERROR = "error"


class Sealing(Protocol):
    """What the judge reads of the checkpoint's packages (``PackageCheckpoint``)."""

    gate: GateRun | None
    sealed: Any
    gate_stand: str | None

    def output_copy(self, package: str, name: str) -> Any: ...

    def reported_done(self, package: str) -> bool: ...

    def producer_closed(self, package: str) -> bool: ...

    def new_pass(self) -> None: ...


def _clip(text: str, limit: int = CLIP) -> str:
    """``text`` cut to ``limit`` characters, saying so."""
    return text if len(text) <= limit else text[: max(limit - len(TRUNCATED), 0)] + TRUNCATED


class JevJudge:
    """The judge of one checkpoint (module docstring)."""

    def __init__(
        self, judge: PackageJudge, acceptance: Acceptance, workspace: str = "/app"
    ) -> None:
        self.criteria_ref = judge.criteria
        self.package, _, self.output = judge.criteria.partition(".")
        self.config = config_of(judge)
        self.acceptance = acceptance
        self.loop = JudgeLoop(self.config, judge.max_passes, judge.rework, acceptance, workspace)
        self.criteria_sha256: str | None = None
        self.ending: str | None = None
        self.gate_ending: str | None = None
        self.errors: list[str] = []

    @property
    def seconds(self) -> float:
        """How long the last judge pass took (the fit rule's reserve)."""
        return self.loop.seconds

    def start(self, checkpoint: Checkpoint) -> None:
        """Ready the asker, before the agent starts; an unusable judge is the instrument's."""
        try:
            self.loop.start(checkpoint)
        except ValueError as error:
            raise TrialInvalid("instrument", f"{PRIMITIVE}: {error}") from error

    def take_baseline(self, gate: GateRun | None) -> None:
        """The tests that passed on the base, from the gate's baseline run (none: none passed)."""
        found = None if gate is None else gate.evidence.baseline
        tests = found.get("tests") if found and found.get("status") != ERROR else None
        self.loop.baseline_passed = sorted(judging.passed_tests(tests))
        self.loop.baseline = None if gate is None else gate.record().get("baseline")

    def load_criteria(self, runtime: Sealing) -> bool:
        """Read the criteria the producer's ``done`` stop copied; fixed at the first valid one.

        A producer that reported ``done`` owes a valid copy: anything else is ``TrialInvalid``.
        """
        copy = runtime.output_copy(self.package, self.output)
        if copy is None:
            if runtime.reported_done(self.package):
                self._broken("the producer reported done but no valid criteria copy exists")
            return self.criteria_sha256 is not None
        digest = copy.get("sha256")
        if self.criteria_sha256 is not None:
            if digest != self.criteria_sha256:
                self._broken(f"changed after it was read: {digest}")
            return True
        try:
            data = Path(str(copy["host_path"])).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("the copy's bytes differ from its recorded digest")
            criteria = validate_criteria(json.loads(data.decode("utf-8")))
        except (OSError, ValueError) as error:
            self._broken(str(error))
        self.acceptance.load(criteria)
        self.criteria_sha256 = str(digest)
        return True

    def _broken(self, why: str) -> NoReturn:
        message = f"{self.criteria_ref}: {why}"
        self._error(message)
        raise TrialInvalid("integrity", f"{PRIMITIVE}: {message}")

    def _error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    async def after(self, checkpoint: Checkpoint, runtime: Sealing) -> Resume | None:
        """Judge the tree the checkpoint's gate just sealed; end the loop or send the open
        criteria back (``rework``). ``None`` when no seal stands or the judge has ended."""
        sealed, stand, gate = runtime.sealed, runtime.gate_stand, runtime.gate
        if sealed is None or stand is None or gate is None or self.ending is not None:
            return None
        if self.criteria_sha256 is None and runtime.producer_closed(self.package):
            self.gate_ending = {
                GATE_PASSED: GATE_PASSED_ENDING,
                GATE_ERROR: GATE_ERROR_ENDING,
            }.get(stand, GATE_REFUSED_ENDING)
            self.ending = NO_CRITERIA
            return None
        if stand == GATE_ERROR:
            self.gate_ending = self.ending = GATE_ERROR_ENDING
            return None
        if stand == GATE_BUDGET:
            self.gate_ending, self.ending = GATE_REFUSED_ENDING, BUDGET_EXHAUSTED
            await self.loop.judge_if_fits(checkpoint, gate.evidence.gates, sealed.record)
            return None
        return await self._judge(checkpoint, runtime)

    async def _judge(self, checkpoint: Checkpoint, runtime: Sealing) -> Resume | None:
        assert runtime.sealed is not None
        assert runtime.gate is not None
        stand = runtime.gate_stand
        loop, record, gates = self.loop, runtime.sealed.record, runtime.gate.evidence.gates
        self.gate_ending = GATE_PASSED_ENDING if stand == GATE_PASSED else GATE_REFUSED_ENDING
        left = checkpoint.remaining()
        if left is not None and left <= 0:
            self.ending = DEADLINE
            return None
        result, changed = await loop.judge_pass(checkpoint, gates, record)
        if self.gate_ending == GATE_REFUSED_ENDING:
            self.ending = GATE_REFUSED_ENDING
            return None
        ended = loop.stop(changed)
        if ended is None and not loop.fits(checkpoint, REWORK, gates):
            ended = BUDGET_EXHAUSTED
        if ended is not None:
            self.ending = ended
            return None
        runtime.new_pass()  # ``max_fix_rounds`` bounds each pass's fix turns
        return loop.rework(result, _clip(str(record.get("output") or "(no output)")))

    def close(self, cut: str | None) -> None:
        """A cut ends a checkpoint the judge had not ended."""
        if cut is not None and self.ending is None:
            self.ending = cut

    def record(self) -> dict[str, Any]:
        """The primitive's evidence (module docstring)."""
        return {
            "criteria": self.criteria_ref,
            "criteria_sha256": self.criteria_sha256,
            "model": self.config.model,
            "ending": self.ending,
            "gate_ending": self.gate_ending,
            "errors": self.errors,
            **self.loop.record(),
        }
