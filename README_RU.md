# Hybrid Harness R8 Empty Test Template

Чистая Git-болванка Hybrid Harness R8 для запуска новой dogfood/test mission.

Начните с [`START_HERE_RU.md`](START_HERE_RU.md).

Содержимое:
- `scripts/harness/` — R8 control/validation engine;
- `config/control/harness/` — R8 policies/schemas;
- `tests/` — self/hardening/portability/traceability tests Harness;
- `src/`, `tests/product/` — пустые product slots;
- `evidence/` — пустые evidence slots;
- `examples/` — нейтральные заготовки specification/work order/requirements/acceptance;
- `tools/external_attestation.py` — keygen/sign helper; private seeds разрешены только вне repository.

Template не содержит MiniLedger implementation, его завершённую mission, receipts/attestations или старую h-7 Git history.
