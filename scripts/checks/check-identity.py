#!/usr/bin/env python3
"""Record and enforce the repository's execution identities without refreshing implicitly."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = ".omp/identity.json"


def compare_identities(
    recorded: Mapping[str, str], current: Mapping[str, str], *, record_path: str
) -> list[str]:
    remedy = (
        f"declare the identity change: update {record_path} in its own reviewed commit "
        "with before/after values and schedule revalidation/relock"
    )
    return [
        f"{key}: {recorded.get(key, '<missing>')} -> {current.get(key, '<missing>')}; {remedy}"
        for key in sorted(recorded.keys() | current.keys())
        if recorded.get(key) != current.get(key)
    ]


def frozen_exclusions(
    released: Mapping[str, str], current: Mapping[str, str], payload_paths: Sequence[str]
) -> list[str]:
    return sorted(
        set(payload_paths)
        | {folder for folder, digest in released.items() if current.get(folder) == digest}
    )


def current_identities() -> dict[str, str]:
    """Discover every registered surface using its authoritative digest implementation."""
    from raidar.contract.releases import ReleaseKind, load_register
    from raidar.design.experiment.lock import spec_source_digest
    from raidar.grading.records import grading_execution_digest
    from raidar.scoring.catalog.builtin import default_scorer_registry
    from raidar.subject.primitive.registry import PRIMITIVE_MODULES, WORKFLOW_PRIMITIVE_MODULES
    from raidar.subject.workflow.identity import ADAPTER_ENTRIES, primitive_code
    from raidar_contract.package.digest import read_stage_tree, stage_tree_digest

    identities = {
        "grading_execution": grading_execution_digest(),
        "raidar_spec": spec_source_digest(),
    }
    contracts = PRIMITIVE_MODULES.keys() | WORKFLOW_PRIMITIVE_MODULES.keys()
    for adapter in ADAPTER_ENTRIES:
        for contract, digest in primitive_code(contracts, adapter).items():
            identities[f"primitive_code/{adapter.value}/{contract}"] = digest
    registry = default_scorer_registry()
    for ref in registry.refs():
        digest = registry.get(ref).implementation_digest()
        if digest is None:
            raise ValueError(f"registered scorer {ref} has no implementation digest")
        identities[f"scorer/{ref}"] = digest
    register = load_register(ROOT / "lab/releases.yaml")
    for name in sorted(register.names(ReleaseKind.STAGE)):
        identities[f"stage/{name}"] = stage_tree_digest(
            read_stage_tree(ROOT / "lab/stages" / name)
        )
    return dict(sorted(identities.items()))


def read_record(path: Path) -> dict[str, str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or set(document) != {"schemaVersion", "identities"}
        or type(document["schemaVersion"]) is not int
        or document["schemaVersion"] != 1
        or not isinstance(document["identities"], dict)
        or not document["identities"]
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for key, value in document["identities"].items()
        )
    ):
        raise ValueError(f"{path}: invalid identity record")
    return document["identities"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", default=RECORD)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    path = ROOT / args.record
    try:
        current = current_identities()
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"schemaVersion": 1, "identities": current}, indent=2) + "\n",
                encoding="utf-8",
            )
            return 0
        lines = compare_identities(read_record(path), current, record_path=args.record)
    except Exception as exc:
        print(f"identity-check: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line, file=sys.stderr)
    return int(bool(lines))


if __name__ == "__main__":
    sys.exit(main())
