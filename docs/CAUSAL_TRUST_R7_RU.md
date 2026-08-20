# R7 Causal Trust

## Проблема R6

В `h-6` Ed25519 review и integration attestations были математически валидны, но оба были добавлены в Git до REVIEW_REQUEST и Director readiness. R6 доказывал identity ключа, но не причинный порядок решения.

## Attestation v2

Подписанный payload содержит:

```text
prerequisite_event
prerequisite_event_sha256
evidence_paths[]
evidence_digest
subject_head
mission_id
work_order_id
purpose / decision
```

Validator находит commit первого добавления attestation и его parent. В parent уже обязаны существовать prerequisite event и весь exact evidence set. Digest пересчитывается по Git blobs именно этого parent commit. Attestation add commit обязан быть потомком prerequisite event commit.

Следствия:
- REVIEW_PASS нельзя подготовить до REVIEW_REQUEST;
- INTEGRATION_APPROVE нельзя подготовить до DIRECTOR_PRE_INTEGRATION;
- поздняя подмена evidence меняет digest;
- произвольный `proof_ref` ничего не доказывает.

Signature всё ещё не доказывает когнитивную независимость, если один субъект владеет всеми private keys. Trust root и private-key custody остаются внешней организационной границей.
