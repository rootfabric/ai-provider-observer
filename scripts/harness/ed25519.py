"""Dependency-free Ed25519 helper for Harness R6 external attestations.

Uses extended Edwards coordinates so verification is fast enough for normal
control loops while keeping the trust verifier portable and self-contained.
Private signing keys must remain outside the repository.
"""
from __future__ import annotations

import hashlib

Q = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, Q - 2, Q)) % Q
I = pow(2, (Q - 1) // 4, Q)


def _inv(x: int) -> int:
    return pow(x, Q - 2, Q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(D * y * y + 1) % Q
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q != 0:
        x = x * I % Q
    if x & 1:
        x = Q - x
    return x


BY = 4 * _inv(5) % Q
BX = _xrecover(BY)

# Extended coordinates (X:Y:Z:T), x=X/Z, y=Y/Z, T=XY/Z.
IDENTITY = (0, 1, 1, 0)
BASE = (BX, BY, 1, BX * BY % Q)


def _add(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % Q
    b = (y1 + x1) * (y2 + x2) % Q
    c = 2 * D * t1 * t2 % Q
    d = 2 * z1 * z2 % Q
    e = (b - a) % Q
    f = (d - c) % Q
    g = (d + c) % Q
    h = (b + a) % Q
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _double(p: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, z, _ = p
    a = x * x % Q
    b = y * y % Q
    c = 2 * z * z % Q
    d = (-a) % Q
    e = ((x + y) * (x + y) - a - b) % Q
    g = (d + b) % Q
    f = (g - c) % Q
    h = (d - b) % Q
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _scalarmult(p: tuple[int, int, int, int], scalar: int) -> tuple[int, int, int, int]:
    q = IDENTITY
    n = p
    e = scalar
    while e:
        if e & 1:
            q = _add(q, n)
        n = _double(n)
        e >>= 1
    return q


def _to_affine(p: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, z, _ = p
    iz = _inv(z)
    return x * iz % Q, y * iz % Q


def _encodepoint(p: tuple[int, int, int, int]) -> bytes:
    x, y = _to_affine(p)
    out = bytearray(int(y).to_bytes(32, "little"))
    out[31] |= (x & 1) << 7
    return bytes(out)


def _decodepoint(s: bytes) -> tuple[int, int, int, int]:
    if len(s) != 32:
        raise ValueError("ED25519_POINT_LENGTH")
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    if y >= Q:
        raise ValueError("ED25519_POINT_NONCANONICAL")
    x = _xrecover(y)
    if (x & 1) != (s[31] >> 7):
        x = Q - x
    # Curve equation: -x^2 + y^2 = 1 + d*x^2*y^2.
    if (-x * x + y * y - 1 - D * x * x * y * y) % Q != 0:
        raise ValueError("ED25519_POINT_INVALID")
    return (x, y, 1, x * y % Q)


def _hint(m: bytes) -> int:
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _secret_scalar_and_prefix(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("ED25519_SEED_LENGTH")
    h = bytearray(hashlib.sha512(seed).digest())
    h[0] &= 248
    h[31] &= 63
    h[31] |= 64
    return int.from_bytes(h[:32], "little"), bytes(h[32:])


def public_key_from_seed(seed: bytes) -> bytes:
    a, _ = _secret_scalar_and_prefix(seed)
    return _encodepoint(_scalarmult(BASE, a))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _secret_scalar_and_prefix(seed)
    pk = _encodepoint(_scalarmult(BASE, a))
    r = _hint(prefix + message) % L
    r_enc = _encodepoint(_scalarmult(BASE, r))
    k = _hint(r_enc + pk + message) % L
    s = (r + k * a) % L
    return r_enc + s.to_bytes(32, "little")


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        r_enc = signature[:32]
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False
        a = _decodepoint(public_key)
        r = _decodepoint(r_enc)
        k = _hint(r_enc + public_key + message) % L
        left = _scalarmult(BASE, s)
        right = _add(r, _scalarmult(a, k))
        return _encodepoint(left) == _encodepoint(right)
    except (ValueError, OverflowError):
        return False
