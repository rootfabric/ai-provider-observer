# Agent Router

Этот файл — короткий router. Он не хранит mutable project state и не является roadmap.

## Всегда

1. Прочитай `config/control/project-state.v1.json`.
2. Подгрузи только нужный route из `config/control/harness/context-routing.v1.json`.
3. Перед изменениями выполни `./CONTROL_HARNESS.sh validate`.
4. До implementation зафиксируй immutable Mission Specification и пройди `requirements-scan`, `requirements-check`, `acceptance-check`.
5. Если mission открыта, завершение локальной роли не означает завершение mission.

## Жёсткие инварианты

- Git/declared verified sink — durable memory; чат не authority.
- Implementer не принимает собственную работу; missing evidence не превращается в PASS.
- Work Order + Requirements Manifest + Acceptance Contract создаются control-only dispatch commit до product mutation.
- Каждый machine-extracted normative clause должен идти `REQ → predicate → partition → verifier case → receipt`.
- Semantic tags добавляют risk/partitions, но не заменяют Requirement Traceability.
- Reviewed candidate exact/fresh; post-candidate product mutation инвалидирует review.
- MEDIUM+ external review требует base-trusted Ed25519 attestation, причинно связанный с уже durable request/evidence digest.
- Pre-signed review/integration approval запрещён.
- Evidence receipt/raw output/events должны быть durable в Git; старый event не редактируется.
- Integration authorization предшествует merge; resulting head проверяется после merge.
- Managed attempt не переписывается reset/amend/rebase; failure оформляется новым ABORTED/SUPERSEDED attempt.
- Runtime mutation требует единственной конфликтующей mutation lease.

## Масштабирование

Большую задачу не растягивать в один prompt/Work Order. Director сохраняет parent mission и создаёт bounded child Work Orders с dependencies, Requirement IDs и acceptance predicates.

## Stop semantics

Допустимы только: `ROLE_BOUNDARY`, `EXTERNAL_WAIT`, `HUMAN_DECISION_REQUIRED`, `SYSTEM_BLOCKED`, `MISSION_COMPLETE`. Для открытой mission всегда сохраняются next actor/action/resume condition.
