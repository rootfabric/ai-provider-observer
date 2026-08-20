#!/usr/bin/env python3
"""External trust-domain helper for Hybrid Harness R9.

This utility intentionally lives outside the normal control command surface.
Possession of a private seed is the trust boundary; do not place seeds in the
managed repository or give them to the implementer session.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/harness"))
from ed25519 import public_key_from_seed, sign
from trust import canonical_attestation_payload


def seed_bytes(path: Path) -> bytes:
    raw = path.read_text(encoding="utf-8").strip()
    value = bytes.fromhex(raw)
    if len(value) != 32:
        raise SystemExit("PRIVATE_SEED_MUST_BE_32_BYTES_HEX")
    return value


def keygen(private_out: Path) -> int:
    resolved = private_out.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
        raise SystemExit("REFUSING_PRIVATE_KEY_INSIDE_MANAGED_REPOSITORY")
    except ValueError:
        pass
    seed = secrets.token_bytes(32)
    private_out.parent.mkdir(parents=True, exist_ok=True)
    private_out.write_text(seed.hex() + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(private_out, 0o600)
    print("public_key_b64=" + base64.b64encode(public_key_from_seed(seed)).decode("ascii"))
    print("private_seed_written_outside_repo=" + str(private_out))
    return 0


def sign_file(private_seed: Path, unsigned_json: Path, output: Path) -> int:
    att = json.loads(unsigned_json.read_text(encoding="utf-8"))
    if not isinstance(att, dict):
        raise SystemExit("ATTESTATION_OBJECT_REQUIRED")
    att.pop("signature_b64", None)
    att["signature_b64"] = base64.b64encode(sign(seed_bytes(private_seed), canonical_attestation_payload(att))).decode("ascii")
    output.write_text(json.dumps(att, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


def main() -> int:
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="cmd",required=True)
    kg=sub.add_parser("keygen"); kg.add_argument("--private-out",required=True,type=Path)
    sg=sub.add_parser("sign"); sg.add_argument("--private-seed",required=True,type=Path); sg.add_argument("--unsigned",required=True,type=Path); sg.add_argument("--output",required=True,type=Path)
    a=ap.parse_args()
    return keygen(a.private_out) if a.cmd=="keygen" else sign_file(a.private_seed,a.unsigned,a.output)

if __name__ == "__main__": raise SystemExit(main())
