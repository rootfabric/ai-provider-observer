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
