# Hybrid Harness R11 — Efficiency + Semantic Integrity Template

Чистая Git-болванка R11 для новой dogfood/test mission. R11 сохраняет R8–R10 guarantees и закрывает проблемы, проявившиеся в h-10.

Главные изменения:

- **Structured oracle evidence.** `assert_oracle(id, True)` запрещён. Verifier фиксирует concrete `equal`, `raises` или `unchanged` observation; очевидные no-op assertions отбрасываются base-owned runner.
- **Safe retry.** `attempt-retry` supersede-ит старый attempt, сохраняет старую ветку/историю, создаёт новый Work Order/attempt и feature branch без `reset/amend/rebase`.
- **Derived effective status.** `status`/`final-report` различают `declared_status` и machine-derived `effective_status`; ложный `MISSION_COMPLETE` отображается как `INVALID_COMPLETION`.
- **Durable resume.** `resume [runtime_reason]` восстанавливает следующий шаг только из Git/control state; TIMEOUT/interrupt остаётся диагностикой runtime, а не Harness verdict.
- **External custody metadata.** MEDIUM+ review/integration keys обязаны иметь `custody_id` + внешний `custody_class`; review/integration custody domains должны различаться. Локальный Harness честно не утверждает, что способен доказать физическое владение ключом.

Базовые команды:

```bash
./CONTROL_HARNESS.sh resume
./CONTROL_HARNESS.sh validate
./CONTROL_HARNESS.sh hygiene
python3 -m unittest discover -s tests -p 'test_*.py' -v
./CONTROL_HARNESS.sh selftest
./CONTROL_HARNESS.sh portable-check
./CONTROL_HARNESS.sh final-report
```

Начните с `START_HERE_RU.md`.
