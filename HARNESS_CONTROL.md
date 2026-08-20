# Harness Control — Hybrid R10

Canonical mutable state: `config/control/project-state.v1.json`.

## Lifecycle

```text
Mission Specification
  ↓
Requirement Manifest + Acceptance Contract + Work Order
  ↓
implementation branch → frozen candidate → product machine receipts
  ↓
REVIEW_REQUEST → signed external REVIEW_PASS
  ↓
verifier-owned tests through base-owned verifier runner
  ↓
case-derived partition coverage + runtime oracle proof
  ↓
Director pre-integration → signed integration authorization
  ↓
pinned integration → post-integration validation
  ↓
portable clean-clone proof → Director final → derived MISSION_COMPLETE
```

## Retained R8/R9 invariants

- `git replace` refs are forbidden and all Git proof disables replacement objects.
- immutable evidence means no mutate→revert history laundering.
- closure/source policy is checked commit-by-commit.
- consumer events bind exact attestation SHA-256 + Git blob.
- `issued_at_utc` must be causally consistent with prerequisite/consumer times.
- final proof must reproduce in a normal clean clone.
- `final-report` is machine-derived.
- mutation selftest is a separate mandatory gate.

## R10 semantic verifier protocol

R10 does not trust manifest prose such as `covered_partitions` by itself.

For every acceptance predicate partition:

```text
base-owned Acceptance Contract partition_oracle
  ↓
Verifier Manifest CASE-* partition + oracle_id + exact test_id
  ↓
base-owned verifier_runner executes that exact test
  ↓
runtime PASS record contains same case/partition/oracle metadata
  ↓
VerifierTestCase.assert_oracle* records successful oracle observation
  ↓
durable verifier receipt binds exact runtime result
```

Completion fails if a partition is claimed but absent from referenced cases, if a test ID was not actually observed PASS, if runtime metadata differs from the manifest, or if a required oracle was not observed.

### Verifier test API

```python
from verifier_api import VerifierTestCase, verifier_case

class MyVerifierTests(VerifierTestCase):
    @verifier_case(
        "CASE-001",
        partitions=["boundary"],
        oracle_ids=["ORACLE-PRED-001-BOUNDARY"],
    )
    def test_boundary(self):
        actual = ...
        self.assert_oracle_equal(
            "ORACLE-PRED-001-BOUNDARY",
            actual,
            expected,
        )
```

Run verifier tests only through:

```bash
./CONTROL_HARNESS.sh verifier-run verifier-adversarial evidence/verifier 'test_*.py'
```

## Normative parser R10

Both wrapped list items and ordinary wrapped prose paragraphs are folded into logical Markdown units before sentence extraction. Pure formatting line-wrap changes must not change logical clause text/hash.
