"""Run portable parse/refusal/digest vectors: python -m raidar_contract.conformance.check."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from raidar_contract.canonical import digest_json
from raidar_contract.criteria import validate_criteria
from raidar_contract.package.digest import read_stage_tree, stage_tree_digest
from raidar_contract.package.manifest import parse_manifest
from raidar_contract.releases import Release
from raidar_contract.workflow import PackageWorkflowDefinition, package_workflow_digest


def _manifest(case: dict[str, Any]) -> dict[str, Any]:
    value = parse_manifest(yaml.safe_dump(case["input"])).model_dump(mode="json")
    return {"value": value, "digest": digest_json(value)}


def _workflow(case: dict[str, Any]) -> dict[str, Any]:
    definition = PackageWorkflowDefinition.model_validate(case["input"])
    return {
        "value": definition.to_json(),
        "digest": definition.digest,
        "packages_digest": package_workflow_digest(
            definition, case.get("packages", [])
        ),
    }


def _criteria(case: dict[str, Any]) -> dict[str, Any]:
    value = validate_criteria(case["input"]).model_dump(mode="json")
    return {"value": value, "digest": digest_json(value)}


def _reference(case: dict[str, Any]) -> dict[str, Any]:
    parser = {
        "stage": Release.parse_stage,
        "workflow": Release.parse_workflow,
        "agent": Release.parse,
    }
    release = parser[case["reference_kind"]](case["input"])
    return {
        "value": {
            "name": release.name,
            "version": release.version,
            "text": str(release),
        }
    }


def _stop(case: dict[str, Any]) -> dict[str, Any]:
    from raidar_contract.call import parse_stop_result

    raw = (
        bytes.fromhex(case["raw_hex"])
        if "raw_hex" in case
        else json.dumps(case["input"]).encode()
    )
    value = asdict(parse_stop_result(raw, tuple(case.get("declared", []))))
    return {"value": value, "digest": digest_json(value)}


def _stop_step(case: dict[str, Any]) -> dict[str, Any]:
    from raidar_contract.call import judge_stop_result, parse_stop_result

    result = parse_stop_result(json.dumps(case["input"]).encode(), ())
    return {"value": asdict(judge_stop_result(result, **case["state"]))}


def _context(case: dict[str, Any]) -> dict[str, Any]:
    from raidar_contract.call import CallContext

    value = CallContext.model_validate(case["input"]).document()
    return {"value": value, "digest": digest_json(value)}


def _tree(case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="raidar-conformance-") as directory:
        root = Path(directory)
        for entry in case["input"]:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(
                    "conformance file paths must be relative and cannot traverse"
                )
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if "symlink" in entry:
                path.symlink_to(entry["symlink"])
            else:
                path.write_bytes(bytes.fromhex(entry["hex"]))
                path.chmod(0o755 if entry.get("executable") else 0o644)
        files = read_stage_tree(root)
        return {
            "value": [[item.path, item.sha256, item.executable] for item in files],
            "digest": stage_tree_digest(files),
        }


PARSERS = {
    "manifest": _manifest,
    "workflow": _workflow,
    "criteria": _criteria,
    "reference": _reference,
    "stop": _stop,
    "stop-step": _stop_step,
    "context": _context,
    "stage-tree": _tree,
}


def check_case(case: dict[str, Any]) -> None:
    """Require the expected normalized value/digests or semantic refusal for one vector."""
    expected = case["expected"]
    try:
        actual = PARSERS[case["kind"]](case)
    except ValueError as error:
        if "refusal" not in expected or expected["refusal"] not in str(error):
            raise AssertionError(
                f"{case['id']}: unexpected refusal: {error}"
            ) from error
    else:
        if actual != expected:
            raise AssertionError(f"{case['id']}: expected {expected!r}, got {actual!r}")


def main() -> None:
    cases = json.loads(Path(__file__).with_name("cases.json").read_text())
    for case in cases:
        check_case(case)
    print(f"{len(cases)} conformance cases passed")


if __name__ == "__main__":
    main()
