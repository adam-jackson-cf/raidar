"""``protocol.verifier_coverage``: where the verifier's per-test coverage pass runs.

Left at ``trial`` it is omitted from every dump, so the locks and plans of
experiments that do not set it keep their bytes; ``offline`` is part of the
experiment lock. ``code-quality`` reads the deferred pass from the store.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from raidar.design.experiment.schema import ExperimentSpec, ProtocolSpec
from tests.experiment_spec_support import experiment_document


def test_the_default_is_omitted_from_every_dump_and_offline_is_kept() -> None:
    assert "verifier_coverage" not in ProtocolSpec().model_dump(mode="json")
    offline = ProtocolSpec(verifier_coverage="offline")
    assert offline.model_dump(mode="json")["verifier_coverage"] == "offline"
    assert ProtocolSpec.model_validate(offline.model_dump(mode="json")) == offline


@pytest.mark.parametrize("mode", [None, "trial", "offline"])
@pytest.mark.parametrize("scorers", [("correctness@10",), ("correctness@10", "code-quality@9")])
def test_experiment_identity_records_only_deferred_coverage(
    mode: str | None, scorers: tuple[str, ...]
) -> None:
    protocol = {} if mode is None else {"verifier_coverage": mode}
    spec = ExperimentSpec.model_validate(
        experiment_document(protocol=protocol, assessment={"scorers": scorers})
    )
    serialized = spec.to_json()
    recorded = serialized["protocol"]
    if mode == "offline":
        assert recorded["verifier_coverage"] == "offline"
    else:
        assert "verifier_coverage" not in recorded
    assert ExperimentSpec.model_validate(serialized) == spec


@pytest.mark.parametrize("mode", ["", "disabled", None, 1])
def test_unknown_coverage_modes_are_refused(mode: object) -> None:
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate({"verifier_coverage": mode})
