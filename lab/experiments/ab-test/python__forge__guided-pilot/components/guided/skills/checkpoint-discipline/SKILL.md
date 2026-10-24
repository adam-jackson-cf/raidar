---
name: checkpoint-discipline
description: Use when implementing or extending a checkpoint of a multi-step coding task, to plan the change, keep the existing interface working and verify before finishing.
---

# Checkpoint discipline

1. Read the whole specification for this checkpoint and list what is new
   compared with the code already in the workspace.
2. Find where each new requirement belongs in the existing modules. Extend
   them; do not rewrite working code or change its public interface.
3. Write or update a test for each new behaviour before you implement it.
4. Implement in small, cohesive functions. Factor out shared logic instead of
   copying it.
5. Run the whole test suite and fix every failure, including failures in
   behaviour from earlier checkpoints.
6. Delete dead code, debugging output and scratch files before you finish.
