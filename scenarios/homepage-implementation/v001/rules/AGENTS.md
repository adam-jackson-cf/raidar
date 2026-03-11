# Project Guidelines

## Code Style
- Use TypeScript strict mode
- Use Tailwind utility classes exclusively (no inline styles, no CSS modules)
- Use shadcn/ui components from `@/components/ui`
- Use Lucide icons via `lucide-react`

## Validation
- Use Zod for all input validation schemas
- Use React Hook Form with Zod resolver for forms

## Component Structure
- Place page components in `app/` directory
- Place reusable components in `components/`
- Export types from component files

## Quality Gates
Before reporting completion, ensure:
1. `bun run typecheck` passes
2. `bun run lint` passes
3. `bun run test:coverage` passes and coverage remains at least 80%
4. `bun run build` succeeds

## Error Handling
- Use try/catch with typed error handling
- Display user-friendly error messages
- Log errors to console in development only

## Imports
- Use absolute imports with `@/` prefix
- Group imports: React, external libraries, internal modules, types
- No unused imports

## Styling
- Use Tailwind CSS classes only
- Follow the color scheme defined in globals.css
- Use CSS variables for theming
