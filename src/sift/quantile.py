"""Streaming quantiles with a t-digest. SPDX-License-Identifier: MIT OR Apache-2.0.

Keeps the distribution as ``O(compression)`` weighted centroids and spends that
budget where the rank error matters most: the tails. That is what makes a
t-digest the right shape for "what is my p99 latency" over a stream you cannot
sort, and it is why the middle of the distribution is deliberately coarser.

The implementation is the merging variant: points accumulate in a buffer and are
folded into the centroid list when the buffer is long enough, at which point
neighbouring centroids may be combined as long as the combined centroid stays
within the size limit its quantile position allows.

Reference: Dunning and Tibshirani (2021), "Sliding-window histograms and
quantile estimation on approximate data" (the t-digest scaling-function paper).
"""

from __future__ import annotations

import struct
from bisect import bisect_left
from math import isfinite

from . import _serial

#: Centroid size limit at quantile position ``q`` for a digest holding ``n``
#: mass. The ``4*q*(1-q)`` shape is the derivative of the asin scaling function:
#: widest at the median, narrowest at the tails.
def _limit(q: float, n: float, compression: float) -> float:
    return 4.0 * n * q * (1.0 - q) / compression


class TDigest:
    """Approximate quantiles and CDF for a stream of numbers."""

    __slots__ = ("compression", "_means", "_weights", "_buffer", "_total")

    def __init__(
        self,
        compression: float = 100.0,
        *,
        means: tuple[float, ...] = (),
        weights: tuple[int, ...] = (),
        buffered: tuple[tuple[float, int], ...] = (),
        total: int | None = None,
    ) -> None:
        if not isinstance(compression, (int, float)) or isinstance(compression, bool):
            raise TypeError("compression must be a number")
        if not isfinite(compression) or compression < 1.0:
            raise ValueError("compression must be a finite number >= 1")
        if len(means) != len(weights):
            raise ValueError("means and weights must be the same length")
        self.compression = float(compression)
        self._means = list(means)
        self._weights = list(weights)
        self._buffer = list(buffered)
        self._total = int(total) if total is not None else sum(weights)

    # --- update -----------------------------------------------------------

    @property
    def _flush_at(self) -> int:
        return max(64, int(2 * self.compression))

    def add(self, value: float, weight: int = 1) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("TDigest.add needs a real number")
        if not isfinite(value):
            raise ValueError("NaN and infinities have no quantile")
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 1:
            raise ValueError("weight must be a positive int")
        self._buffer.append((float(value), weight))
        self._total += weight
        if len(self._buffer) >= self._flush_at:
            self.compress()

    def add_all(self, values: object) -> None:
        for value in values:  # type: ignore[union-attr]
            self.add(value)

    def compress(self) -> None:
        """Fold the buffer into the centroid list, merging within size limits."""
        merged = list(zip(self._means, self._weights)) + self._buffer
        self._buffer.clear()
        if not merged:
            return
        total = self._total
        if total <= 0:
            self._means, self._weights = [], []
            return
        merged.sort(key=lambda pair: pair[0])
        means: list[float] = []
        weights: list[int] = []
        mean_sum = 0.0  # mass consumed before the centroid being built
        current_mean = 0.0
        current_weight = 0

        def flush() -> None:
            nonlocal current_mean, current_weight
            if current_weight:
                means.append(current_mean / current_weight)
                weights.append(current_weight)

        for value, weight in merged:
            if current_weight == 0:
                current_mean = value * weight
                current_weight = weight
                continue
            centre = (mean_sum + current_weight / 2.0 + weight / 2.0) / total
            if current_weight + weight <= max(_limit(centre, total, self.compression), 1.0):
                current_mean += value * weight
                current_weight += weight
            else:
                flush()
                mean_sum += current_weight
                current_mean = value * weight
                current_weight = weight
        flush()
        self._means = means
        self._weights = weights

    def merge(self, other: "TDigest") -> "TDigest":
        if not isinstance(other, TDigest):
            raise TypeError("can only merge another TDigest")
        if abs(other.compression - self.compression) > 1e-12:
            raise ValueError(
                f"cannot merge compression {self.compression} with {other.compression}"
            )
        left = self.copy()
        left._buffer += other._snapshot_buffer()
        left._buffer += [
            (m, w) for m, w in zip(other._means, other._weights)
        ]
        left._total = self._total + other._total
        left.compress()
        return left

    def copy(self) -> "TDigest":
        return TDigest(
            self.compression,
            means=tuple(self._means),
            weights=tuple(self._weights),
            buffered=tuple(self._buffer),
            total=self._total,
        )

    # --- query ------------------------------------------------------------

    def __len__(self) -> int:
        return self._total

    @property
    def centroid_count(self) -> int:
        return len(self._means)

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def quantile(self, q: float) -> float:
        """The value below which ``q`` of the observed mass falls."""
        if not 0.0 <= q <= 1.0:
            raise ValueError("q must be in [0, 1]")
        if self._buffer:
            self.compress()
        if not self._means:
            raise ValueError("quantile of an empty TDigest")
        centres, means = self._centres()
        target = q * self._total
        if target <= centres[0]:
            return means[0]
        if target >= centres[-1]:
            return means[-1]
        at = bisect_left(centres, target)
        right = means[at]
        left = means[at - 1]
        span = centres[at] - centres[at - 1]
        if span <= 0.0:
            return right
        return left + (right - left) * (target - centres[at - 1]) / span

    def cdf(self, value: float) -> float:
        """Fraction of the observed mass at or below ``value``, in [0, 1].

        At a centroid mean the answer is that centroid's centre rank, which is
        the largest ``q`` whose ``quantile(q)`` still resolves to it, so the two
        are inverses at the joints as well as in between.
        """
        if self._buffer:
            self.compress()
        if not self._means:
            raise ValueError("cdf of an empty TDigest")
        centres, means = self._centres()
        if value <= means[0]:
            return 0.0 if value < means[0] else centres[0] / self._total
        if value >= means[-1]:
            return 1.0 if value > means[-1] else centres[-1] / self._total
        at = bisect_left(means, value)
        if at == 0:
            return 0.0
        lower, upper = means[at - 1], means[at]
        if upper == lower:
            return centres[at] / self._total
        weight_at = centres[at - 1] + (centres[at] - centres[at - 1]) * (value - lower) / (
            upper - lower
        )
        return weight_at / self._total

    def _centres(self) -> tuple[list[float], list[float]]:
        """Mass position of each centroid's middle, and the centroid means."""
        centres: list[float] = []
        walked = 0.0
        for weight in self._weights:
            centres.append(walked + weight / 2.0)
            walked += weight
        return centres, self._means

    # --- representation ---------------------------------------------------

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, TDigest)
            and self.compression == other.compression
            and self._total == other._total
            and self._means == other._means
            and self._weights == other._weights
            and self._buffer == other._buffer
        )

    def __repr__(self) -> str:
        return (
            f"TDigest(compression={self.compression:g}, n={self._total}, "
            f"centroids={len(self._means)})"
        )

    def memory_usage(self) -> int:
        """Rough live bytes for the centroids plus the pending buffer."""
        return 8 * len(self._means) + 8 * len(self._weights) + 16 * len(self._buffer)

    def _snapshot_buffer(self) -> list[tuple[float, int]]:
        return list(self._buffer)

    # --- serialisation ----------------------------------------------------

    def to_bytes(self) -> bytes:
        config = struct.pack(">d", self.compression)
        body = struct.pack(">QI", self._total, len(self._means))
        for mean, weight in zip(self._means, self._weights):
            body += struct.pack(">dQ", mean, weight)
        body += struct.pack(">I", len(self._buffer))
        for value, weight in self._buffer:
            body += struct.pack(">dQ", value, weight)
        return _serial.encode(_serial._KIND_TDIGEST, config, body)

    @classmethod
    def from_bytes(cls, buf: bytes | bytearray | memoryview) -> "TDigest":
        config, payload = _serial.decode(buf, _serial._KIND_TDIGEST)
        if len(config) != 8:
            raise _serial.FrameError("malformed TDigest config")
        (compression,) = struct.unpack(">d", config)
        offset = 0

        def take(fmt: str) -> tuple:
            nonlocal offset
            size = struct.calcsize(fmt)
            if offset + size > len(payload):
                raise _serial.FrameError("TDigest payload is truncated")
            values = struct.unpack_from(fmt, payload, offset)
            offset += size
            return values

        total, centroid_count = take(">QI")
        means: list[float] = []
        weights: list[int] = []
        for _ in range(centroid_count):
            mean, weight = take(">dQ")
            means.append(mean)
            weights.append(weight)
        (buffered_count,) = take(">I")
        buffered: list[tuple[float, int]] = []
        for _ in range(buffered_count):
            value, weight = take(">dQ")
            buffered.append((value, weight))
        if offset != len(payload):
            raise _serial.FrameError(f"{len(payload) - offset} trailing bytes in TDigest payload")
        return cls(
            compression,
            means=tuple(means),
            weights=tuple(weights),
            buffered=tuple(buffered),
            total=total,
        )
