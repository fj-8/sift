"""Count-Min frequency sketch. SPDX-License-Identifier: MIT OR Apache-2.0.

``depth`` rows of ``width`` counters. An item increments one cell per row, at a
position derived from its hash; the estimate for an item is the minimum over its
rows. Because every collision can only push a counter up, the estimate never
understates the truth -- that asymmetry is the whole reason to use this instead
of a dict you are willing to be wrong about.

With ``width = ceil(e/eps)`` and ``depth = ceil(ln(1/delta))`` the error is at
most ``eps * N`` (``N`` = total mass added) with probability ``1 - delta``.

Reference: Cormode and Muthukrishnan (2005), "An Improved Data Stream Summary:
The Count-Min Sketch and its Applications".
"""

from __future__ import annotations

import sys
from array import array
from math import ceil, e, log

from . import _hash, _serial


class CountMinSketch:
    """Fixed-memory frequency counts with a one-sided error guarantee."""

    __slots__ = ("depth", "width", "_counts", "mass")

    def __init__(self, width: int, depth: int) -> None:
        if not isinstance(width, int) or not isinstance(depth, int):
            raise TypeError("width and depth must be ints")
        if isinstance(width, bool) or isinstance(depth, bool):
            raise TypeError("width and depth must be ints")
        if width < 1 or depth < 1:
            raise ValueError("width and depth must both be at least 1")
        self.width = width
        self.depth = depth
        self._counts = array("q", bytes(8 * width * depth))
        self.mass = 0

    # --- construction -----------------------------------------------------

    @classmethod
    def with_accuracy(cls, eps: float, delta: float) -> "CountMinSketch":
        """The sketch sized for ``eps`` relative-to-N error at ``1 - delta``."""
        if not 0.0 < eps < 1.0:
            raise ValueError("eps must be in (0, 1)")
        if not 0.0 < delta < 1.0:
            raise ValueError("delta must be in (0, 1)")
        return cls(width=ceil(e / eps), depth=ceil(log(1.0 / delta)))

    # --- update -----------------------------------------------------------

    def add(self, item: object, count: int = 1) -> None:
        if not isinstance(count, int) or isinstance(count, bool):
            raise TypeError("count must be an int")
        if count < 0:
            raise ValueError("CountMinSketch models frequencies; use a dict for negatives")
        k1, k2 = _hash.halves(item)
        for row_index in range(self.depth):
            position = _hash.derive(k1, k2, row_index, self.width)
            self._counts[row_index * self.width + position] += count
        self.mass += count

    def add_all(self, items: object, count: int = 1) -> None:
        """Every item in ``items``, each weighted ``count``."""
        for item in items:  # type: ignore[union-attr]
            self.add(item, count)

    def merge(self, other: "CountMinSketch") -> "CountMinSketch":
        """A sketch over the concatenation of both streams."""
        if not isinstance(other, CountMinSketch):
            raise TypeError("can only merge another CountMinSketch")
        if (other.width, other.depth) != (self.width, self.depth):
            raise ValueError(
                f"cannot merge {self.width}x{self.depth} with "
                f"{other.width}x{other.depth}"
            )
        out = CountMinSketch(self.width, self.depth)
        out._counts = array("q", (a + b for a, b in zip(self._counts, other._counts)))
        out.mass = self.mass + other.mass
        return out

    # --- query ------------------------------------------------------------

    def query(self, item: object) -> int:
        """Minimum of the item's cells: an upper bound on its true count."""
        k1, k2 = _hash.halves(item)
        best = None
        for row_index in range(self.depth):
            position = _hash.derive(k1, k2, row_index, self.width)
            value = self._counts[row_index * self.width + position]
            if best is None or value < best:
                best = value
        return best or 0

    def __contains__(self, item: object) -> bool:
        return self.query(item) > 0

    # --- representation ---------------------------------------------------

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, CountMinSketch)
            and self.width == other.width
            and self.depth == other.depth
            and self.mass == other.mass
            and self._counts == other._counts
        )

    def __repr__(self) -> str:
        return (
            f"CountMinSketch(width={self.width}, depth={self.depth}, "
            f"mass={self.mass}, bytes={self.memory_usage()})"
        )

    def memory_usage(self) -> int:
        """Bytes of counter storage."""
        return self._counts.buffer_info()[1] * self._counts.itemsize

    def cells(self) -> array:
        """A copy of the counter grid, row-major. For tests and debugging."""
        return array("q", self._counts)

    # --- serialisation ----------------------------------------------------

    def to_bytes(self) -> bytes:
        payload = array("q", self._counts)
        if sys.byteorder != "big":
            payload.byteswap()
        config = (
            self.depth.to_bytes(4, "big")
            + self.width.to_bytes(4, "big")
            + self.mass.to_bytes(8, "big")
        )
        return _serial.encode(_serial._KIND_CM, config, payload.tobytes())

    @classmethod
    def from_bytes(cls, buf: bytes | bytearray | memoryview) -> "CountMinSketch":
        config, payload = _serial.decode(buf, _serial._KIND_CM)
        if len(config) != 16:
            raise _serial.FrameError("malformed CountMinSketch config")
        depth = int.from_bytes(config[:4], "big")
        width = int.from_bytes(config[4:8], "big")
        mass = int.from_bytes(config[8:16], "big")
        if len(payload) != 8 * width * depth:
            raise _serial.FrameError(
                f"expected {8 * width * depth} counter bytes, got {len(payload)}"
            )
        counts = array("q")
        counts.frombytes(payload)
        if sys.byteorder != "big":
            counts.byteswap()
        out = cls(width, depth)
        out._counts = counts
        out.mass = mass
        return out
