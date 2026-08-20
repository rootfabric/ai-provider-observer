# Hybrid Harness R9 Hardening Template

Чистая Git-болванка Hybrid Harness R9 для новой dogfood/test mission.

R9 сохраняет R8 portable/causal-history guarantees и добавляет hardening, найденный на h-8:

- temporal consistency для signed `issued_at_utc` относительно prerequisite/consumer events;
- bullet-aware normative clause extraction, устойчивый к Markdown line wrapping;
- полностью machine-generated `final-report`;
- mutation selftest вынесен из обычного unittest discovery в отдельный gate.

Основные компоненты:

- `scripts/harness/` — control/validation engine;
- `config/control/harness/` — policies/schemas/rule registry;
- `tests/` — быстрые unit/hardening/traceability regressions;
- `./CONTROL_HARNESS.sh selftest` — отдельный mutation gate;
- `./CONTROL_HARNESS.sh portable-check` — clean-clone proof;
- `./CONTROL_HARNESS.sh final-report` — machine-derived финальный отчёт.

Начните с `START_HERE_RU.md`.

Важно: R9 меняет normative clause extraction с physical-line на logical-bullet semantics. Не обновляйте незавершённый R8 attempt «на месте»: для уже dispatched mission используйте новый attempt/control base и пересоберите Requirement Manifest от R9 `requirements-scan`.
