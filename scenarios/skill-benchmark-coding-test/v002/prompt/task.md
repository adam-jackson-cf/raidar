Build the TypeScript utility and tests, then verify the work with the existing project commands.

Implementation:
- Add `src/lib/math.ts`.
- Export exactly `sumEven(nums: number[]): number`.
- Return the sum of values that are both integers and even.
- Ignore odd values and non-integers.
- Preserve sign for negative even integers.
- Return `0` when no values qualify.
- Use `Number.isInteger` before applying the even check.

Tests:
- Add `src/test/math.test.ts` using Vitest `describe` and `it` cases.
- Cover mixed integers, odd-only input, decimals, negative even values, empty input, and zero.
- Keep test lines formatted for the repo linter: split long `expect(...)` calls across multiple lines instead of one very long line.
- Keep imports sorted for the existing Ultracite/Biome lint rules; project alias imports such as `@/lib/math` should come before package imports such as `vitest`.

Before finishing:
- Run `bun run typecheck`, `bun run lint`, `bun run test`, and `bun run test:coverage`.
- If lint reports formatting or import-order changes, edit the files and re-run `bun run lint` until it passes.
