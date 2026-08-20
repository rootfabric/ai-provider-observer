# HYBRID HARNESS R10 — H99 SEMANTIC-PROOF HARDENING

## Исходный дефект h-99

R9 принимал `covered_partitions` из verifier manifest как самостоятельное доказательство. Referenced cases могли реально содержать только часть partitions, а manifest всё равно заявлял полный predicate coverage. В h-99 это скрыло `payload_conflict_invalid_payload`: продукт возвращал validation error вместо требуемого conflict, но mission получила ложный `MISSION_COMPLETE`.

## R10

1. Predicate partition coverage выводится из union partitions referenced cases.
2. `covered_partitions` не может содержать partition, отсутствующий в этих cases.
3. Verification Manifest v2 требует у каждого case exact `test_ids` и `oracle_ids`.
4. Verifier tests исполняются base-owned `verifier_runner.py`.
5. Evidence receipt v5 сохраняет exact runtime PASS test IDs и runtime case/partition/oracle metadata.
6. Acceptance Contract заранее определяет `partition_oracles` для каждого partition.
7. `VerifierTestCase.assert_oracle*` делает успешное semantic oracle observation машинно видимым.
8. Completion требует совпадения manifest metadata с runtime metadata и фактического observation всех contract-owned oracle IDs.
9. Normative parser теперь fold-ит обычные prose paragraphs до sentence extraction, а не только Markdown bullets.

## Regression против h-99

R10 analysis исторического h-99 manifest выдаёт fail-closed, включая:

- `VERIFIER_MANIFEST_SCHEMA_INVALID`;
- `VERIFIER_CASE_TEST_IDS_MISSING`;
- `VERIFIER_CASE_ORACLES_MISSING`;
- `PREDICATE_PARTITION_OVERCLAIMED`;
- `PREDICATE_PARTITION_NOT_BACKED_BY_CASE`.

В частности `PRED-IDEMP-009` больше не может заявить `payload_conflict_invalid_payload`, если referenced case содержит только `payload_conflict`.

## Baseline R10 gates

- Harness unit tests: 60/60 PASS.
- Mutation selftest: 38/38 negative mutations detected.
- `validate`: PASS.
- `hygiene`: PASS.
- `portable-check`: PASS.
- `final-report`: PASS on idle template.

## Migration

Не обновлять уже dispatched R9 mission на месте. Для R10 создаётся новый control base/attempt, Acceptance Contract дополняется `partition_oracles`, verifier tests переводятся на `VerifierTestCase` + `@verifier_case`, а verifier receipt создаётся только через `CONTROL_HARNESS.sh verifier-run`.
