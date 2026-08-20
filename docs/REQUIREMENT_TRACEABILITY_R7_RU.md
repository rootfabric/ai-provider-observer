# R7 Requirement Traceability

## Проблема R6

R6 мог полностью доказать неполный Acceptance Contract. В `h-6` Mission Specification содержала `No account balance may become negative`, но это требование не попало ни в predicate, ни в verifier suite; `create_account("A", -1)` поэтому прошёл.

## R7

Mission Specification существует до dispatch. `requirements-scan` извлекает нормативные clauses детерминированно из MUST/SHALL/required/forbidden/no...may и эквивалентных русских форм, а также imperative/list rules в нормативных разделах.

Dispatch добавляет `config/control/requirements/<WO>.json`. Manifest содержит Requirement IDs и exact source clause IDs. Validator пересчитывает clauses из base-owned specification и fail-closed проверяет:

- каждый machine-extracted normative clause отображён хотя бы в один `REQ-*`;
- Requirement ID двунаправленно связан с acceptance predicate;
- requirement partitions покрываются linked predicates;
- каждый linked predicate присутствует в verifier manifest coverage;
- evidence map перечисляет все Requirement IDs как proven coverage.

Semantic taxonomy остаётся дополнительным инструментом: она повышает risk и добавляет domain partitions, но больше не определяет полный список требований.

## Ограничение

Ни один детерминированный parser естественного языка не является универсальным theorem prover. Поэтому MEDIUM+ external reviewer получает specification + Requirements Manifest + Acceptance Contract в подписываемом evidence digest. Для критичных доменов рекомендуется structured requirement authoring (`MUST`, отдельный bullet на requirement) и project-specific semantic/domain policies.
