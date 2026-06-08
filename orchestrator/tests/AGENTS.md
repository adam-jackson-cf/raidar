## Testing

### Placement

- Place tests by behavior owner, not by a strict package mirror.
- Add to an existing behavior file when the scenario belongs with existing coverage.
- Create a new test file only for a distinct responsibility or workflow surface.
- Use meaningful test names that describe the behavior and expected outcome.
- Avoid generic names such as `test_works`, `test_valid`, or implementation-step names.
- Group related assertions in behavior scenarios instead of creating brittle one-assertion tests for incidental values.
- Put reusable fakes, builders, and shared assertions in the closest existing support module.
- Keep large reusable fixtures separate from behavior tests.
- For scorer work, preserve the distinction between direct retained evidence and `proxy:` evidence in test assertions.
