# R12 Lean — быстрый старт

1. `./CONTROL_HARNESS.sh brief NEW_SESSION`.
2. Читай только `must_read=`. В нормальном ходе не открывай `scripts/harness/**`, policy/schema internals и `evidence/raw/**`.
3. До mutation один раз `validate-active`; подготовь spec/requirements/acceptance/Work Order на canonical base и dispatch feature branch.
4. Реализуй продукт и гоняй только product tests до green.
5. Один раз зафиксируй exact candidate и receipt:
   `./CONTROL_HARNESS.sh candidate-check candidate-tests -- <product-test-command>`.
6. Verifier tests + manifest commit-ятся один раз; затем:
   `./CONTROL_HARNESS.sh verifier-check verifier-adversarial evidence/verifier 'test_*.py'`.
7. Durable события: `event-record ...`; failed attempt: `attempt-retry ...`.
8. После ошибки сначала `brief`; затем `diagnose`. `diagnose --full` и чтение Harness source — только для точечного SYSTEM_BLOCKED.
9. Финально по одному разу: `validate-ready`, `portable-check`, `selftest`, `final-report`.

Для полного stdout конкретной диагностики: `HARNESS_VERBOSE=1 <command>`.
