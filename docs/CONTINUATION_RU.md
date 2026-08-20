# Mission Continuation / Role Handoff

Цель слоя continuation — не ослабить gates, а не позволить workflow потерять исходную цель на границе ролей.

## Классы переходов

- `ROLE_BOUNDARY`: работа штатно продолжается другой ролью.
- `EXTERNAL_WAIT`: ожидается уже запущенная внешняя проверка.
- `HUMAN_DECISION_REQUIRED`: нужен реальный human choice/approval.
- `SYSTEM_BLOCKED`: нужен repair.
- `MISSION_COMPLETE`: глобальная success condition выполнена.

## Review handoff

```text
Implementer GREEN
→ Reviewer required
→ Reviewer сам сохраняет PASS/FAIL в declared durable sink
→ exact-head freshness проверяется
→ Director/Verifier получает продолжение
```

Запрещённый anti-pattern:

```text
Reviewer → PASS в чат → человек копирует PASS → следующий агент
```

## Старые Work Orders

`mission` и `handoff` optional. Это позволяет внедрять continuation постепенно без переписывания исторических ledgers.
