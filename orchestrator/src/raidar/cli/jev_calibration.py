"""Jev calibration commands: labels (direct and blind), supersessions, recorded splits,
promotion and its verification.

Attached to the ``raidar jev`` group by ``raidar.cli.jev``.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from raidar.scoring.framework.catalog import default_scorer_registry
from raidar.scoring.judge.audit import ReviewerKind
from raidar.scoring.judge.blind import derive_blind_labels, export_blind
from raidar.scoring.judge.calibration import (
    LabelImportError,
    import_labels,
    promotion_readiness,
    record_split,
    supersede_labels,
)
from raidar.scoring.judge.ledger import SplitRecord, recorded_split
from raidar.scoring.judge.policies import POLICY_DIR, CalibrationSplit, PolicyError
from raidar.scoring.judge.promotion import (
    PromotionError,
    PromotionPlan,
    plan_promotion,
    scorer_id,
    verify_promotion,
    write_promotion,
)
from raidar.scoring.judge.session import open_runtime


def _version_key(version: str) -> list[object]:
    return [(0, int(part)) if part.isdigit() else (1, part) for part in version.split(".")]


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


_REVIEWER = click.option(
    "--reviewer",
    type=click.Choice([kind.value for kind in ReviewerKind]),
    default=ReviewerKind.HUMAN.value,
    show_default=True,
    help="Reviewer kind for rows that do not name one.",
)
_REVIEWER_MODEL = click.option(
    "--reviewer-model", help="Model id of a model reviewer (required with --reviewer model)."
)
_STORE = click.option(
    "--store", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True
)


@click.command("import-labels")
@_STORE
@click.option("--policy", required=True)
@_REVIEWER
@_REVIEWER_MODEL
@click.argument("labels", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def jev_import_labels(
    store: Path, policy: str, reviewer: str, reviewer_model: str | None, labels: Path
) -> None:
    """Import labels (JSON lines: request_id, label, reviewer, note) and show readiness."""
    runtime = open_runtime(store, replay_only=True)
    defaults = {"reviewer": reviewer, "reviewer_model": reviewer_model}
    try:
        runtime.policies.get(policy)
        rows = [
            {**{k: v for k, v in defaults.items() if v is not None}, **row}
            for row in _read_jsonl(labels)
        ]
        added = import_labels(runtime.audit, policy, rows)
    except (PolicyError, LabelImportError, json.JSONDecodeError) as error:
        raise click.ClickException(str(error)) from error
    readiness = promotion_readiness(runtime.audit, policy)
    click.echo(json.dumps({"imported": added, **readiness.summary()}, indent=2))


@click.command("export-blind")
@_STORE
@click.option("--policy", required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--trial", "trials", multiple=True, help="Only decisions of this trial; repeatable.")
@click.option("--limit", type=click.IntRange(min=1), help="At most this many decisions.")
@click.option(
    "--request-id",
    "request_ids",
    multiple=True,
    help="Exactly this selected decision (repeatable); refused unless unlabelled and answered.",
)
def jev_export_blind(
    store: Path,
    policy: str,
    output: Path,
    trials: tuple[str, ...],
    limit: int | None,
    request_ids: tuple[str, ...],
) -> None:
    """Export unlabelled answered decisions WITHOUT Jev's answers, for a blind labeller."""
    runtime = open_runtime(store, replay_only=True)
    try:
        runtime.policies.get(policy)
        rows = export_blind(
            runtime.audit,
            policy,
            trials=set(trials) or None,
            limit=limit,
            request_ids=set(request_ids) or None,
        )
    except (PolicyError, LabelImportError) as error:
        raise click.ClickException(str(error)) from error
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    click.echo(json.dumps({"policy": policy, "exported": len(rows), "output": str(output)}))


@click.command("import-blind")
@_STORE
@click.option("--policy", required=True)
@_REVIEWER
@_REVIEWER_MODEL
@click.argument("answers", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def jev_import_blind(
    store: Path, policy: str, reviewer: str, reviewer_model: str | None, answers: Path
) -> None:
    """Derive labels from a blind labeller's answers (JSON lines) and import them."""
    runtime = open_runtime(store, replay_only=True)
    try:
        runtime.policies.get(policy)
        rows = derive_blind_labels(
            runtime.audit,
            policy,
            _read_jsonl(answers),
            reviewer=ReviewerKind(reviewer),
            reviewer_model=reviewer_model,
        )
        added = import_labels(runtime.audit, policy, rows)
    except (PolicyError, LabelImportError, json.JSONDecodeError) as error:
        raise click.ClickException(str(error)) from error
    derived: dict[str, int] = {}
    for row in rows:
        derived[str(row["label"])] = derived.get(str(row["label"]), 0) + 1
    readiness = promotion_readiness(runtime.audit, policy)
    report = {"imported": added, "derived": dict(sorted(derived.items())), **readiness.summary()}
    click.echo(json.dumps(report, indent=2))


@click.command("supersede-labels")
@_STORE
@click.option("--policy", required=True)
@click.option("--reason", required=True, help="Why these labels no longer count.")
@click.option("--request-id", "request_ids", multiple=True, help="Label to supersede; repeatable.")
@click.option("--all-current", is_flag=True, help="Supersede every current label of the policy.")
def jev_supersede_labels(
    store: Path, policy: str, reason: str, request_ids: tuple[str, ...], all_current: bool
) -> None:
    """Take labels out of calibration without deleting them, and show readiness."""
    if bool(request_ids) == all_current:
        raise click.ClickException("name labels with --request-id or pass --all-current (one)")
    runtime = open_runtime(store, replay_only=True)
    try:
        runtime.policies.get(policy)
        records = supersede_labels(
            runtime.audit, policy, reason=reason, request_ids=request_ids or None
        )
    except (PolicyError, LabelImportError) as error:
        raise click.ClickException(str(error)) from error
    readiness = promotion_readiness(runtime.audit, policy)
    click.echo(json.dumps({"superseded": len(records), **readiness.summary()}, indent=2))


@click.command("record-split")
@_STORE
@click.option("--policy", required=True)
@click.option("--split-id", required=True, help="Name of this split, unique per policy.")
@click.option(
    "--calibration", required=True, help="Calibration trial ids (space or comma separated)."
)
@click.option(
    "--evaluation", required=True, help="Evaluation trial ids (space or comma separated)."
)
@click.option("--reason", required=True, help="How and why the split was chosen.")
def jev_record_split(
    store: Path, policy: str, split_id: str, calibration: str, evaluation: str, reason: str
) -> None:
    """Record a calibration/evaluation split of trials before labelling against it."""
    runtime = open_runtime(store, replay_only=True)
    try:
        runtime.policies.get(policy)
        record = record_split(
            runtime.audit,
            policy,
            split_id=split_id,
            calibration=_trial_list(calibration),
            evaluation=_trial_list(evaluation),
            reason=reason,
        )
    except (PolicyError, LabelImportError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(record.model_dump(mode="json"), indent=2))


def scorer_versions() -> dict[str, str]:
    """Each registered scorer's highest version (``id -> version``)."""
    versions: dict[str, list[str]] = {}
    for ref in default_scorer_registry().refs():
        name, version = ref.split("@", 1)
        versions.setdefault(name, []).append(version)
    return {name: max(found, key=_version_key) for name, found in versions.items()}


_POLICY_DIR = click.option(
    "--policy-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=POLICY_DIR,
    show_default=True,
    help="Directory of versioned Jev policy files.",
)


@click.command("verify-promotion")
@_STORE
@click.option("--policy", required=True)
@_POLICY_DIR
def jev_verify_promotion(store: Path, policy: str, policy_dir: Path) -> None:
    """Check a policy's recorded promotion against the store's audit, labels and scorers."""
    runtime = open_runtime(store, replay_only=True, policy_dir=policy_dir)
    try:
        failures = verify_promotion(
            runtime.policies.get(policy), runtime.audit, scorer_versions=scorer_versions()
        )
    except PolicyError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps({"policy": policy, "supported": not failures, "failures": failures}))
    if failures:
        raise SystemExit(1)


def _trial_list(raw: str | None) -> tuple[str, ...]:
    return tuple(item for item in (raw or "").replace(",", " ").split() if item)


def _split(
    calibration: str | None, evaluation: str | None, recorded: SplitRecord | None
) -> CalibrationSplit | None:
    """The explicit split, else the policy's latest recorded one, else None (default split)."""
    if not (calibration or evaluation):
        if recorded is None:
            return None
        return CalibrationSplit(calibration=recorded.calibration, evaluation=recorded.evaluation)
    return CalibrationSplit(
        calibration=_trial_list(calibration), evaluation=_trial_list(evaluation)
    )


def _write(path: Path, plan: PromotionPlan) -> str:
    try:
        write_promotion(path, plan)
    except PromotionError as error:
        raise click.ClickException(str(error)) from error
    return str(path)


@click.command("promote")
@_STORE
@click.option("--policy", required=True)
@click.option(
    "--calibration",
    help="Calibration trial ids (space or comma separated); default: the recorded split.",
)
@click.option(
    "--evaluation",
    help="Evaluation trial ids (space or comma separated); default: the recorded split.",
)
@click.option("--dry-run", is_flag=True, help="Report the promotion without writing the policy.")
@click.option(
    "--allow-pending-scorer-bump",
    is_flag=True,
    help=(
        "Write the enforce policy even though its scorer still needs the minor bump the "
        "promotion ships with; `jev verify-promotion` fails until that bump lands."
    ),
)
@_POLICY_DIR
def jev_promote(
    store: Path,
    policy: str,
    calibration: str | None,
    evaluation: str | None,
    dry_run: bool,
    allow_pending_scorer_bump: bool,
    policy_dir: Path,
) -> None:
    """Fit and freeze the threshold, then write the enforce promotion if it verifies.

    A promotion ships with the next minor version of its scorer. While that bump is
    still pending, writing ``mode: enforce`` would leave a repo policy that
    ``jev verify-promotion`` rejects, so the write is refused unless
    ``--allow-pending-scorer-bump`` is given.
    """
    runtime = open_runtime(store, replay_only=True, policy_dir=policy_dir)
    try:
        current = runtime.policies.get(policy)
        split = _split(calibration, evaluation, recorded_split(runtime.audit, policy))
    except (PolicyError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    version = scorer_versions().get(scorer_id(policy))
    plan = plan_promotion(current, runtime.audit, split=split, scorer_version=version)
    blocked = plan.ok and plan.scorer_bump is not None and not allow_pending_scorer_bump
    write = plan.ok and not dry_run and not blocked
    written = _write(policy_dir / f"{policy}.yaml", plan) if write else None
    report = {**plan.summary(), "written": written, "blocked_on_scorer_bump": blocked}
    click.echo(json.dumps(report, indent=2))
    if not plan.ok or (blocked and not dry_run):
        raise SystemExit(1)


def register(group: click.Group) -> None:
    for command in (
        jev_import_labels,
        jev_export_blind,
        jev_import_blind,
        jev_supersede_labels,
        jev_record_split,
        jev_verify_promotion,
        jev_promote,
    ):
        group.add_command(command)
