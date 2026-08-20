from __future__ import annotations

import unittest
from functools import wraps
from typing import Callable, Iterable


def verifier_case(case_id: str, *, partitions: Iterable[str], oracle_ids: Iterable[str]):
    """Bind a verifier test method to durable semantic case metadata.

    Metadata is inspected by the base-owned R10 verifier runner after the test
    actually executes. The manifest must match this runtime observation.
    """
    case_id = str(case_id)
    parts = tuple(str(x) for x in partitions)
    oracles = tuple(str(x) for x in oracle_ids)
    if not case_id or not parts:
        raise ValueError("VERIFIER_CASE_METADATA_INVALID")

    def deco(fn: Callable):
        setattr(fn, "_harness_verifier_case", {
            "case_id": case_id,
            "partitions": list(parts),
            "oracle_ids": list(oracles),
        })
        return fn
    return deco


class VerifierTestCase(unittest.TestCase):
    """Verifier testcase with observable semantic-oracle assertions.

    R10 does not pretend to prove arbitrary Python semantics. Instead it makes
    the exact executable test, declared partitions and successful oracle checks
    machine-visible and binds them into the receipt used for completion.
    """

    def setUp(self) -> None:  # pragma: no cover - subclasses may override
        super().setUp()
        self._harness_observed_oracles: set[str] = set()

    def _ensure_oracle_store(self) -> set[str]:
        store = getattr(self, "_harness_observed_oracles", None)
        if store is None:
            store = set()
            self._harness_observed_oracles = store
        return store

    def assert_oracle(self, oracle_id: str, condition: object, msg: str | None = None) -> None:
        if not isinstance(oracle_id, str) or not oracle_id:
            raise ValueError("ORACLE_ID_INVALID")
        self.assertTrue(condition, msg)
        self._ensure_oracle_store().add(oracle_id)

    def assert_oracle_equal(self, oracle_id: str, first: object, second: object, msg: str | None = None) -> None:
        if not isinstance(oracle_id, str) or not oracle_id:
            raise ValueError("ORACLE_ID_INVALID")
        self.assertEqual(first, second, msg)
        self._ensure_oracle_store().add(oracle_id)
