"""GitHub-backed policy approval, measurement import, and budget CLI I/O."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

POLICY_PATH = ".omp/test-budgets/limits.json"
WORKFLOW_PATH = ".github/workflows/test-budget-measure.yml"
RULE = "TEST.budget.fast-tier-time"


def _run(root: Path, *command: str) -> bytes:
    result = subprocess.run(command, cwd=root, capture_output=True, check=False, timeout=120)
    if result.returncode:
        raise ValueError(f"{RULE}: {' '.join(command[:3])}: {result.stderr.decode().strip()}")
    return result.stdout


def _api(root: Path, endpoint: str) -> Any:
    return json.loads(_run(root, "gh", "api", endpoint))


def _pages(root: Path, endpoint: str, key: str) -> list[dict[str, Any]]:
    records = []
    page = 1
    while True:
        separator = "&" if "?" in endpoint else "?"
        batch = _api(root, f"{endpoint}{separator}per_page=100&page={page}")[key]
        records.extend(batch)
        if len(batch) < 100:
            return records
        page += 1


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{RULE}: {path} must contain a JSON object")
    return value


def _repository(root: Path) -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not repository:
        remote = _run(root, "git", "remote", "get-url", "origin").decode().strip()
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?", remote
        )
        if match:
            repository = match[1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(f"{RULE}: cannot establish GitHub repository identity")
    return repository


def _trusted_base(root: Path, repository: str | None) -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if repository is None:
            raise ValueError(f"{RULE}: CI repository identity is missing")
        event = _json(Path(os.environ["GITHUB_EVENT_PATH"]))
        if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
            pull = _api(root, f"repos/{repository}/pulls/{int(event['number'])}")
            return str(pull["base"]["sha"])
        metadata = _api(root, f"repos/{repository}")
        if (
            os.environ.get("GITHUB_EVENT_NAME") == "push"
            and os.environ.get("GITHUB_REF") == f"refs/heads/{metadata['default_branch']}"
        ):
            before = str(event["before"])
            if re.fullmatch(r"[0-9a-f]{40}", before) and set(before) != {"0"}:
                return before
        return str(_api(root, f"repos/{repository}/commits/{metadata['default_branch']}")["sha"])
    return _run(root, "git", "merge-base", "HEAD", "origin/main").decode().strip()


def load_base_policy(root: Path, repository: str | None = None) -> dict[str, Any] | None:
    base = _trusted_base(root, repository)
    paths = (
        _run(root, "git", "ls-tree", "--name-only", base, "--", POLICY_PATH).decode().splitlines()
    )
    if POLICY_PATH not in paths:
        return None
    value = json.loads(_run(root, "git", "show", f"{base}:{POLICY_PATH}"))
    if not isinstance(value, dict):
        raise ValueError(f"{RULE}: trusted base policy is malformed")
    return value


def _review(
    root: Path,
    repository: str,
    review_id: int,
    pull_number: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    review = _api(root, f"repos/{repository}/pulls/{pull_number}/reviews/{review_id}")
    commit = review["commit_id"]
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"{RULE}: review commit is malformed")
    reviewed_policy = json.loads(_run(root, "git", "show", f"{commit}:{POLICY_PATH}"))
    if reviewed_policy != policy:
        raise ValueError(f"{RULE}: current policy differs from the owner-reviewed commit")
    markers = re.findall(r"(?m)^Policy-Diff-SHA256: ([0-9a-f]{64})\s*$", review.get("body") or "")
    return {
        "reviewer": review["user"]["login"],
        "state": review["state"],
        "commit": commit,
        "policyDiffDigest": markers[0] if len(markers) == 1 else None,
    }


def _measurement_artifacts(
    root: Path, repository: str, run_id: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    run = _api(root, f"repos/{repository}/actions/runs/{run_id}")
    if (
        run.get("conclusion") != "success"
        or run.get("status") != "completed"
        or run.get("event") != "workflow_dispatch"
        or run.get("path") != WORKFLOW_PATH
        or run.get("repository", {}).get("full_name") != repository
    ):
        raise ValueError(
            f"{RULE}: run {run_id} is not a successful repository measurement dispatch"
        )
    jobs = _pages(root, f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest", "jobs")
    measured = {job["name"]: job for job in jobs if re.fullmatch(r"measure-\d+", job["name"])}
    if not measured or any(job.get("conclusion") != "success" for job in measured.values()):
        raise ValueError(f"{RULE}: measurement jobs are missing or unsuccessful")
    artifacts = _pages(root, f"repos/{repository}/actions/runs/{run_id}/artifacts", "artifacts")
    selected = [
        artifact for artifact in artifacts if artifact["name"].startswith("test-budget-report-")
    ]
    if len(selected) != len(measured) or len({item["name"] for item in selected}) != len(selected):
        raise ValueError(f"{RULE}: measurement artifact/job count mismatch")
    evidence = []
    for artifact in selected:
        index = artifact["name"].removeprefix("test-budget-report-")
        job = measured.get(f"measure-{index}")
        if artifact.get("expired") or job is None:
            raise ValueError(f"{RULE}: missing job or expired artifact")
        archive = _run(
            root,
            "gh",
            "api",
            f"repos/{repository}/actions/artifacts/{int(artifact['id'])}/zip",
        )
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            if sorted(bundle.namelist()) != ["completion.json", "report.json"]:
                raise ValueError(f"{RULE}: unexpected measurement artifact contents")
            report = json.loads(bundle.read("report.json"))
            completion = json.loads(bundle.read("completion.json"))
        provenance = {"runId": str(run_id), "jobId": str(job["id"]), "successful": True}
        if (
            completion.get("workflowRevision") != run["head_sha"]
            or completion.get("revision") != report.get("revision")
            or completion.get("profile") != report.get("profile")
        ):
            raise ValueError(f"{RULE}: artifact revision/profile does not match measurement run")
        if report.get("measurement") != provenance:
            raise ValueError(f"{RULE}: artifact job identity does not match GitHub")
        evidence.append((report, completion))
    return evidence


def _measurement_review(
    root: Path,
    repository: str,
    run_id: int,
    policy: dict[str, Any],
    baselines: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence = _measurement_artifacts(root, repository, run_id)
    unsigned = {key: value for key, value in policy.items() if key != "measurementApproval"}
    digest = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if any(
        completion.get("policyDigest") != digest or report.get("profile") != policy.get("profile")
        for report, completion in evidence
    ):
        raise ValueError(f"{RULE}: measure completion is not bound to this policy/profile")
    retained = {(_canonical(item)) for item in baselines}
    if any(_canonical(report) not in retained for report, _ in evidence):
        raise ValueError(f"{RULE}: measure completion reports are not retained baselines")
    return {
        "state": "APPROVED",
        "commit": evidence[0][0]["revision"],
        "policyDiffDigest": digest,
    }


def import_measurements(root: Path, run_ids: list[str], record_reports: Any) -> list[Path]:
    repository = _repository(root)
    reports: list[dict[str, Any]] = []
    for run_id in run_ids:
        if not run_id.isdecimal():
            raise ValueError(f"{RULE}: RUNS must contain GitHub run IDs")
        reports.extend(
            report for report, _ in _measurement_artifacts(root, repository, int(run_id))
        )
    if not reports:
        raise ValueError(f"{RULE}: at least one measurement run is required")
    records = record_reports(
        reports, revision=reports[0]["revision"], profile=reports[0]["profile"]
    )
    destination = root / ".omp/test-budgets/baselines"
    # Validate all records and collisions before writing any evidence.
    pending = []
    for record in records:
        provenance = record["measurement"]
        name = f"{int(provenance['runId'])}-{int(provenance['jobId'])}.json"
        path = destination / name
        content = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise ValueError(f"{RULE}: immutable baseline already exists: {name}")
        pending.append((path, content))
    destination.mkdir(parents=True, exist_ok=True)
    for path, content in pending:
        if not path.exists():
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content)
    return [path for path, _ in pending]


def seal_measurement(root: Path, report_path: Path, index: str) -> None:
    repository = _repository(root)
    run_id = int(os.environ["GITHUB_RUN_ID"])
    jobs = _pages(root, f"repos/{repository}/actions/runs/{run_id}/jobs?filter=latest", "jobs")
    matching = [
        job for job in jobs if job["name"] == f"measure-{index}" and job["status"] == "in_progress"
    ]
    if len(matching) != 1:
        raise ValueError(f"{RULE}: cannot identify current GitHub measurement job")
    report = _json(report_path)
    revision = _run(root, "git", "rev-parse", "HEAD").decode().strip()
    if (
        report.get("revision") != revision
        or report.get("complete") is not True
        or report.get("exitCode") != 0
    ):
        raise ValueError(f"{RULE}: only complete successful checked-out revision can be sealed")
    report["measurement"] = {
        "runId": str(run_id),
        "jobId": str(matching[0]["id"]),
        "successful": True,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    policy = _json(root / POLICY_PATH)
    policy.pop("measurementApproval", None)
    policy["status"] = "accepted"
    completion = {
        "revision": report["revision"],
        "workflowRevision": os.environ["GITHUB_SHA"],
        "profile": report["profile"],
        "policyDigest": hashlib.sha256(_canonical(policy)).hexdigest(),
    }
    (report_path.parent / "completion.json").write_text(
        json.dumps(completion, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _check_command(args: argparse.Namespace, checker: ModuleType) -> int:
    if args.report is None or not args.invocation:
        raise ValueError(f"{RULE}: check requires --report and --invocation")
    revision = args.revision or _run(args.root, "git", "rev-parse", "HEAD").decode().strip()
    policy = _json(args.root / POLICY_PATH)
    baselines = [
        _json(path) for path in sorted((args.root / ".omp/test-budgets/baselines").glob("*.json"))
    ]
    ci = args.ci or os.environ.get("GITHUB_ACTIONS") == "true"
    repository = _repository(args.root) if ci or policy.get("status") == "accepted" else None
    base = load_base_policy(args.root, repository)
    digest = checker.policy_diff_digest(base, policy) if base is not None else None
    approvals = [
        _json(path) for path in sorted((args.root / ".omp/test-budgets/approvals").glob("*.json"))
    ]
    approval = next((item for item in approvals if item.get("policyDiffDigest") == digest), None)
    needs_external = policy.get("status") == "accepted" or (
        base is not None and checker.policy_increases(base, policy)
    )
    owner = ""
    if needs_external:
        repository = repository or _repository(args.root)
        owner = _api(args.root, f"repos/{repository}")["owner"]["login"]

    def read_review(review_repository: str, review_id: int) -> dict[str, Any]:
        if review_repository != repository:
            raise ValueError(f"{RULE}: approval repository differs from origin")
        measurement = policy.get("measurementApproval", {})
        if measurement.get("kind") == "measurement" and measurement.get("reviewId") == review_id:
            return {
                **_measurement_review(args.root, review_repository, review_id, policy, baselines),
                "reviewer": owner,
            }
        if approval is None or approval.get("reviewId") != review_id:
            raise ValueError(f"{RULE}: review reference is missing")
        return _review(args.root, review_repository, review_id, int(approval["pullNumber"]), policy)

    result = checker.check_budget(
        _json(args.report),
        policy,
        baselines,
        revision=revision,
        invocation_id=args.invocation,
        ci=ci,
        now=datetime.date.today,
        base_policy=base,
        approval=approval,
        owner=owner,
        read_review=read_review,
    )
    for line in result["lines"]:
        print(line)
    return int(result["exitCode"])


def main(checker: ModuleType) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("check", "record", "seal"), default="check")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--invocation", "--invocation-id", dest="invocation")
    parser.add_argument("--ci", action="store_true")
    parser.add_argument("--runs", nargs="+", default=[])
    parser.add_argument("--index")
    args = parser.parse_args()
    try:
        if args.command == "record":
            run_ids = [item for value in args.runs for item in re.split(r"[\s,]+", value) if item]
            for path in import_measurements(args.root, run_ids, checker.record_reports):
                print(path.relative_to(args.root))
            return 0
        if args.command == "seal":
            if args.report is None or args.index is None:
                raise ValueError(f"{RULE}: seal requires --report and --index")
            seal_measurement(args.root, args.report, args.index)
            return 0
        return _check_command(args, checker)
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        zipfile.BadZipFile,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"{RULE}: evidence validity failure: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import importlib.util

    specification = importlib.util.spec_from_file_location(
        "test_budget_checker", Path(__file__).with_name("check-test-budgets.py")
    )
    if specification is None or specification.loader is None:
        raise SystemExit(f"{RULE}: checker module is unavailable")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    raise SystemExit(main(module))
