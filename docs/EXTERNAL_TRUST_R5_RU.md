# External Trust в R5

## Bootstrap

1. До dispatch сгенерируйте reviewer/integrator private seed вне репозитория.
2. В canonical `main` добавьте только public key в `trust-providers.v1.json`.
3. Commit trust configuration.
4. Только следующий commit может быть `WorkOrder` dispatch; его `base_sha` должен уже содержать trust root.

## Key entry

```json
{
  "key_id": "reviewer-prod-01",
  "principal": "independent-reviewer",
  "public_key_b64": "...",
  "allowed_purposes": ["REVIEW_PASS"]
}
```

Integration key обычно имеет отдельный `key_id` и `allowed_purposes: ["INTEGRATION_APPROVE"]`.

## Unsigned attestation

```json
{
  "schema": "hybrid_harness.external_attestation.v1",
  "attestation_id": "REV-001",
  "provider_id": "PROJECT_EXTERNAL_REVIEW",
  "key_id": "reviewer-prod-01",
  "principal": "independent-reviewer",
  "purpose": "REVIEW_PASS",
  "subject_head": "<40-hex-candidate>",
  "mission_id": "MISSION-001",
  "work_order_id": "WO-001",
  "decision": "PASS",
  "issued_at_utc": "2026-01-01T00:00:00Z"
}
```

External signer добавляет `signature_b64`. Implementer не должен иметь private seed.

## Почему signed file можно хранить в Git

Public attestation — evidence, не secret. Его подпись позволяет clean clone независимо перепроверить, что решение сделал holder заранее доверенного private key и что candidate/mission/purpose не были изменены после подписи.
