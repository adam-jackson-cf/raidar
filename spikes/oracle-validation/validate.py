"""Oracle validation spike for SCB pilot problems (reboot plan task 0.5).

For one SCB problem this harness checks, with no LLM involved:

1. positive   - reference solution k passes every test of checkpoints 1..k
                (Harbor ``oracle`` agent, strict all-cases, continue mode);
2. negative   - reference k-1 (empty starter for k=1) fails checkpoint k;
3. determinism - ``--repeats`` Harbor trials of each variant produce identical
                per-test outcomes;
4. parity     - SCB's native runner (``slop-code eval-snapshot``) grades the
                same snapshots exactly like the Harbor port, test by test.

A problem can be validated as a multi-checkpoint trajectory (all checkpoints)
or as a one-shot task (``--oneshot-checkpoint N``: a single-step Harbor task
whose starter is empty).

Usage (from the repo root; Docker via OrbStack only)::

    orchestrator/.venv/bin/python spikes/oracle-validation/validate.py \
        --problem forge --repeats 3
    orchestrator/.venv/bin/python spikes/oracle-validation/validate.py \
        --problem circuit_eval --oneshot-checkpoint 1 --repeats 3

Outputs land in ``spikes/oracle-validation/_work/<label>/`` and the verdict
is written to ``_work/<label>/summary.json``. Exit code 0 means the problem
validated, 1 means it failed a check, 2 means an infrastructure error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
DEFAULT_WORK = HERE / "_work"
DEFAULT_HARBOR = REPO_ROOT / "orchestrator" / ".venv" / "bin" / "harbor"
DEFAULT_SCB_RUNNER = Path("/Users/adamjackson/Projects/inflight/slop-code-bench")

# Pinned SCB problem source: upstream HEAD after PR #27 (held-out test leak
# fix). forge/ and circuit_eval/ are byte-identical to ef6a9dd.
SCB_PROBLEMS_URL = "https://github.com/gabeorlanski/scb-problems"
SCB_PROBLEMS_SHA = "38d627ecf668a88f88f8d260f8df8df6116e9b03"

# Evaluator lock the SCB fork's native runner requires per problem, per the
# fork's docs/evaluation/configuration.md convention.
NATIVE_EVALUATOR_DEPS = [
    "coverage==7.15.4",
    "pytest==8.4.1",
    "pytest-json-ctrf==0.3.5",
    "pytest-json-report==1.5.0",
    "pytest-timeout==2.4.0",
    "ruff==0.8.6",
]
NATIVE_ENV_CONFIG = "configs/environments/docker-python3.12-uv.yaml"

GROUPS = ("core", "functionality", "error", "regression")
# Observation-only hook added to each generated test.sh: keep pytest's JSON
# report (the converter's EXIT trap deletes it) so per-test ids survive intact.
REPORT_HOOK = (
    "PYTEST_RC=$?\n"
    "cp /tmp/pytest-report.json /logs/verifier/pytest-report.json 2>/dev/null || true\n"
)
CKPT_IN_FILE = re.compile(r"test_(checkpoint_\d+)\.py")
MIN_REWARD_LINE = re.compile(r"^min_reward = .*$", re.MULTILINE)


class InfraError(RuntimeError):
    """Raised when tooling fails, as opposed to a validation check failing."""


@dataclass
class Grade:
    """One grading of one snapshot against one checkpoint's test selection."""

    source: str
    checkpoint: str
    groups: dict[str, list[int]]
    tests: dict[str, str]
    infra_failure: bool

    @property
    def all_pass(self) -> bool:
        total = sum(t for _, t in self.groups.values())
        passed = sum(p for p, _ in self.groups.values())
        return not self.infra_failure and total > 0 and passed == total

    def compact(self) -> dict[str, Any]:
        passed = sum(p for p, _ in self.groups.values())
        total = sum(t for _, t in self.groups.values())
        return {
            "passed": passed,
            "total": total,
            "all_pass": self.all_pass,
            "groups": self.groups,
            "infra_failure": self.infra_failure,
        }


@dataclass
class Check:
    name: str
    ok: bool
    detail: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# subprocess helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        log: Path | None = None, timeout: int = 7200) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True,
                          timeout=timeout, check=False)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"$ {' '.join(cmd)}\n--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}")
    return proc


def docker_env() -> dict[str, str]:
    """Pin the OrbStack context for docker CLI users and docker-py users."""
    env = dict(os.environ)
    env["DOCKER_CONTEXT"] = "orbstack"
    env["DOCKER_HOST"] = f"unix://{Path.home()}/.orbstack/run/docker.sock"
    return env


# ---------------------------------------------------------------------------
# sources and task variants
# ---------------------------------------------------------------------------


def ensure_problems_checkout(work: Path) -> Path:
    src = work / "src" / "scb-problems"
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        if run(["git", "clone", "-q", SCB_PROBLEMS_URL, str(src)]).returncode != 0:
            raise InfraError("clone of scb-problems failed")
    run(["git", "-C", str(src), "checkout", "-q", SCB_PROBLEMS_SHA])
    head = run(["git", "-C", str(src), "rev-parse", "HEAD"]).stdout.strip()
    if head != SCB_PROBLEMS_SHA:
        raise InfraError(f"scb-problems HEAD {head} != pinned {SCB_PROBLEMS_SHA}")
    return src


def checkpoint_names(problem_dir: Path) -> list[str]:
    config = yaml.safe_load((problem_dir / "config.yaml").read_text())
    items = sorted(config["checkpoints"].items(), key=lambda kv: kv[1].get("order", 0))
    return [name for name, _ in items]


def convert(src: Path, problem: str, out: Path, log_dir: Path) -> Path:
    proc = run(["uv", "run", "scripts/scb_to_harbor.py", "--org", "scb", "--out", str(out),
                "--no-build", "--force", problem], cwd=src, log=log_dir / "convert.log")
    task = out / "scb" / problem
    if proc.returncode != 0 or not (task / "task.toml").exists():
        raise InfraError(f"scb_to_harbor conversion failed for {problem}")
    return task


def split_steps(task_toml: str) -> tuple[str, list[tuple[str, str]]]:
    """Split task.toml into its header and ``[[steps]]`` blocks keyed by name."""
    head, *blocks = task_toml.split("[[steps]]\n")
    named: list[tuple[str, str]] = []
    for block in blocks:
        match = re.search(r'^name = "([^"]+)"', block, re.MULTILINE)
        if match is None:
            raise InfraError("step block without a name in task.toml")
        named.append((match.group(1), block))
    return head, named


def write_variant(base: Path, dest: Path, keep: list[str],
                  payloads: dict[str, Path | None]) -> Path:
    """Copy the converted task, keep only ``keep`` steps (continue mode) and
    replace each step's solution payload (``None`` means an empty workspace)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest)
    head, blocks = split_steps((base / "task.toml").read_text())
    kept = [block for name, block in blocks if name in keep]
    kept = [MIN_REWARD_LINE.sub("min_reward = { strict_pass_rate = 0.0 }", b) for b in kept]
    (dest / "task.toml").write_text(head + "".join("[[steps]]\n" + b for b in kept))
    for step_dir in (dest / "steps").iterdir():
        if step_dir.name not in keep:
            shutil.rmtree(step_dir)
    for step in keep:
        hook_report(dest / "steps" / step / "tests" / "test.sh")
        replace_payload(dest / "steps" / step / "solution" / "payload", payloads[step])
    return dest


def replace_payload(payload: Path, source: Path | None) -> None:
    shutil.rmtree(payload)
    if source is None:
        payload.mkdir(parents=True)
    else:
        shutil.copytree(source, payload)


def hook_report(test_sh: Path) -> None:
    text = test_sh.read_text()
    if text.count("PYTEST_RC=$?\n") != 1:
        raise InfraError(f"unexpected test.sh shape: {test_sh}")
    test_sh.write_text(text.replace("PYTEST_RC=$?\n", REPORT_HOOK))


def build_variants(base: Path, ckpts: list[str], oneshot: str | None,
                   variants_dir: Path) -> dict[str, Path]:
    # Reference payloads as emitted by the converter (canary-stripped).
    refs_dir = variants_dir / "reference_payloads"
    if refs_dir.exists():
        shutil.rmtree(refs_dir)
    for c in ckpts:
        shutil.copytree(base / "steps" / c / "solution" / "payload", refs_dir / c)
    ref = {c: refs_dir / c for c in ckpts}
    keep = [oneshot] if oneshot is not None else ckpts
    negative = {c: negative_snapshot(c, ckpts, ref, oneshot is not None) for c in keep}
    return {
        "positive": write_variant(base, variants_dir / "positive", keep, {c: ref[c] for c in keep}),
        "negative": write_variant(base, variants_dir / "negative", keep, negative),
        "reference_payloads": refs_dir,
    }


def negative_snapshot(step: str, ckpts: list[str], ref: dict[str, Path],
                      oneshot: bool) -> Path | None:
    """Reference k-1 for checkpoint k; the empty starter for k=1 or a one-shot."""
    idx = ckpts.index(step)
    return None if oneshot or idx == 0 else ref[ckpts[idx - 1]]


# ---------------------------------------------------------------------------
# Harbor side
# ---------------------------------------------------------------------------


def run_harbor(harbor: Path, task: Path, jobs: Path, job_name: str, repeats: int,
               log_dir: Path) -> list[Path]:
    job_dir = jobs / job_name
    if job_dir.exists():
        shutil.rmtree(job_dir)
    cmd = [str(harbor), "run", "-p", str(task), "-a", "oracle", "-o", str(jobs),
           "--job-name", job_name, "-k", str(repeats), "-n", str(repeats), "-q", "-y"]
    proc = run(cmd, env=docker_env(), log=log_dir / f"harbor-{job_name}.log", timeout=14400)
    trials = sorted(p.parent for p in job_dir.glob("*/result.json"))
    if proc.returncode != 0 or len(trials) != repeats:
        raise InfraError(f"harbor job {job_name}: rc={proc.returncode}, trials={len(trials)}")
    return trials


def parse_pytest_report(path: Path) -> dict[str, str]:
    tests: dict[str, str] = {}
    for test in json.loads(path.read_text()).get("tests", []):
        file_part, _, test_id = str(test["nodeid"]).partition("::")
        ckpt = CKPT_IN_FILE.search(file_part)
        outcome = str(test.get("outcome", "error"))
        key = f"{ckpt.group(1) if ckpt else '?'}::{test_id}"
        tests[key] = "passed" if outcome in ("passed", "xpassed") else outcome
    return tests


def harbor_grade(trial: Path, step: str) -> Grade:
    verifier = trial / "steps" / step / "verifier"
    details_path = verifier / "reward_details.json"
    if not details_path.exists():
        return Grade("harbor", step, {g: [0, 0] for g in GROUPS}, {}, True)
    details = json.loads(details_path.read_text())
    report_path = verifier / "pytest-report.json"
    tests = parse_pytest_report(report_path) if report_path.exists() else {}
    groups = {g: [int(details[f"{g}_passed"]), int(details[f"{g}_collected"])] for g in GROUPS}
    infra = bool(details.get("infrastructure_failure"))
    return Grade("harbor", step, groups, tests, infra)


# ---------------------------------------------------------------------------
# SCB native side
# ---------------------------------------------------------------------------


def stage_native_catalog(src: Path, problem: str, catalog: Path, log_dir: Path) -> None:
    dest = catalog / problem
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src / problem, dest)
    config = yaml.safe_load((dest / "config.yaml").read_text())
    deps = NATIVE_EVALUATOR_DEPS + list(config.get("test_dependencies") or [])
    dep_lines = "".join(f'    "{d}",\n' for d in deps)
    (dest / "pyproject.toml").write_text(
        f'[project]\nname = "{problem.replace("_", "-")}-evaluator"\nversion = "0.0.0"\n'
        f'requires-python = ">=3.12"\ndependencies = [\n{dep_lines}]\n')
    if run(["uv", "lock", "-q"], cwd=dest, log=log_dir / "native-uv-lock.log").returncode != 0:
        raise InfraError(f"uv lock failed for native evaluator of {problem}")


def native_grade(runner: Path, catalog: Path, problem: str, snapshot: Path | None,
                 ckpt_index: int, out: Path, log_dir: Path) -> Grade:
    step = f"checkpoint_{ckpt_index}"
    snap = out / "snapshot"
    if out.exists():
        shutil.rmtree(out)
    if snapshot is None:
        snap.mkdir(parents=True)
    else:
        shutil.copytree(snapshot, snap)
    env = docker_env()
    env.pop("DOCKER_CONTEXT")  # docker-py reads DOCKER_HOST only
    env["SCBENCH_PROBLEMS_PATH"] = str(catalog)
    cmd = [str(runner / ".venv" / "bin" / "slop-code"), "--quiet", "eval-snapshot", str(snap),
           "-o", str(out / "eval"), "-p", problem, "-c", str(ckpt_index),
           "-e", str(runner / NATIVE_ENV_CONFIG), "--json"]
    run(cmd, cwd=runner, env=env, log=log_dir / f"native-{out.parent.name}-{step}.log")
    evaluation = out / "eval" / "evaluation.json"
    if not evaluation.exists():
        return Grade("native", step, {g: [0, 0] for g in GROUPS}, {}, True)
    data = json.loads(evaluation.read_text())
    groups = {g: [int(data["pass_counts"].get(g.capitalize(), 0)),
                  int(data["total_counts"].get(g.capitalize(), 0))] for g in GROUPS}
    tests: dict[str, str] = {}
    for key, bucket in data["tests"].items():
        file_ckpt = key.rsplit("-", 1)[0]
        for status in ("passed", "failed", "skipped"):
            for test_id in bucket.get(status, []):
                tests[f"{file_ckpt}::{test_id}"] = status
    return Grade("native", step, groups, tests, bool(data.get("infrastructure_failure")))


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def outcome_view(grade: Grade) -> dict[str, bool]:
    """Pass/non-pass per test; skipped tests are dropped (both runners exclude
    them from the pass denominator)."""
    return {k: v == "passed" for k, v in grade.tests.items() if v != "skipped"}


def diff_tests(a: Grade, b: Grade, limit: int = 8) -> list[str]:
    va, vb = outcome_view(a), outcome_view(b)
    notes = [f"only in {a.source}: {k}" for k in sorted(va.keys() - vb.keys())]
    notes += [f"only in {b.source}: {k}" for k in sorted(vb.keys() - va.keys())]
    notes += [f"{k}: {a.source}={'pass' if va[k] else 'fail'} {b.source}={'pass' if vb[k] else 'fail'}"
              for k in sorted(va.keys() & vb.keys()) if va[k] != vb[k]]
    return notes[:limit] + ([f"... {len(notes) - limit} more"] if len(notes) > limit else [])


def check_expectation(name: str, runs: list[dict[str, Grade]], expect_pass: bool) -> Check:
    detail: list[str] = []
    for i, grades in enumerate(runs):
        for step, grade in grades.items():
            if grade.infra_failure or not grade.tests:
                detail.append(f"trial {i} {step}: infrastructure failure or no tests parsed")
            elif grade.all_pass != expect_pass:
                c = grade.compact()
                detail.append(f"trial {i} {step}: {c['passed']}/{c['total']} (expected "
                              f"{'all pass' if expect_pass else 'at least one failure'})")
    return Check(name, not detail, detail)


def check_determinism(name: str, runs: list[dict[str, Grade]]) -> Check:
    detail: list[str] = []
    first = runs[0]
    for i, grades in enumerate(runs[1:], start=1):
        for step, grade in grades.items():
            ref = first[step]
            if outcome_view(grade) != outcome_view(ref) or grade.groups != ref.groups:
                detail.append(f"trial {i} vs 0 {step}: " + "; ".join(diff_tests(ref, grade)))
    return Check(name, not detail, detail)


def check_parity(name: str, harbor: dict[str, Grade], native: dict[str, Grade]) -> Check:
    detail: list[str] = []
    for step, h in harbor.items():
        n = native[step]
        if n.infra_failure:
            detail.append(f"{step}: native infrastructure failure")
            continue
        if h.groups != n.groups:
            detail.append(f"{step}: groups harbor={h.groups} native={n.groups}")
        per_test = diff_tests(h, n)
        if per_test:
            detail.append(f"{step}: " + "; ".join(per_test))
    return Check(name, not detail, detail)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def harbor_runs(args: argparse.Namespace, variants: dict[str, Path], steps: list[str],
                label_dir: Path) -> dict[str, list[dict[str, Grade]]]:
    out: dict[str, list[dict[str, Grade]]] = {}
    for variant in ("positive", "negative"):
        trials = run_harbor(args.harbor, variants[variant], label_dir / "jobs",
                            f"{variant}", args.repeats, label_dir / "logs")
        out[variant] = [{s: harbor_grade(t, s) for s in steps} for t in trials]
    return out


def native_runs(args: argparse.Namespace, src: Path, variants: dict[str, Path],
                steps: list[str], all_ckpts: list[str], label_dir: Path) -> dict[str, dict[str, Grade]]:
    catalog = label_dir / "native_catalog"
    stage_native_catalog(src, args.problem, catalog, label_dir / "logs")
    refs = variants["reference_payloads"]
    out: dict[str, dict[str, Grade]] = {"positive": {}, "negative": {}}
    ref = {c: refs / c for c in all_ckpts}
    for step in steps:
        idx = all_ckpts.index(step) + 1
        prev = negative_snapshot(step, all_ckpts, ref, args.oneshot_checkpoint is not None)
        for variant, snap in (("positive", ref[step]), ("negative", prev)):
            out[variant][step] = native_grade(args.scb_runner, catalog, args.problem, snap, idx,
                                              label_dir / "native" / variant / step,
                                              label_dir / "logs")
    return out


def validate(args: argparse.Namespace) -> dict[str, Any]:
    oneshot = f"checkpoint_{args.oneshot_checkpoint}" if args.oneshot_checkpoint else None
    label = f"{args.problem}-oneshot-{oneshot}" if oneshot else f"{args.problem}-multi"
    label_dir = args.work / label
    label_dir.mkdir(parents=True, exist_ok=True)
    src = ensure_problems_checkout(args.work)
    all_ckpts = checkpoint_names(src / args.problem)
    steps = [oneshot] if oneshot else all_ckpts
    base = convert(src, args.problem, label_dir / "converted", label_dir / "logs")
    variants = build_variants(base, all_ckpts, oneshot, label_dir / "variants")

    harbor = harbor_runs(args, variants, steps, label_dir)
    native: dict[str, dict[str, Grade]] = {}
    if not args.skip_native:
        native = native_runs(args, src, variants, steps, all_ckpts, label_dir)
    checks = run_checks(harbor, native)
    summary = {
        "label": label,
        "problem": args.problem,
        "mode": "one-shot" if oneshot else "multi-checkpoint",
        "steps": steps,
        "scb_problems_sha": SCB_PROBLEMS_SHA,
        "harbor": run([str(args.harbor), "--version"]).stdout.strip(),
        "repeats": args.repeats,
        "validated": all(c.ok for c in checks),
        "checks": [asdict(c) for c in checks],
        "grades": compact_grades(harbor, native),
    }
    (label_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def run_checks(harbor: dict[str, list[dict[str, Grade]]],
               native: dict[str, dict[str, Grade]]) -> list[Check]:
    checks = [
        check_expectation("reference_k_passes_1..k", harbor["positive"], expect_pass=True),
        check_expectation("reference_k-1_fails_k", harbor["negative"], expect_pass=False),
        check_determinism("repeat_identical_positive", harbor["positive"]),
        check_determinism("repeat_identical_negative", harbor["negative"]),
    ]
    for variant, grades in native.items():
        checks.append(check_parity(f"native_parity_{variant}", harbor[variant][0], grades))
    return checks


def compact_grades(harbor: dict[str, list[dict[str, Grade]]],
                   native: dict[str, dict[str, Grade]]) -> dict[str, Any]:
    return {
        "harbor": {v: [{s: g.compact() for s, g in r.items()} for r in runs]
                   for v, runs in harbor.items()},
        "native": {v: {s: g.compact() for s, g in r.items()} for v, r in native.items()},
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--problem", required=True)
    parser.add_argument("--oneshot-checkpoint", type=int, default=None,
                        help="Validate only this checkpoint as a one-shot task")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--harbor", type=Path, default=DEFAULT_HARBOR)
    parser.add_argument("--scb-runner", type=Path, default=DEFAULT_SCB_RUNNER)
    parser.add_argument("--skip-native", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = validate(args)
    except InfraError as exc:
        print(f"INFRA: {exc}", file=sys.stderr)
        return 2
    for check in summary["checks"]:
        print(f"[{'PASS' if check['ok'] else 'FAIL'}] {check['name']}")
        for line in check["detail"]:
            print(f"    {line}")
    print(f"{summary['label']}: {'VALIDATED' if summary['validated'] else 'EXCLUDED'}")
    return 0 if summary["validated"] else 1


if __name__ == "__main__":
    sys.exit(main())
