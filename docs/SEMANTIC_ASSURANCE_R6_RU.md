# R6 — Semantic Assurance

R5 доказал provenance, trust и Git-lineage, но MiniLedger показал новый false positive:
валидные 30/30 тестов не проверяли `A -> A`, поэтому predicate `total_balance_conservation`
был объявлен PASS, хотя self-transfer увеличивал деньги.

R6 добавляет слой между спецификацией и evidence:

```text
immutable Mission Specification (exists in base_sha)
        ↓
machine semantic inference
        ↓
machine-owned risk floor
        ↓
immutable Acceptance Contract at dispatch
        ↓
predicate + domain partitions
        ↓
Implementer candidate
        ↓
Verifier-owned adversarial/property/fault cases
        ↓
predicate → case → receipt coverage
        ↓
MEDIUM+ signed external review
        ↓
MISSION_COMPLETE
```

## 1. Machine-owned semantic risk

`config/control/harness/semantic-risk-policy.v1.json` содержит контролируемую taxonomию.
Например `persistence`, `transaction`, `exactly_once`, `conservation`, `concurrency`
автоматически дают минимум `MEDIUM`; security/authority/cross-server — минимум `HIGH`.
Implementer может повысить risk, но не понизить machine floor.

Semantic inference читает исходную Mission Specification из `base_sha`, поэтому нельзя
обойти floor, переписав `success_condition` после постановки задачи.

## 2. Acceptance Contract

До implementation создаётся `config/control/acceptance/<WO>.json` в том же dispatch commit,
что Work Order. После dispatch контракт immutable.

Каждый predicate содержит:
- устойчивый `predicate_id`;
- statement;
- class;
- semantic tags;
- domain partitions;
- требуемые evidence modes;
- признак `verifier_owned`.

Universal predicates (`CONSERVATION`, `ATOMICITY`, `IDEMPOTENCY`, etc.) не могут быть
доказаны одним happy-path case.

Для conservation R6 policy требует как минимум:
`distinct_entities`, `same_entity`, `boundary_amount`.
Именно `same_entity` ловит MiniLedger self-transfer defect.

## 3. Verifier evidence

`evidence/verifier/verification-manifest.json` связывает:

```text
predicate_id
  → case_ids
  → covered_partitions
  → machine receipt refs
```

Verifier-owned predicate обязан иметь verifier-owned case и receipt, который хэширует
реальный verifier artifact под `evidence/verifier/**`.

Для MEDIUM+ хотя бы один adversarial/fault/property case обязателен, а Implementer test suite
сам по себе недостаточен.

## 4. Evidence receipt R6

Receipt v4 различает:
- `subject_head` — frozen candidate;
- `execution_head` — closure-only head, где может существовать verifier evidence;
- `input_files` — хэшированные durable verifier artifacts.

Это позволяет независимому Verifier добавлять свои тесты после freeze, не меняя product candidate.

## 5. Fail-closed

Если semantic tag не объявлен, domain partition отсутствует, verifier coverage неполон,
risk ниже machine floor или исходная specification появилась после base — completion блокируется.

Harness всё ещё не может математически доказать полноту любой человеческой спецификации.
R6 делает следующий сильный шаг: он не принимает generic predicate PASS без явного domain
partitioning и evidence coverage, а MEDIUM+ дополнительно требует внешний trust domain.
