Decision: opus (high confidence) — regression verified: identical lock returns `direct-reference` before C but raises uncaught `ValueError` at HEAD.
The pre-C version/parser/key failures propagate before grading; they crash rather than produce an incorrect grade.
Restore all four grading files, adapt tests through existing interfaces/monkeypatched runners, confirm the old digest, then relock.
Defer resolver error-handling fixes to a deliberate grading change with revalidation.
Adopt Opus’s full future rule: test-only work must preserve identity-bearing bytes; identity changes require separate review, before/after values and scheduled revalidation.
No files edited.
