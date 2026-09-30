"""``raidar problem import``: pinned SCB problem -> Raidar task + oracle bundle.

Import is reproducible: the manifest records git tree ids of the problem and
the converter at the pinned commit plus content digests of everything
generated. Re-importing a problem whose manifest is committed must reproduce
those digests exactly; a mismatch is refused as drift unless ``update`` is set.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from raidar.oracle.bundle import OracleBundle
from raidar.packs.python import PackSpec, image_tag, load_pack_spec
from raidar.problems.content import content_digest, hidden_content_in_task
from raidar.problems.models import (
    PackBinding,
    ProblemId,
    ProblemManifest,
    ProblemSource,
    ProblemStep,
)
from raidar.problems.rewrite import (
    build_bundle,
    converted_steps,
    copy_references,
    render_task,
    select_steps,
)
from raidar.problems.source import PinnedCheckout, convert_problem, ensure_checkout, open_checkout
from raidar.problems.store import ProblemStore, ProblemStoreError

# The converter's README embeds the conversion date; it is not task content.
_CONVERTER_README = "README.md"
_CONVERSION_OVERRIDES = "conversion.toml"
_PROBLEM_CONFIG = "config.yaml"


class ProblemDriftError(ProblemStoreError):
    """A re-import produced content that differs from the committed manifest."""


@dataclass(frozen=True)
class ImportResult:
    manifest: ProblemManifest
    manifest_path: Path
    content_dir: Path
    changed: bool


def resolve_checkout(
    store: ProblemStore, problem: ProblemId, source_checkout: Path | None
) -> PinnedCheckout:
    pin = store.pin(problem.dataset)
    if pin.version != problem.version:
        raise ProblemStoreError(
            f"{problem} does not match the {problem.dataset} pin {pin.commit} "
            f"(expected version {pin.version})"
        )
    if source_checkout is not None:
        return open_checkout(source_checkout, pin)
    return ensure_checkout(store.cache_dir(), problem.dataset, pin)


def _manifest(
    problem: ProblemId,
    checkout: PinnedCheckout,
    materialised: _Materialised,
    pack: PackSpec,
) -> ProblemManifest:
    content = materialised.content
    steps = tuple(
        ProblemStep(
            name=step.name,
            checkpoint=step.checkpoint,
            instruction_sha256=hashlib.sha256(
                (content / "task" / "steps" / step.name / "instruction.md").read_bytes()
            ).hexdigest(),
            test_files=step.test_files,
        )
        for step in materialised.bundle.manifest.steps
    )
    return ProblemManifest(
        ref=str(problem),
        mode=problem.mode,
        checkpoints=len(steps),
        steps=steps,
        source=ProblemSource(
            dataset=problem.dataset,
            url=checkout.pin.url,
            commit=checkout.pin.commit,
            problem=problem.problem,
            checkpoint_selection=problem.checkpoint,
            problem_tree=checkout.tree_id(problem.problem),
            converter_tree=checkout.tree_id(checkout.converter_dir),
            conversion_overrides=checkout.optional_tree_id(_CONVERSION_OVERRIDES),
            converted_task_sha256=materialised.converted_sha256,
        ),
        pack=PackBinding(id=pack.id, version=pack.version, image=image_tag(pack)),
        task_sha256=content_digest(content / "task"),
        bundle_sha256=content_digest(content / "oracle"),
        references_sha256=content_digest(content / "references"),
        entry_file=materialised.bundle.manifest.entry_file,
    )


def problem_entry_file(checkout: PinnedCheckout, problem: ProblemId, pack: PackSpec) -> str | None:
    """The problem's ``config.yaml`` ``entry_file`` in the pack's snapshot-file format."""
    config = yaml.safe_load((checkout.root / problem.problem / _PROBLEM_CONFIG).read_text())
    raw = config.get("entry_file") if isinstance(config, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ProblemStoreError(f"{problem.problem}/{_PROBLEM_CONFIG}: entry_file must be a string")
    try:
        return pack.entry_file(raw)
    except ValueError as exc:
        raise ProblemStoreError(f"{problem.problem}/{_PROBLEM_CONFIG}: {exc}") from exc


@dataclass(frozen=True)
class _Materialised:
    content: Path
    bundle: OracleBundle
    converted_sha256: str


def _materialise(
    checkout: PinnedCheckout, problem: ProblemId, staging: Path, pack: PackSpec
) -> _Materialised:
    converted = convert_problem(checkout, problem.problem, staging / "converted")
    (converted / _CONVERTER_README).unlink(missing_ok=True)
    converted_sha256 = content_digest(converted)
    steps = select_steps(converted_steps(converted), problem.checkpoint)
    content = staging / "content"
    render_task(converted, content / "task", problem=problem, steps=steps, pack=pack)
    bundle = build_bundle(
        converted,
        content / "oracle",
        problem=problem,
        steps=steps,
        pack_image=image_tag(pack),
        source={
            "dataset": problem.dataset,
            "commit": checkout.pin.commit,
            "problem": problem.problem,
        },
        entry_file=problem_entry_file(checkout, problem, pack),
    )
    copy_references(converted, content / "references", steps)
    leaked = hidden_content_in_task(content / "task", bundle)
    if leaked:
        raise ProblemStoreError(f"hidden oracle content in the agent task: {leaked}")
    return _Materialised(content=content, bundle=bundle, converted_sha256=converted_sha256)


def _drift(recorded: ProblemManifest, produced: ProblemManifest) -> list[str]:
    old, new = recorded.model_dump(mode="json"), produced.model_dump(mode="json")
    return sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))


def _recorded(store: ProblemStore, problem: ProblemId) -> ProblemManifest | None:
    return store.load_manifest(problem) if store.manifest_path(problem).is_file() else None


def _replace(target: Path, content: Path) -> None:
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(content), target)


def _content_matches(store: ProblemStore, problem: ProblemId) -> bool:
    """Materialised content already matches the manifest (left in place for live readers)."""
    try:
        store.load(problem)
    except (ProblemStoreError, ValueError):
        return False
    return True


def import_problem(
    store: ProblemStore,
    problem: ProblemId,
    *,
    source_checkout: Path | None = None,
    update: bool = False,
    pack: PackSpec | None = None,
) -> ImportResult:
    """Import (or re-materialise) one problem into the store."""
    pack = pack or load_pack_spec()
    checkout = resolve_checkout(store, problem, source_checkout)
    checkout.verify_clean(problem.problem, checkout.converter_dir, _CONVERSION_OVERRIDES)
    if not (checkout.root / problem.problem / _PROBLEM_CONFIG).is_file():
        raise ProblemStoreError(f"{problem.problem} is not an SCB problem at {checkout.pin.commit}")
    recorded = _recorded(store, problem)
    with tempfile.TemporaryDirectory(prefix="raidar-problem-import-") as tmp:
        staging = Path(tmp)
        materialised = _materialise(checkout, problem, staging, pack)
        manifest = _manifest(problem, checkout, materialised, pack)
        drift = _drift(recorded, manifest) if recorded is not None else []
        if drift and not update:
            raise ProblemDriftError(
                f"re-import of {problem} differs from its committed manifest in {drift}; "
                "pass --update to accept the new content"
            )
        target = store.content_dir(problem)
        if recorded is None or drift or not _content_matches(store, problem):
            _replace(target, staging / "content")
    path = store.write_manifest(manifest)
    return ImportResult(
        manifest=manifest,
        manifest_path=path,
        content_dir=target,
        changed=recorded is None or bool(drift),
    )
