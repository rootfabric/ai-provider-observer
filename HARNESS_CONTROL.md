# Harness Control — Hybrid R8

Canonical mutable state: `config/control/project-state.v1.json`.

## Жизненный цикл

```text
immutable Mission Specification
  ↓
Requirement Manifest + Acceptance Contract + Work Order
  ↓
implementation branch → frozen candidate → machine receipts
  ↓
REVIEW_REQUEST
  ↓
causally-bound signed external REVIEW_PASS
  ↓
verifier-owned adversarial/property/fault evidence
  ↓
Director pre-integration
  ↓
content-addressed external integration authorization
  ↓
pinned integration
  ↓
per-commit closure trajectory proof
  ↓
post-integration validation
  ↓
portable clean-clone validation
  ↓
Director final / derived MISSION_COMPLETE
```

## R8 portable Git truth

Harness не доверяет локальным history overlays. Все Git proof operations выполняются с `GIT_NO_REPLACE_OBJECTS=1`. Если существуют `refs/replace/*`, mission completion fail-closed.

Immutable evidence — это свойство истории, а не совпадение first/final bytes. `A → B → A` считается mutation и не может восстановить immutability.

Closure policy применяется к **каждому commit** в разрешённой траектории. Запрещённый файл нельзя добавить, использовать и затем удалить перед final HEAD.

## R8 exact consumer binding

Для внешней attestation consumer event должен содержать:

```text
attestation
attestation_sha256
attestation_git_blob
```

(поле в JSON называется `attestation_sha256`; пробел выше только визуальный.) Validator берёт attestation bytes из exact consumer commit, а не из финального HEAD.

## Portable clean clone

```bash
./CONTROL_HARNESS.sh portable-check
```

Создаёт обычный `git clone --no-local` и повторяет canonical validation. Для `MISSION_COMPLETE` `validate-ready` включает этот gate автоматически.

## Commands

```bash
./CONTROL_HARNESS.sh validate
./CONTROL_HARNESS.sh validate-active
./CONTROL_HARNESS.sh validate-ready
./CONTROL_HARNESS.sh portable-check
./CONTROL_HARNESS.sh selftest
./CONTROL_HARNESS.sh report
```
