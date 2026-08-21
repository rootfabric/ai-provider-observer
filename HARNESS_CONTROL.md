# Harness Control — Hybrid R11

R11 = R8 portable causal history + R9 temporal/report/parser hardening + R10 case-derived verifier proof + h-10 efficiency/integrity repair.

## Новое в R11

### Structured semantic observations
Contract oracle объявляет `observation_kind`. Runtime evidence хранит structured observation, а base-owned runner отклоняет generic boolean oracle, literal-vs-literal и same-expression no-op assertions.

### Safe attempt retry
`attempt-retry` supersede-ит failed attempt и создаёт новый dispatch без history rewrite. Старый Work Order/branch остаются inspectable.

### Derived state
`declared_status` — input. `effective_status` — machine result. Completion существует только при `completion_proven=true`.

### Resume without chat reconstruction
`resume [reason]` отделяет runtime termination (`TIMEOUT`, `INTERRUPTED`) от Harness continuation и выдаёт next actor/action из durable state.

### External custody honesty
Base trust key содержит `custody_id`/`custody_class`; review/integration custody domains различны. Local-only Harness не притворяется доказательством физического custody: реальная независимость требует private key вне implementer process.
