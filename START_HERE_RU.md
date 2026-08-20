# Hybrid Harness R8 — чистая болванка для новой тестовой задачи

Это **IDLE-шаблон**: активной mission, product code и acceptance evidence нет. Harness/control уже установлен и должен проходить baseline validation.

## 1. Сразу после распаковки

```bash
./CONTROL_HARNESS.sh status
./CONTROL_HARNESS.sh validate
./CONTROL_HARNESS.sh hygiene
./CONTROL_HARNESS.sh selftest
./CONTROL_HARNESS.sh portable-check
PYTHONPATH=tests:. python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Ожидание: baseline Harness PASS, `active_mission=null`.

## 2. Настройте внешний trust ДО dispatch mission

Создайте private seeds **за пределами репозитория**:

```bash
python3 tools/external_attestation.py keygen --private-out "$HOME/.hybrid-harness-test/reviewer.seed"
python3 tools/external_attestation.py keygen --private-out "$HOME/.hybrid-harness-test/integrator.seed"
```

Скопируйте выведенные `public_key_b64` в:

`config/control/harness/trust-providers.v1.json`

для двух разных key IDs/purposes (`REVIEW_PASS`, `INTEGRATION_APPROVE`), затем commit на `main`. Private seeds в Git не добавлять.

## 3. Опишите тестовую задачу

Возьмите за основу `examples/` и создайте:

- `config/control/specifications/MISSION-001.md`
- `config/control/requirements/WO-001.json`
- `config/control/acceptance/WO-001.json`
- `config/control/missions/WO-001.json`

Сначала выполните:

```bash
./CONTROL_HARNESS.sh requirements-scan config/control/specifications/MISSION-001.md
./CONTROL_HARNESS.sh requirements-check WO-001
./CONTROL_HARNESS.sh acceptance-check WO-001
```

Не начинайте product implementation, пока specification + requirements + acceptance + Work Order не согласованы и не находятся в durable base history.

## 4. Активируйте mission

Обновите `config/control/project-state.v1.json` только после control-plane commit: задайте `active_work_order`, `active_mission`, epoch/checkpoint/lease согласно Work Order. Product mutation делайте в отдельной feature branch.

## 5. Product работа

- код: `src/`
- implementer tests: `tests/product/`
- verifier evidence: `evidence/verifier/`
- machine receipts: создавайте только через `CONTROL_HARNESS.sh evidence-run`
- immutable events: через `CONTROL_HARNESS.sh event-add`

Перед review:

```bash
./CONTROL_HARNESS.sh freeze-candidate
./CONTROL_HARNESS.sh validate-active
./CONTROL_HARNESS.sh report
```

После полного review/integration lifecycle финальный gate:

```bash
./CONTROL_HARNESS.sh validate-ready
./CONTROL_HARNESS.sh portable-check
```

## R8 invariants, которые нельзя обходить

- никаких `git replace` refs;
- никакого amend/rebase/reset managed proof history;
- immutable evidence нельзя mutate→revert;
- closure/source paths проверяются commit-by-commit;
- consumer event должен bind exact attestation SHA-256 + Git blob;
- финальный proof должен воспроизводиться в обычном clean clone.
