# Hybrid Harness R10 Semantic-Proof Template

Чистая Git-болванка Hybrid Harness R10 для новой dogfood/test mission.

R10 сохраняет R8/R9 guarantees (portable Git history, exact causal attestation binding, temporal consistency, machine final report) и закрывает h-99 false-positive semantic coverage:

- `covered_partitions` больше не считается доказательством сам по себе: coverage выводится из partitions реально referenced verifier cases;
- каждый verifier case обязан bind exact `test_ids`;
- verifier tests запускаются через base-owned `scripts/harness/verifier_runner.py`;
- durable verifier receipt содержит exact runtime PASS test IDs + case/partition/oracle metadata;
- acceptance contract заранее определяет `partition_oracles` для каждого partition;
- verifier test обязан успешно наблюдать соответствующие oracle IDs через `VerifierTestCase.assert_oracle*`;
- обычные Markdown prose paragraphs теперь fold-ятся до sentence parsing так же, как wrapped bullets.

Основные команды:

```bash
./CONTROL_HARNESS.sh validate
./CONTROL_HARNESS.sh hygiene
python3 -m unittest discover -s tests -p 'test_*.py' -v
./CONTROL_HARNESS.sh selftest
./CONTROL_HARNESS.sh portable-check
./CONTROL_HARNESS.sh final-report
```

Verifier evidence для активной mission запускайте через:

```bash
./CONTROL_HARNESS.sh verifier-run verifier-adversarial evidence/verifier 'test_*.py'
```

Начните с `START_HERE_RU.md`.
