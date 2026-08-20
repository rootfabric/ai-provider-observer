# Project Control

Этот seed не содержит project-specific architecture. Добавляйте architecture/ownership/domain policies как main-owned machine contracts до dispatch.

Базовые invariants:
- Git — durable memory; clean clone должен воспроизводить acceptance;
- Mission Specification — immutable normative source;
- normative clauses не могут исчезать между specification и verification;
- control/trust/semantic policy разрешается из immutable `base_sha`;
- external approval должен быть cryptographically valid **и causally bound**;
- critical gates fail closed;
- integration имеет отдельную доказуемую lineage;
- child completion не закрывает parent mission автоматически.
