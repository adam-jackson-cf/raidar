# environments/ (read-only history)

This directory belongs to the scenario epoch, which was archived in reboot Phase 0
(task 0.6) and whose authoring and execution code was removed at the Phase 1 cutover
(WS-L). Nothing in Raidar reads it any more. It is kept unchanged as the record of
what earlier scenario-epoch experiments ran; do not add to it or edit it.

New work uses the experiment model: an `experiment.yaml` names pinned problems
(`problems/`), conditions built from components, agents, scorers and an analysis
plan. See the walkthrough in the repository README.md and `make help`.
Historical scenario-epoch experiments still render in the review surface from their
persisted `review-projection.json` files under `experiments/`.
