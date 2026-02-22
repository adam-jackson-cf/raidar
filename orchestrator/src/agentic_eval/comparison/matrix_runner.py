"""Matrix runner for executing multiple harness/model combinations."""

import concurrent.futures
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..harness.config import HarnessConfig
from ..matrix import MatrixConfig, generate_matrix_entries
from ..runner import RunRequest, prepare_workspace, run_task
from ..scaffold import resolve_scaffold_source
from ..schemas.scorecard import Scorecard
from ..schemas.task import TaskDefinition


@dataclass
class MatrixRunResult:
    """Result of a single matrix run."""

    config: HarnessConfig
    scorecard: Scorecard | None
    error: str | None = None
    duration_seconds: int = 0


@dataclass
class MatrixRunReport:
    """Report from running a full matrix."""

    task: str
    started_at: datetime
    completed_at: datetime
    results: list[MatrixRunResult]

    @property
    def successful_runs(self) -> int:
        return sum(1 for r in self.results if r.scorecard is not None)

    @property
    def failed_runs(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    @property
    def best_result(self) -> MatrixRunResult | None:
        successful = [r for r in self.results if r.scorecard is not None]
        if not successful:
            return None
        return max(successful, key=lambda r: r.scorecard.composite_score)


class MatrixRunner:
    """Runs evaluation matrix across multiple configurations."""

    def __init__(
        self,
        tasks_dir: Path,
        executions_dir: Path,
    ) -> None:
        self.tasks_dir = tasks_dir
        self.executions_dir = executions_dir

    def run_single(
        self,
        task: TaskDefinition,
        harness_config: HarnessConfig,
        task_dir: Path | None = None,
        dry_run: bool = False,
        progress_callback: Callable[[str], None] | None = None,
    ) -> MatrixRunResult:
        """Run a single configuration.

        Args:
            task: Task definition
            harness_config: Harness/model configuration
            dry_run: If True, prepare workspace but don't execute
            progress_callback: Optional callback for progress updates

        Returns:
            MatrixRunResult with scorecard or error
        """
        import time

        start_time = time.time()

        try:
            model_str = harness_config.model.qualified_name
            run_id = f"{task.name}-{harness_config.agent.value}-{uuid.uuid4().hex[:8]}"
            execution_dir = self.executions_dir / run_id
            if progress_callback:
                progress_callback(
                    f"Preparing workspace for {harness_config.agent.value}/{model_str}"
                )

            # Prepare workspace using module-level function
            resolved_task_dir = task_dir or (self.tasks_dir / task.name)
            adapter = harness_config.adapter()
            adapter.validate()

            scaffold_source = resolve_scaffold_source(
                resolved_task_dir,
                task.scaffold.root,
                task_name=task.name,
                task_version=task.version,
            )

            if dry_run:
                workspace_path, _ = prepare_workspace(
                    scaffold_dir=scaffold_source.path,
                    target_dir=execution_dir / "runs" / "run-01" / "workspace",
                    task_dir=resolved_task_dir,
                    agent=harness_config.agent.value,
                )
                adapter.prepare_workspace(workspace_path)
                return MatrixRunResult(
                    config=harness_config,
                    scorecard=None,
                    error=None,
                    duration_seconds=int(time.time() - start_time),
                )

            if progress_callback:
                progress_callback(f"Executing {harness_config.agent.value} with {model_str}")

            # Run task
            request = RunRequest(
                task=task,
                config=harness_config,
                task_dir=resolved_task_dir,
                execution_dir=execution_dir,
                repeat_index=1,
            )
            eval_run = run_task(request)

            run_meta = eval_run.scores.metadata.get("run", {})
            run_json_path_raw = run_meta.get("run_json_path")
            if not isinstance(run_json_path_raw, str):
                raise ValueError("run metadata missing run_json_path")
            result_path = Path(run_json_path_raw)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(eval_run.model_dump_json(indent=2))

            return MatrixRunResult(
                config=harness_config,
                scorecard=eval_run.scores,
                duration_seconds=int(time.time() - start_time),
            )

        except Exception as e:
            return MatrixRunResult(
                config=harness_config,
                scorecard=None,
                error=str(e),
                duration_seconds=int(time.time() - start_time),
            )

    def run_matrix(
        self,
        task: TaskDefinition,
        matrix_config: MatrixConfig,
        parallel: int = 1,
        dry_run: bool = False,
        progress_callback: Callable[[str], None] | None = None,
    ) -> MatrixRunReport:
        """Run a full matrix of configurations.

        Args:
            task: Task definition
            matrix_config: Matrix configuration
            parallel: Number of parallel executions (default 1)
            dry_run: If True, prepare workspaces but don't execute
            progress_callback: Optional callback for progress updates

        Returns:
            MatrixRunReport with all results
        """
        entries = generate_matrix_entries(matrix_config)
        configs = [entry.to_harness_config() for entry in entries]
        started_at = datetime.now()
        results: list[MatrixRunResult] = []

        if progress_callback:
            progress_callback(f"Running matrix with {len(configs)} configurations")

        if parallel > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
                futures = {
                    executor.submit(
                        self.run_single,
                        task,
                        cfg,
                        dry_run=dry_run,
                        progress_callback=progress_callback,
                    ): cfg
                    for cfg in configs
                }

                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    results.append(result)
        else:
            for i, cfg in enumerate(configs, 1):
                model_str = cfg.model.qualified_name
                if progress_callback:
                    progress_callback(f"Run {i}/{len(configs)}: {cfg.agent.value}/{model_str}")
                result = self.run_single(
                    task,
                    cfg,
                    dry_run=dry_run,
                    progress_callback=progress_callback,
                )
                results.append(result)

        return MatrixRunReport(
            task=task.name,
            started_at=started_at,
            completed_at=datetime.now(),
            results=results,
        )


def run_matrix(
    tasks_dir: Path,
    executions_dir: Path,
    task: TaskDefinition,
    matrix_config: MatrixConfig,
    parallel: int = 1,
    dry_run: bool = False,
) -> MatrixRunReport:
    """Convenience function to run a matrix.

    Args:
        tasks_dir: Path to tasks directory
        executions_dir: Path to matrix execution output directory
        task: Task definition
        matrix_config: Matrix configuration
        parallel: Number of parallel executions
        dry_run: If True, prepare but don't execute

    Returns:
        MatrixRunReport
    """
    runner = MatrixRunner(
        tasks_dir=tasks_dir,
        executions_dir=executions_dir,
    )

    return runner.run_matrix(
        task=task,
        matrix_config=matrix_config,
        parallel=parallel,
        dry_run=dry_run,
    )
