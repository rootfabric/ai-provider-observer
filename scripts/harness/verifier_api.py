from __future__ import annotations

import hashlib
import json
import unittest
from typing import Any, Callable, Iterable


def verifier_case(case_id: str, *, partitions: Iterable[str], oracle_ids: Iterable[str]):
    """Bind a verifier test method to durable semantic case metadata."""
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


def _stable(value: Any) -> dict[str, str]:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=repr)
    except Exception:
        text = repr(value)
    if len(text) > 512:
        text = text[:509] + "..."
    return {
        "type": type(value).__name__,
        "repr": text,
        "sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(),
    }


class VerifierTestCase(unittest.TestCase):
    """Verifier testcase with structured, machine-visible oracle observations.

    R11 intentionally forbids the R10 escape hatch `assert_oracle(id, True)`.
    A durable oracle must record a concrete comparison, expected exception, or
    before/after observation that the base-owned runner can bind to the contract.
    """

    def setUp(self) -> None:  # pragma: no cover - subclasses may override
        super().setUp()
        self._harness_oracle_observations: list[dict[str, Any]] = []

    def _store(self) -> list[dict[str, Any]]:
        store = getattr(self, "_harness_oracle_observations", None)
        if store is None:
            store = []
            self._harness_oracle_observations = store
        return store

    @staticmethod
    def _oracle_id(oracle_id: str) -> str:
        if not isinstance(oracle_id, str) or not oracle_id:
            raise ValueError("ORACLE_ID_INVALID")
        return oracle_id

    def assert_oracle(self, oracle_id: str, condition: object, msg: str | None = None) -> None:
        raise AssertionError("UNSTRUCTURED_ORACLE_FORBIDDEN:R11:use assert_oracle_equal/assert_oracle_raises/assert_oracle_unchanged")

    def assert_oracle_equal(self, oracle_id: str, actual: object, expected: object, msg: str | None = None) -> None:
        oid = self._oracle_id(oracle_id)
        self.assertEqual(actual, expected, msg)
        self._store().append({
            "oracle_id": oid,
            "kind": "equal",
            "matched": True,
            "actual": _stable(actual),
            "expected": _stable(expected),
        })

    def assert_oracle_raises(self, oracle_id: str, expected_exception: type[BaseException], callable_obj: Callable[..., Any], *args: Any, **kwargs: Any) -> BaseException:
        oid = self._oracle_id(oracle_id)
        if not isinstance(expected_exception, type) or not issubclass(expected_exception, BaseException):
            raise ValueError("ORACLE_EXPECTED_EXCEPTION_INVALID")
        with self.assertRaises(expected_exception) as cm:
            callable_obj(*args, **kwargs)
        exc = cm.exception
        self._store().append({
            "oracle_id": oid,
            "kind": "raises",
            "matched": True,
            "expected_exception": expected_exception.__name__,
            "observed_exception": type(exc).__name__,
        })
        return exc

    def assert_oracle_unchanged(self, oracle_id: str, before: object, after: object, msg: str | None = None) -> None:
        oid = self._oracle_id(oracle_id)
        self.assertEqual(before, after, msg)
        b = _stable(before); a = _stable(after)
        self._store().append({
            "oracle_id": oid,
            "kind": "unchanged",
            "matched": True,
            "before": b,
            "after": a,
        })
