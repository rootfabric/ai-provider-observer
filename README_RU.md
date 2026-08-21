# Hybrid Harness R12 — Lean / Token-Bounded Template

R12 — это R11 с более дешёвым агентским протоколом. Проверки не ослаблены: сокращены повторные LLM-шаги, объём stdout и необходимость читать внутренности Harness.

Основной цикл:

```bash
./CONTROL_HARNESS.sh brief NEW_SESSION
# читать только must_read из brief
./CONTROL_HARNESS.sh validate-active
# implement + локальные product tests
./CONTROL_HARNESS.sh candidate-check candidate-tests -- python3 -m unittest discover -s tests/product -p 'test_*.py'
# verifier tests + manifest один раз commit
./CONTROL_HARNESS.sh verifier-check verifier-adversarial evidence/verifier 'test_*.py'
# события фиксировать через event-record
# closure: validate-ready, portable-check, selftest, final-report
```

`HARNESS_VERBOSE=1` включает полный диагностический вывод. По умолчанию findings дедуплицируются и ограничиваются, а raw machine output хранится как durable evidence без возврата в контекст модели.

См. `START_HERE_RU.md`.
