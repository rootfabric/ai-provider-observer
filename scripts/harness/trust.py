from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ed25519 import verify as ed25519_verify
from gitproof import bytes_at_commit, file_at_commit
from strictjson import StrictJSONError, loads


@dataclass(frozen=True)
class TrustError:
    code: str
    message: str


def canonical_attestation_payload(att: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in att.items() if k != "signature_b64"}
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def evidence_digest_at_commit(root: Path, commit: str, paths: list[str]) -> str | None:
    rows: list[dict[str, str]] = []
    for rel in sorted(set(paths)):
        data = bytes_at_commit(root, commit, rel)
        if data is None:
            return None
        rows.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})
    payload = (json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_trust_at_base(root: Path, base_sha: str) -> tuple[dict[str, Any] | None, list[TrustError]]:
    rel = "config/control/harness/trust-providers.v1.json"
    text = file_at_commit(root, base_sha, rel)
    if text is None:
        return None, [TrustError("TRUST_ROOT_NOT_IN_BASE", f"{rel} missing at {base_sha}")]
    try:
        value = loads(text, label=f"{base_sha}:{rel}")
    except StrictJSONError as exc:
        return None, [TrustError("TRUST_ROOT_BASE_JSON_INVALID", str(exc))]
    if not isinstance(value, dict):
        return None, [TrustError("TRUST_ROOT_BASE_INVALID", "object required")]
    return value, []


def verify_attestation(
    root: Path,
    attestation: dict[str, Any],
    *,
    base_sha: str,
    purpose: str,
    subject_head: str,
    mission_id: str,
    work_order_id: str,
) -> list[TrustError]:
    errors: list[TrustError] = []
    required = {
        "schema", "attestation_id", "provider_id", "key_id", "principal",
        "purpose", "subject_head", "mission_id", "work_order_id", "decision",
        "issued_at_utc", "prerequisite_event", "prerequisite_event_sha256",
        "evidence_paths", "evidence_digest", "signature_b64"
    }
    missing = sorted(required - set(attestation))
    if missing:
        return [TrustError("ATTESTATION_FIELD_MISSING", ",".join(missing))]
    if attestation.get("schema") != "hybrid_harness.external_attestation.v2":
        errors.append(TrustError("ATTESTATION_SCHEMA_INVALID", str(attestation.get("schema"))))
    if attestation.get("purpose") != purpose:
        errors.append(TrustError("ATTESTATION_PURPOSE_MISMATCH", f"expected={purpose} got={attestation.get('purpose')}"))
    if attestation.get("subject_head") != subject_head:
        errors.append(TrustError("ATTESTATION_SUBJECT_MISMATCH", f"expected={subject_head} got={attestation.get('subject_head')}"))
    if attestation.get("mission_id") != mission_id or attestation.get("work_order_id") != work_order_id:
        errors.append(TrustError("ATTESTATION_SCOPE_MISMATCH", "mission/work-order mismatch"))
    expected_decision = "PASS" if purpose == "REVIEW_PASS" else "APPROVE"
    if attestation.get("decision") != expected_decision:
        errors.append(TrustError("ATTESTATION_DECISION_INVALID", str(attestation.get("decision"))))
    paths = attestation.get("evidence_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(x, str) and x for x in paths):
        errors.append(TrustError("ATTESTATION_EVIDENCE_PATHS_INVALID", repr(paths)))
    if not isinstance(attestation.get("prerequisite_event"), str) or not str(attestation.get("prerequisite_event")).startswith("evidence/events/"):
        errors.append(TrustError("ATTESTATION_PREREQUISITE_EVENT_INVALID", repr(attestation.get("prerequisite_event"))))

    trust, trust_errors = _load_trust_at_base(root, base_sha)
    errors.extend(trust_errors)
    if trust is None:
        return errors
    providers = trust.get("providers") if isinstance(trust.get("providers"), dict) else {}
    provider = providers.get(attestation.get("provider_id")) if isinstance(providers, dict) else None
    if not isinstance(provider, dict) or provider.get("enabled") is not True:
        errors.append(TrustError("ATTESTATION_PROVIDER_NOT_TRUSTED_AT_BASE", str(attestation.get("provider_id"))))
        return errors
    if provider.get("type") != "ED25519_V1":
        errors.append(TrustError("ATTESTATION_PROVIDER_TYPE_UNSUPPORTED", str(provider.get("type"))))
        return errors
    key = None
    for item in provider.get("keys", []):
        if isinstance(item, dict) and item.get("key_id") == attestation.get("key_id"):
            key = item
            break
    if not isinstance(key, dict):
        errors.append(TrustError("ATTESTATION_KEY_NOT_TRUSTED_AT_BASE", str(attestation.get("key_id"))))
        return errors
    if key.get("principal") != attestation.get("principal"):
        errors.append(TrustError("ATTESTATION_PRINCIPAL_MISMATCH", f"key={key.get('principal')} attestation={attestation.get('principal')}"))
    purposes = key.get("allowed_purposes") if isinstance(key.get("allowed_purposes"), list) else []
    if purpose not in purposes:
        errors.append(TrustError("ATTESTATION_PURPOSE_NOT_ALLOWED_FOR_KEY", purpose))
    try:
        public_key = base64.b64decode(str(key.get("public_key_b64")), validate=True)
        signature = base64.b64decode(str(attestation.get("signature_b64")), validate=True)
    except Exception:
        errors.append(TrustError("ATTESTATION_BASE64_INVALID", "public key or signature"))
        return errors
    if not ed25519_verify(public_key, canonical_attestation_payload(attestation), signature):
        errors.append(TrustError("ATTESTATION_SIGNATURE_INVALID", str(attestation.get("attestation_id"))))
    return errors
