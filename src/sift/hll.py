"""HyperLogLog cardinality estimator. SPDX-License-Identifier: MIT OR Apache-2.0.

Estimates the number of distinct items in a stream using ``2**p`` one-byte
registers, i.e. 256 B at ``p=8`` or 16 KiB at ``p=14``, no matter how long the
stream is. The estimator is the original one: an item's hash contributes the
run length of its leading zeros, the register for its bucket keeps the maximum
seen, and ``alpha * m**2 / sum(2**-register)`` inverts that back into a count.

Where the estimate is small enough that many registers are still empty, linear
counting is strictly better, so it takes over there. There is no large-range
correction: it exists to patch a 32-bit hash, and this module hashes to 64 bits.

Reference: Flajolet, Fusy, Gandouet, Meunier (2007), and the follow-up
"HyperLogLog: the analysis of a near-optimal cardinality estimation algorithm".
"""

from __future__ import annotations

from math import log

from . import _hash, _serial

#: Register counts below 16 are too noisy to be useful and above 16 cost more
#: than the accuracy they buy for a pure-Python stream walker.
MIN_PRECISION = 4
MAX_PRECISION = 16

_ALPHA = {16: 0.673, 32: 0.697, 64: 0.709}


class HyperLogLog:
    """A fixed-memory sketch of "how many distinct items have I seen"."""

    __slots__ = ("precision", "_registers")

    def __init__(self, precision: int = 12, *, values: bytes | None = None) -> None:
        if not isinstance(precision, int) or isinstance(precision, bool):
            raise TypeError("precision must be an int")
        if not MIN_PRECISION <= precision <= MAX_PRECISION:
            raise ValueError(
                f"precision must be in [{MIN_PRECISION}, {MAX_PRECISION}], got {precision}"
            )
        self.precision = precision
        count = 1 << precision
        if values is None:
            self._registers = bytearray(count)
        else:
            if len(values) != count:
                raise ValueError(f"expected {count} registers, got {len(values)}")
            self._registers = bytearray(values)

    # --- construction -----------------------------------------------------

    @property
    def register_count(self) -> int:
        return 1 << self.precision

    @property
    def standard_error(self) -> float:
        """The textbook bound ``1.04 / sqrt(m)``, as a fraction."""
        return 1.04 / (self.register_count ** 0.5)

    # --- update -----------------------------------------------------------

    def add(self, item: object) -> None:
        k1, _ = _hash.halves(item)
        low_bits = 64 - self.precision
        index = k1 >> low_bits
        tail = k1 & ((1 << low_bits) - 1)
        rank = _hash.leading_zeros(tail, low_bits) + 1
        if rank > self._registers[index]:
            self._registers[index] = rank

    def add_all(self, items: object) -> None:
        for item in items:  # type: ignore[union-attr]
            self.add(item)

    # --- query ------------------------------------------------------------

    def count(self) -> int:
        """The current cardinality estimate, rounded to an integer."""
        registers = self._registers
        m = len(registers)
        alpha = _ALPHA.get(m) or 0.7213 / (1.0 + 1.079 / m)
        inv_sum = 0.0
        zeros = 0
        for register in registers:
            if register == 0:
                zeros += 1
            inv_sum += 2.0 ** -register
        estimate = alpha * m * m / inv_sum
        if estimate <= 2.5 * m and zeros:
            estimate = m * log(m / zeros)
        return int(round(estimate))

    def empty_registers(self) -> int:
        return self._registers.count(0)

    def registers(self) -> bytes:
        """A copy of the register file, for tests and debugging."""
        return bytes(self._registers)

    def merge(self, other: "HyperLogLog") -> "HyperLogLog":
        """Union of two sketches built with the same precision.

        Merging is register-wise ``max``, which is exactly the union of the two
        streams they saw -- the property that makes HLL usable across shards.
        """
        if not isinstance(other, HyperLogLog):
            raise TypeError("can only merge another HyperLogLog")
        if other.precision != self.precision:
            raise ValueError(
                f"cannot merge precision {self.precision} with {other.precision}"
            )
        return HyperLogLog(
            self.precision,
            values=bytes(a if a >= b else b for a, b in zip(self._registers, other._registers)),
        )

    # --- representation ---------------------------------------------------

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, HyperLogLog)
            and self.precision == other.precision
            and self._registers == other._registers
        )

    def __repr__(self) -> str:
        return (
            f"HyperLogLog(precision={self.precision}, estimate={self.count()}, "
            f"stderr={self.standard_error:.3%})"
        )

    def memory_usage(self) -> int:
        """Bytes of register storage; the Python object itself is a few more."""
        return len(self._registers)

    # --- serialisation ----------------------------------------------------

    def to_bytes(self) -> bytes:
        return _serial.encode(_serial._KIND_HLL, bytes((self.precision,)), bytes(self._registers))

    @classmethod
    def from_bytes(cls, buf: bytes | bytearray | memoryview) -> "HyperLogLog":
        config, payload = _serial.decode(buf, _serial._KIND_HLL)
        if len(config) != 1:
            raise _serial.FrameError("malformed HyperLogLog config")
        precision = config[0]
        if not MIN_PRECISION <= precision <= MAX_PRECISION:
            # A frame whose own config byte is nonsense is a broken frame, not a
            # caller who picked a bad precision, so it reports as one.
            raise _serial.FrameError(
                f"HyperLogLog precision {precision} out of range "
                f"[{MIN_PRECISION}, {MAX_PRECISION}]"
            )
        return cls(precision, values=payload)
