# Agent Router — Hybrid Harness R12 Lean

## Normal path (token-bounded)

1. Start/resume with `./CONTROL_HARNESS.sh brief [RUNTIME_REASON]`.
2. Read **only** paths printed by `must_read=`. Do not read `scripts/harness/**`, policy/schema internals, raw receipts/logs, or old evidence during normal work.
3. Before product mutation run `./CONTROL_HARNESS.sh validate-active` once. Do not repeatedly run `validate-ready` before closure.
4. Develop and run product tests locally until green. For the final exact candidate use one atomic command: `candidate-check RECEIPT -- TEST_COMMAND`.
5. Commit verifier tests + manifest once, then use `verifier-check`. Use `event-record` for append-only events; it commits the event automatically.
6. After any failure run `brief`; use `diagnose` only when the compact route is insufficient. Use `diagnose --full` or Harness source inspection only for a targeted `SYSTEM_BLOCKED` Harness defect.
7. Failed attempts use `attempt-retry`; never reset/rebase/delete history.
8. At closure run `validate-ready`, `portable-check`, `selftest`, `final-report` once each. Machine report is authority.

## Precision invariants

- Git/control evidence is durable memory; chat is not authority.
- `effective_status` is derived. Declared completion without proof is invalid.
- `REQ → predicate → partition → CASE → exact executed test_id → structured oracle observation → receipt` remains mandatory.
- `assert_oracle(..., True)` and trivial/no-op oracle assertions are forbidden.
- MEDIUM+ reviewer/integrator use distinct base-trusted external custody domains; implementer cannot self-accept.
- Reviewed candidate is exact/fresh; evidence is immutable/content-addressed; missing proof fails closed.

## Stop classes

Only `ROLE_BOUNDARY`, `EXTERNAL_WAIT`, `HUMAN_DECISION_REQUIRED`, `SYSTEM_BLOCKED`, `MISSION_COMPLETE`. Runtime timeout/interrupt is diagnostic only; resume with `brief TIMEOUT` / `brief INTERRUPTED`.
