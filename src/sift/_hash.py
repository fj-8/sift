"""Deterministic hashing for the sketches. SPDX-License-Identifier: MIT OR Apache-2.0.

Python's built-in ``hash()`` is salted per process, so two runs over the same
stream would build two incomparable sketches and a sketch written to disk today
would be meaningless tomorrow. Everything here goes through ``blake2b`` with a
fixed key instead.

Values are also type-prefixed: without that, the integer ``5`` and the string
``"5"`` would be the same item to a sketch, which is rarely what a caller means
when they mix ``int`` and ``str`` keys in one stream.
"""

from __future__ import annotations

import hashlib

KEY = b"sift.v1"

#: 16 bytes is two independent 64-bit halves, which is what the sketches use.
DIGEST_SIZE = 16

_MASK64 = (1 << 64) - 1


def encode(item: object) -> bytes:
    """Map a hashable-ish value to bytes, tagged by type.

    Accepts ``int``, ``bool``, ``str``, ``bytes``-like and ``None``. Anything else
    raises :class:`TypeError` rather than hashing a repr that two runs might
    disagree about.
    """
    if item is None:
        return b"N"
    if isinstance(item, bool):
        return b"b\x01" if item else b"b\x00"
    if isinstance(item, int):
        # Minimal two's-complement length; the width is unambiguous because the
        # decoder is not involved -- equal integers always encode identically.
        length = max(1, (item.bit_length() + 8) // 8)
        return b"i" + item.to_bytes(length, "little", signed=True)
    if isinstance(item, (bytes, bytearray, memoryview)):
        return b"s" + bytes(item)
    if isinstance(item, str):
        return b"u" + item.encode("utf-8")
    raise TypeError(
        f"sift hashes int, bool, str, bytes and None; got {type(item).__name__}"
    )


def digest(item: object) -> bytes:
    return hashlib.blake2b(encode(item), digest_size=DIGEST_SIZE, key=KEY).digest()


def halves(item: object) -> tuple[int, int]:
    """The two 64-bit halves of an item's digest, big-endian.

    Sketches that need more than one index derive them arithmetically from these
    two values (see :func:`derive`) instead of re-hashing per row: that is the
    standard cheap construction, it is measurably fine for the error bounds
    reported in ``bench``, and it turns ``d`` hashes into one.
    """
    raw = digest(item)
    return int.from_bytes(raw[:8], "big"), int.from_bytes(raw[8:], "big")


def derive(k1: int, k2: int, row: int, modulus: int) -> int:
    """Position of ``row`` given the two hash halves, in ``[0, modulus)``."""
    return (k1 + row * k2 + row * row) % modulus


def leading_zeros(value: int, bits: int) -> int:
    return bits - value.bit_length()


def mask64(value: int) -> int:
    return value & _MASK64
