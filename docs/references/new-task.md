# Creating a New Eval Task

Use this guide to add a self-contained coding task that agents will execute against the Next.js scaffold.

## 1. Prepare the task directory
- Create `tasks/<task-name>/` and copy any reference assets (images, fixtures, rules).
- Keep naming consistent with the YAML `name` field; the CLI displays this string in results and scorecards.

## 2. Author `task.yaml`
A complete task file follows the structure already used in `tasks/homepage-implementation/task.yaml`.

```yaml
name: homepage-implementation
description: Implement homepage matching provided reference design
difficulty: medium
category: greenfield-ui
timeout_sec: 1800

scaffold:
  template: next-shadcn-starter
  version: v2025.01
  rules_variant: strict

verification:
  max_gate_failures: 3
  gates:
    - name: typecheck
      command: bun run typecheck
      on_failure: continue
```

Key sections to fill out:
1. **Metadata** – `name`, `description`, `difficulty`, `category`, `timeout_sec`.
2. **Scaffold** – `template` name (usually `next-shadcn-starter`), pinned `version` (e.g., `v2025.01`), and default `rules_variant` (strict|minimal|none).
3. **Verification** – `max_gate_failures` plus ordered gate list. Each gate executes in the prepared workspace via `GateWatcher` with global timeouts from `settings.timeouts.gate`.
4. **Compliance** – deterministic checks (`import_present`, `file_exists`, `no_pattern`) and `llm_judge_rubric` entries with weights that sum to 1. These feed `scoring/compliance.py`.
5. **Visual** (optional) – reference image path relative to the task folder, the screenshot capture command, and similarity `threshold`.
6. **Prompt** – multi-line description instructing the agent; keep commands explicit (“Run `bun run dev`…”).

## 3. Provide rules
- Place harness-specific rule files under `tasks/<task>/rules/<agent>/<variant>.md` if custom guidance is required.
- The CLI injects these files during `prepare_workspace`, so ensure filenames match agent enums (e.g., `claude-code`, `codex-cli`).

## 4. Validate locally
1. Run `eval-orchestrator manifest --scaffold scaffolds/<template>/<version>` whenever you update a scaffold version. This regenerates `scaffold.manifest.json` with the latest hashes.
2. Dry-run the task: `eval-orchestrator run --task tasks/<task>/task.yaml --agent codex-cli --model codex/gpt-5.2-high --rules strict --scaffolds-root scaffolds --workspace workspace --output orchestrator/results`.
3. Inspect the emitted scorecard (`orchestrator/results/<id>.json`) to ensure gates, compliance checks, and optional visual diffs behave as expected.

## 5. Document references
- If the task relies on design specs or assets, store them next to the task and reference them in the YAML (e.g., `visual.reference_image`).
- Update `docs/architecture-review.md` or task-specific docs if the new scenario introduces architectural constraints.
