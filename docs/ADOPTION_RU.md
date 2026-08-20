# Как внедрять Hybrid Harness R4

1. Инициализируйте Git и зафиксируйте baseline.
2. Не храните current mutable state в root prose.
3. Для active Work Order обязательно задайте `branch`, `started_at_utc`, `implementer_actor_id`, `mission`, `handoff`, risk и integration policy.
4. Product candidate сначала коммитится, затем фиксируется `freeze-candidate`.
5. Тесты, которые входят в evidence, запускайте через `evidence-run`.
6. После candidate разрешён только closure/evidence diff. Любой product diff требует нового candidate и нового review.
7. Review/verification пишутся как отдельные durable events; MEDIUM+ не должен принимать self-declared independent review.
8. Не amend/reset/rebase active managed branch.
9. Не объявляйте COMPLETE вручную: `validate-active` должен доказать состояние.
10. Canonical merge выполняйте только после durable authorization либо заранее объявленного whitelist.

При переносе в большой существующий проект сначала включайте R4 как наблюдающий слой, затем делайте enforcement после baseline audit.
