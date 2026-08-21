# Agent Router — Hybrid Harness R11

## На каждом старте/возобновлении

1. Выполни `./CONTROL_HARNESS.sh resume [RUNTIME_REASON]`.
2. Прочитай только active Work Order + route из `context-routing.v1.json`.
3. Перед mutation выполни `validate`.
4. Routine `ROLE_BOUNDARY` не является human blocker: сохраняй durable evidence и маршрутизируй следующую роль без использования человека как courier.

## Инварианты

- Git/control evidence — durable memory; chat не authority.
- `effective_status` derived; declared `MISSION_COMPLETE` без proof = `INVALID_COMPLETION`.
- Mission Specification предшествует dispatch; `REQ → predicate → partition → CASE → exact test_id → structured oracle observation → receipt`.
- `assert_oracle(..., True)` и очевидные no-op oracle assertions запрещены.
- MEDIUM+ reviewer/integrator используют base-trusted keys с разными external `custody_id`; private keys недоступны implementer session.
- Failed attempt не переписывается. Используй `attempt-retry`; старые branch/commits остаются.
- Reviewed candidate exact/fresh; post-candidate product mutation инвалидирует review.
- External approval causal, immutable, content-addressed и temporal-consistent.
- Missing evidence fails closed.

## Stop classes

Только: `ROLE_BOUNDARY`, `EXTERNAL_WAIT`, `HUMAN_DECISION_REQUIRED`, `SYSTEM_BLOCKED`, `MISSION_COMPLETE`. Runtime `TIMEOUT`/interrupt — не stop class Harness; после них запускай `resume`.
