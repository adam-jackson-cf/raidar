```text
Q1: c (high) — (b), except that G3, the protected-path checks and the seal's own hardening stay as they are.
  Amendment (exact): delete the broker. That means broker, broker_stop, _handle, _hook_child,
  _peer_uid, _check_broker, the root-owned hook-log directory and socket, and the chained log
  writer. `hook` runs `decide` as the agent's user and appends to a log the agent can write.
  G1, G2 and W5's hook-log condition become readings. hook_log_* integrity findings no longer
  count toward W11 validity. These stay validity: G3 (the gate's tool-directory digest), the
  installed-tree digest, the watched-path and protected-path findings, and the session-log findings.
  Keep _protect_record_channel (PR_SET_DUMPABLE) and the call supervisor. They protect the seal's
  own stdout, not only the hook.
  - The seal does not depend on the broker. primitive/gate.py seal() runs through
    calls.trusted_exec as the agent's user. attribute() uses only the seal record and _tools_intact.
  - Only health/gate.py _g1_step/_g2_step, evidence W5 and W11 read the hook log. Today
    collect.py:369-377 makes G1–G3 and W1–W11 invalidate the trial. A forged line can only
    misstate whether the agent obeyed its own gate. It cannot change the graded tree or the
    seal, so the rule "readings never invalidate" applies.
  - (b) as written also downgrades G3. G3 is the tool-tree digest (_g3, sha256_before/after),
    not hook-log evidence, and D5 keeps protected paths in G.
  - (a) costs too much. S6 added 884 lines and removed 207 in verify_gate.py, and it adds a root
    daemon that serves the agent's socket requests. §5.1 says root only copies bytes and installs
    wheels. It also leaves 12 frozen functions, and its Docker proof is still pending.
  - Correction to the brief: verify_gate.py no longer exists. Commit bcd6f30 moved it to
    subject/primitive/gate_tool.py, which the spike's verify.py uploads as verify_gate.py.

Q2: b (high) — Amendment (exact): the namespace is required for stage references only.
  Agent and workflow references keep RELEASE_PATTERN. stage-new, stage-check and stage-info take
  NAME=<ns>/<name>. registry._check_location and package_folders require exactly two levels.
  Change the folder examples in split-design §5.2, §5.6 and §5.7 and the stage README to
  lab/stages/raidar/<name>/. Add CODEOWNERS for lab/stages/raidar/.
  - The code already maps a reference straight to its path: registry.stage_folder =
    lab/stages/ + release.name, and install uses /opt/raidar/stages/[ns/]name. (b) makes that the
    only rule.
  - (a) adds a special case. A top-level folder could then be either the upstream package x or
    the namespace x/. verify@1 and raidar/verify@1 would also name the same folder unless another
    refusal is added.
  - The raidar/ reservation is not enforced anywhere today (nothing in releases.py or
    registry.py). Under (b) it becomes a directory that review and CODEOWNERS can protect.
  - Every reference then states its namespace, which the rule "only packages merged through a
    reviewed PR count" needs.
  - Switching costs nothing now. lab/stages/ holds only README.md and lab/releases.yaml has no
    stages: section. The phase-5 ports go straight into lab/stages/raidar/. The cost: authors of
    local packages must pick a namespace.
```
