# R8 Hardening

Источник: fresh analysis результата h-7 на Harness R7.

## Blocking defects R7

- local `git replace` менял identity/history truth и не переносился обычным clone;
- evidence/attestation могли быть изменены и позже возвращены к исходным bytes;
- consumer event ссылался на attestation path без exact content identity;
- forbidden closure files можно было добавить и удалить до final HEAD;
- финальный R7 PASS не был portable proof.

## R8 invariants

```text
NO_REPLACE_REFS
NO_REPLACE_OBJECT_INTERPRETATION
HISTORY_IMMUTABLE_EVIDENCE
CONTENT_ADDRESSED_CONSUMER_BINDING
PER_COMMIT_CLOSURE_TRAJECTORY
PORTABLE_CLEAN_CLONE_VALIDATION
```

## Scope

R8 не меняет product semantics, risk taxonomy или requirement traceability model. Это bounded repair слоя Git durability / causal evidence / closure proof.
