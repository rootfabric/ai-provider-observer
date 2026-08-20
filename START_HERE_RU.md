# Hybrid Harness R10 — чистая болванка для новой тестовой задачи

Это IDLE-шаблон: активной mission и product evidence нет.

## 1. Проверка после распаковки

```bash
./CONTROL_HARNESS.sh status
./CONTROL_HARNESS.sh validate
./CONTROL_HARNESS.sh hygiene
./CONTROL_HARNESS.sh portable-check
python3 -m unittest discover -s tests -p 'test_*.py' -v
./CONTROL_HARNESS.sh selftest
```

Ожидание: `HARNESS_READY`, `active_mission=null`, все baseline gates PASS.

## 2. External trust до dispatch

Private seeds храните вне Git:

```bash
python3 tools/external_attestation.py keygen --private-out "$HOME/.hybrid-harness-test/reviewer.seed"
python3 tools/external_attestation.py keygen --private-out "$HOME/.hybrid-harness-test/integrator.seed"
```

Public keys внесите в `config/control/harness/trust-providers.v1.json` и commit на `main` до Work Order dispatch.

## 3. Опишите mission до product implementation

Используйте `examples/` и создайте:

- `config/control/specifications/MISSION-001.md`
- `config/control/requirements/WO-001.json`
- `config/control/acceptance/WO-001.json`
- `config/control/missions/WO-001.json`

Каждый acceptance predicate обязан иметь `partition_oracles`: expected oracle ID + statement для каждого partition.

Проверьте control plane:

```bash
./CONTROL_HARNESS.sh requirements-scan config/control/specifications/MISSION-001.md
./CONTROL_HARNESS.sh requirements-check WO-001
./CONTROL_HARNESS.sh acceptance-check WO-001
```

Только после durable dispatch начинайте implementation в отдельной feature branch.

## 4. Product evidence

- код: `src/`
- implementer tests: `tests/product/`
- machine product receipts: `./CONTROL_HARNESS.sh evidence-run ...`
- immutable events: `./CONTROL_HARNESS.sh event-add ...`

Перед review:

```bash
./CONTROL_HARNESS.sh freeze-candidate
./CONTROL_HARNESS.sh validate-active
./CONTROL_HARNESS.sh report
```

## 5. R10 verifier evidence

Verifier tests кладите в `evidence/verifier/` и наследуйте от `VerifierTestCase`.
Каждый test method связывайте decorator `@verifier_case(...)` с durable CASE ID, partitions и oracle IDs из Acceptance Contract.

Пример: `examples/verifier-test.template.py`.

Запускайте verifier только base-owned runner:

```bash
./CONTROL_HARNESS.sh verifier-run verifier-adversarial evidence/verifier 'test_*.py'
```

Этот command создаёт durable receipt с exact runtime PASS test IDs и observed oracle IDs. Затем создайте `verification-manifest.json` по `examples/verification-manifest.template.json`.

R10 fail-closed если:

- `covered_partitions` содержит partition, которого нет в referenced cases;
- required partition отсутствует в union referenced cases;
- manifest `test_id` не был реально выполнен и PASS;
- runtime case/partition/oracle metadata отличается от manifest;
- required contract oracle не был успешно observed тестом.

## 6. Final gates

```bash
./CONTROL_HARNESS.sh validate-ready
./CONTROL_HARNESS.sh portable-check
./CONTROL_HARNESS.sh selftest
./CONTROL_HARNESS.sh final-report
```

Не переписывайте machine-derived counts/HEAD вручную: canonical итог — `final-report`.
