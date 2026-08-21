# Hybrid Harness R11 — старт новой тестовой mission

Это IDLE-шаблон: product/evidence mission отсутствуют.

## 1. После распаковки

```bash
./CONTROL_HARNESS.sh resume NEW_SESSION
./CONTROL_HARNESS.sh validate
./CONTROL_HARNESS.sh hygiene
./CONTROL_HARNESS.sh portable-check
python3 -m unittest discover -s tests -p 'test_*.py' -v
./CONTROL_HARNESS.sh selftest
```

Ожидание: `effective_status=HARNESS_READY`, baseline gates PASS.

**Каждая новая/возобновлённая агентская сессия сначала выполняет `resume`.** Если предыдущая сессия завершилась timeout:

```bash
./CONTROL_HARNESS.sh resume TIMEOUT
```

`TIMEOUT` — только runtime diagnostic. Следующий actor/action выводится из durable state. Не просите человека писать «продолжай» для routine boundary.

## 2. External trust до dispatch

Private keys не должны находиться в Git и не должны контролироваться implementer session. `keygen` автоматически запрещён после появления active mission.

В `config/control/harness/trust-providers.v1.json` каждый MEDIUM+ key добавляйте на `main` **до dispatch** с полями:

```json
{
  "key_id": "review-key-001",
  "principal": "external-reviewer@example.invalid",
  "custody_id": "review-custody-001",
  "custody_class": "SEPARATE_AGENT",
  "public_key_b64": "...",
  "allowed_purposes": ["REVIEW_PASS"]
}
```

Допустимые external custody classes: `SEPARATE_AGENT`, `REMOTE_SIGNER`, `HARDWARE`. Review и integration должны использовать разные `custody_id`.

Важно: metadata усиливает structural proof, но local Git repo сам по себе не может доказать физическое владение private key. Для настоящей независимости private key должен быть недоступен implementer process.

## 3. Mission до implementation

Создайте из `examples/`:

- `config/control/specifications/MISSION-001.md`
- `config/control/requirements/WO-001.json`
- `config/control/acceptance/WO-001.json`
- `config/control/missions/WO-001.json`

Для каждого partition заранее задайте oracle с `observation_kind`:

```json
{
  "oracle_id": "ORACLE-PRED-001-INVALID",
  "statement": "Invalid operation raises ValidationError and does not mutate state.",
  "observation_kind": "raises",
  "expected_exception": "ValidationError"
}
```

Поддерживаются `equal`, `raises`, `unchanged`.

Проверьте control plane:

```bash
./CONTROL_HARNESS.sh requirements-scan config/control/specifications/MISSION-001.md
./CONTROL_HARNESS.sh requirements-check WO-001
./CONTROL_HARNESS.sh acceptance-check WO-001
```

После durable control-only dispatch переходите в feature branch.

## 4. Если attempt не удался

**Не используйте `reset --hard`, amend, rebase, delete/recreate branch.** Commit-ните полезную failure evidence, вернитесь на clean canonical `main` и выполните:

```bash
./CONTROL_HARNESS.sh attempt-retry WO-002 ATTEMPT-001-B feature/test-b "candidate failed verifier"
```

R11 автоматически:

1. пишет `SUPERSEDED` record старого attempt;
2. сохраняет старую branch/history;
3. копирует immutable specification/requirements/acceptance в новый Work Order binding;
4. создаёт control-only dispatch commit с `base_sha=dispatch parent`;
5. создаёт и переключает на новую feature branch.

## 5. Verifier evidence R11

Verifier tests наследуют `VerifierTestCase`, используют `@verifier_case(...)` и только structured oracle assertions:

```python
self.assert_oracle_equal(oracle_id, actual, expected)
self.assert_oracle_raises(oracle_id, ExpectedError, operation, *args)
self.assert_oracle_unchanged(oracle_id, before, after)
```

Запрещено:

```python
self.assert_oracle(oracle_id, True)
self.assert_oracle_equal(oracle_id, 1, 1)
self.assert_oracle_equal(oracle_id, snapshot, snapshot)
```

Runner:

```bash
./CONTROL_HARNESS.sh verifier-run verifier-adversarial evidence/verifier 'test_*.py'
```

Receipt содержит exact PASS test IDs, case/partition binding, structured oracle observations и oracle-quality findings.

## 6. Final gates

```bash
./CONTROL_HARNESS.sh validate-ready
./CONTROL_HARNESS.sh portable-check
./CONTROL_HARNESS.sh selftest
./CONTROL_HARNESS.sh final-report
```

Доверяйте `effective_status` и `completion_proven`, а не вручную записанному `project_state.status`. Canonical итог — machine `final-report`.
