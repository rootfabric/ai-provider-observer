# Harness Control — Hybrid R12 Lean

R12 preserves every R8–R11 fail-closed proof invariant and changes the **agent interface**, not the proof strength.

## Normal API

- `brief [RUNTIME_REASON]` — bounded current route; this replaces exploratory reading of Harness internals.
- `diagnose` — grouped blockers; `diagnose --full` only for a targeted repair.
- `candidate-check RECEIPT -- COMMAND...` — freeze exact candidate, machine-run tests, commit lock+receipt in one transition.
- `verifier-check RECEIPT [DIR] [PATTERN]` — run semantic verifier and commit generated receipt/raw evidence.
- `event-record PHASE ACTOR VERDICT [...]` — append + commit one immutable event.
- `attempt-retry ...` — preserve failed history and start a fresh attempt.

Raw test/verifier output remains durable in `evidence/raw/**` but is not echoed to the LLM by default. Set `HARNESS_VERBOSE=1` only when a specific failure needs full detail.

## Precision unchanged

Exact-head, clean-clone portability, immutable causal evidence, external custody, temporal consistency, requirement/partition/test/oracle runtime binding, structured observations, and fail-closed completion remain machine-enforced.

## R13.2 Rule Lifecycle API

Rule policy is durable Git state, not chat memory.

- `rules [--json]` — summarize ACTIVE/STALE/BROKEN/ORPHANED rules; exits non-zero on ERROR-severity health.
- `rule-show RULE_ID [--json]` — show one rule's class, status, last review, due date, cadence and missing targets.
- `rule-review RULE_ID [--date YYYY-MM-DD] [--by ACTOR] [--note TEXT]` — refresh review state and create a bounded control commit. Future dates are rejected.
- `rule-add RULE_ID --class CLASS --source TEXT --applies-when TEXT --enforcement machine|mixed|prose --enforced-by PATH ... --retirement POLICY --owner OWNER --test PATH ...` — add a new lifecycle-v1 rule. Machine/mixed rules require at least one existing test target.

Review cadence defaults: SECURITY 45d, CONTROL 90d, PROCESS 120d, EFFICIENCY 180d. Security staleness is an ERROR; other stale classes remain visible WARN findings. Missing enforcement/test targets are ORPHANED/ERROR. Invalid lifecycle metadata is BROKEN/ERROR.
