Build `sumEven` with minimal churn.

Create `src/lib/math.ts` exporting exactly:
- `sumEven(nums: number[]): number`

Use a direct reduce implementation:
- start from `0`
- add `value` only when `Number.isInteger(value)` and `value % 2 === 0`
- otherwise keep the current sum

Create `src/test/math.test.ts` with Vitest. Use this import order:
- `import { sumEven } from "@/lib/math";`
- `import { describe, expect, it } from "vitest";`

Add six small tests only: mixed integers, odd-only input, decimals, negative even values, empty input, and zero. Avoid large arrays and extra edge cases.

Run once after editing: `bun run typecheck`, `bun run lint`, `bun run test`, and `bun run test:coverage`. If lint fails, apply its exact formatting/import-order suggestion and rerun only the failed command.
