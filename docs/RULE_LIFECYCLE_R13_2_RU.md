# R13.2 — жизненный цикл правил Harness

Цель: правило считается живым не потому, что строка всё ещё лежит в `AGENTS.md`, а потому что Harness может машинно показать, где правило исполняется, когда его последний раз пересматривали и не потеряны ли enforcement/test targets.

## Источники истины

- `config/control/harness/rule-registry.v1.json` — реестр правил.
- `config/control/harness/rule-lifecycle-policy.v1.json` — cadence/fail-closed policy.
- `config/control/harness/rule-review-state.v1.json` — Git-tracked last review state.
- `scripts/harness/rules.py` — machine health engine.

Review-state не является runtime cache: он коммитится и переносится в fresh clone.

## Статусы

- `ACTIVE` — metadata/targets существуют, review не просрочен.
- `STALE` — review due date прошла.
- `BROKEN` — lifecycle metadata/review state некорректны.
- `ORPHANED` — правило ссылается на исчезнувший enforcement или test target.
- `DRAFT`, `DEPRECATED`, `SUPERSEDED` — явные lifecycle-состояния.

Приоритет ошибки: BROKEN/ORPHANED всегда ERROR. STALE для SECURITY_INVARIANT — ERROR; для остальных классов — WARN по текущей policy.

## Cadence по умолчанию

| Класс | Review interval |
|---|---:|
| SECURITY_INVARIANT | 45 дней |
| CONTROL_INVARIANT | 90 дней |
| PROCESS_GUARD | 120 дней |
| EFFICIENCY_INVARIANT | 180 дней |

У конкретного правила можно задать `review_every_days`.

## Проверка здоровья

~~~bash
./CONTROL_HARNESS.sh rules
./CONTROL_HARNESS.sh rules --json
./CONTROL_HARNESS.sh rule-show CTRL-EXTERNAL-TRUST-R5
~~~

Пример healthy:

~~~text
rule_id=CTRL-EXTERNAL-TRUST-R5
status=ACTIVE
last_reviewed=2026-08-28
review_due=2026-10-12
~~~

Если enforcement-файл исчез:

~~~text
ORPHANED CTRL-... severity=ERROR ... missing enforcement: trust.py
RULES: FAIL
~~~

## Пересмотр существующего правила

~~~bash
./CONTROL_HARNESS.sh rule-review CTRL-EXTERNAL-TRUST-R5 \
  --by DIRECTOR \
  --note "Verified trust.py/review-policy behavior against current architecture"
~~~

Команда:

1. требует отсутствие active mission;
2. требует clean worktree;
3. запрещает future review date;
4. обновляет только tracked review-state;
5. создаёт bounded Git commit;
6. выводит новый `REVIEW_DUE`.

## Добавление нового правила

Сначала должны существовать implementation/enforcement и тест.

~~~bash
./CONTROL_HARNESS.sh rule-add CTRL-DB-MIGRATION-001 \
  --class CONTROL_INVARIANT \
  --source "Database migration safety" \
  --applies-when "alembic/versions/** changes" \
  --enforcement machine \
  --enforced-by scripts/harness/active_validation.py \
  --retirement CONTROL_REVISION_REQUIRED \
  --owner proof-kernel \
  --test tests/test_rule_lifecycle.py \
  --note "Initial activation"
~~~

Для нового machine/mixed rule обязательны `owner` и минимум один существующий `--test`. Ссылки на отсутствующие файлы не принимаются.

Новые правила получают `lifecycle_version=1`, `status=ACTIVE`, `introduced_revision=R13.2` и первичный review record.

## Что значит «протухло»

Freshness считается от Git-tracked `last_reviewed` плюс cadence класса/правила. Календарная свежесть — только одна ось. Одновременно Harness проверяет:

- существует ли каждое `enforced_by`;
- существуют ли явные `tests` у lifecycle-v1 правил;
- есть ли review state;
- валидна ли дата review;
- не находится ли дата в будущем;
- допустим ли declared status.

Таким образом простое изменение даты не скрывает ORPHANED/BROKEN rule.

## Self-test

Mutation selftest дополнительно портит:

1. review date security-rule → ожидается `RULE_STALE`;
2. enforcement target → ожидается `RULE_ORPHANED`.

Это проверяет, что lifecycle health реально включён в общий Harness audit, а не существует только как отдельный отчёт.

## Ограничение

R13.2 подтверждает structural/freshness health правила. Он не может математически доказать, что тест семантически полностью покрывает intent правила. Для критических правил следующим усилением остаётся per-rule mutation test/attack case: намеренно ослабить enforcement и доказать, что связанный тест/сам Harness краснеет.
