"""``workflow`` built-in: transient-retry@1, a bounded retry of a session the provider dropped.

When a session of the arm's agent fails with a transient provider error
(Harbor's ``ApiOverloadedError``, ``ApiConnectionClosedError``,
``ApiResponseStalledError``, ``ApiInternalServerError``, ``ApiRateLimitError``),
the wrapper (``raidar.subject.treatment.runtime.transient_retry:TransientRetryAgent``) waits
and starts it again, at most twice (60 s, then 180 s), with a fixed preface:
the earlier session was cut off by the provider and the workspace keeps what
it changed. Any other error, and the last retry's error, is raised unchanged.

It is a *session* wrapper (``raidar.subject.treatment.runtime.session_chain``): put it after
``continue-on-stall`` in ``shared_components`` so it sits closest to the
native agent (every session, a stall continuation included, is retried the
same way) and applies identically to every arm. It takes no ``with`` keys and
no files; nothing in it names a problem.

The discovery probe requires at least one transient-retry record in the
step's agent logs (one is written for every session, retried or not).
"""

from __future__ import annotations

import json

from raidar.subject.treatment.catalog.builtin_definition import (
    BOTH_HARNESSES,
    BUILTIN_FINGERPRINT,
    require_file_count,
    require_with_keys,
)
from raidar.subject.treatment.definition.contribution import AgentWrapper, Contribution
from raidar.subject.treatment.definition.evidence import StepEvidence
from raidar.subject.treatment.definition.manifest import (
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)
from raidar.subject.treatment.runtime.transient_retry import COMPONENT, RETRY_DIR, RETRY_RECORD

TRANSIENT_RETRY_IMPORT_PATH = "raidar.subject.treatment.runtime.transient_retry:TransientRetryAgent"


class TransientRetry:
    manifest = ComponentManifest(
        name="transient-retry",
        version="1",
        kind=ComponentKind.WORKFLOW,
        description=(
            "When a session of the arm's agent fails with a transient model-provider error "
            "(overloaded, connection closed, stalled response, internal error, rate limit), "
            "wait and run it again, at most twice, telling the agent the workspace keeps its "
            "changes"
        ),
        supports=BOTH_HARNESSES,
        fingerprint=(
            *BUILTIN_FINGERPRINT,
            "orchestrator/src/raidar/subject/treatment/catalog/retry.py",
            "orchestrator/src/raidar/subject/treatment/runtime/transient_retry.py",
            # the per-attempt run record
            "orchestrator/src/raidar/subject/treatment/runtime/continue_on_stall.py",
            "orchestrator/src/raidar/contract/snapshot.py",  # imported by continue_on_stall
            "orchestrator/src/raidar/subject/treatment/runtime/session_chain.py",
            # usage sums, prompt budget
            "orchestrator/src/raidar/subject/treatment/runtime/review_loop.py",
        ),
        discovery_probe=DiscoveryProbe(
            id="transient-retry-recorded",
            description=(
                "The step's agent logs hold a transient-retry record for its sessions "
                "(retried or not)"
            ),
            evidence=(f"{RETRY_DIR}/{RETRY_RECORD}",),
        ),
    )

    def _validate(self, use: ComponentUseContext) -> None:
        require_with_keys(use, allowed=())
        require_file_count(use, minimum=0, maximum=0)

    def contribute(self, use: ComponentUseContext) -> Contribution:
        self._validate(use)
        return Contribution()

    def session_wrapper(self, use: ComponentUseContext) -> AgentWrapper:
        self._validate(use)
        return AgentWrapper(
            import_path=TRANSIENT_RETRY_IMPORT_PATH, kwargs={"component": COMPONENT}
        )

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del step_index
        self._validate(use)
        records = retry_records(evidence)
        if not records:
            return ProbeOutcome(False, "no transient-retry record in this step's logs")
        retries = sum(_count(record.get("retries")) for record in records)
        recovered = sum(1 for record in records if record.get("recovered") is True)
        return ProbeOutcome(
            True,
            f"{len(records)} session(s) recorded; {retries} retr(ies), {recovered} recovered",
            tuple(
                f"retries={record.get('retries')} recovered={record.get('recovered')}"
                for record in records
            ),
        )


def _count(value: object) -> int:
    return value if isinstance(value, int) else 0


def retry_records(evidence: StepEvidence) -> list[dict[str, object]]:
    """Every transient-retry record in a step's agent logs, in path order."""
    found: list[dict[str, object]] = []
    for path in sorted(evidence.agent_dir.rglob(RETRY_RECORD)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            found.append(payload)
    return found


TRANSIENT_RETRY = TransientRetry()
