"""``protocol.verifier_coverage``: where the verifier's per-test coverage pass runs.

Left at ``trial`` it is omitted from every dump, so the locks and plans of
experiments that do not set it keep their bytes; ``offline`` is part of the
execution identity. An experiment that defers the pass must not declare a
scorer whose G reads only the trial's own pass (``code-quality@1``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from raidar.spec.loader import load_experiment
from raidar.spec.schema import ProtocolSpec
from raidar.spec.validation import SpecValidationError
from tests.experiment_spec_support import (
    AGENTS_BENCHMARK,
    MECHANICS_SMOKE,
    SMOKE_EXPERIMENT,
    experiment_document,
    write_experiment,
)


def test_the_default_is_omitted_from_every_dump_and_offline_is_kept() -> None:
    assert "verifier_coverage" not in ProtocolSpec().model_dump(mode="json")
    offline = ProtocolSpec(verifier_coverage="offline")
    assert offline.model_dump(mode="json")["verifier_coverage"] == "offline"
    assert ProtocolSpec.model_validate(offline.model_dump(mode="json")) == offline


def test_committed_experiments_dump_it_only_when_they_set_it(tmp_path: Path) -> None:
    for path in (MECHANICS_SMOKE, SMOKE_EXPERIMENT, AGENTS_BENCHMARK):
        assert "verifier_coverage" not in load_experiment(path).spec.to_json()["protocol"]

    document = experiment_document(
        protocol={"verifier_coverage": "offline"}, assessment={"scorers": ["correctness@1"]}
    )
    loaded = load_experiment(write_experiment(tmp_path, document))
    assert loaded.spec.to_json()["protocol"]["verifier_coverage"] == "offline"


@pytest.mark.parametrize(
    ("scorer", "refused"), [("code-quality@1", True), ("code-quality@2", False)]
)
def test_a_deferred_pass_needs_a_scorer_that_reads_the_offline_run(
    tmp_path: Path, scorer: str, refused: bool
) -> None:
    document = experiment_document(
        protocol={"verifier_coverage": "offline"},
        assessment={"scorers": ["correctness@1", scorer]},
    )
    path = write_experiment(tmp_path, document)

    if not refused:
        assert load_experiment(path).spec.protocol.verifier_coverage == "offline"
        return
    with pytest.raises(SpecValidationError) as caught:
        load_experiment(path)
    issues = {issue.loc: issue.message for issue in caught.value.issues}
    assert "declare code-quality@2" in issues["assessment.scorers"]
