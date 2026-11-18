"""Fixed-size samples from a stream of unknown length. SPDX-License-Identifier: MIT OR Apache-2.0.

Two flavours, because "give me k of these" has two meanings:

* :class:`Reservoir` is Vitter's Algorithm R -- every item ends up in the sample
  with probability exactly ``k/n``, so it answers "a fair k-subset of the log".
* :class:`WeightedReservoir` is Efraimidis and Spirakis's A-Res -- items carry a
  weight and the k best-connected winners are kept, so it answers "k rows in
  proportion to how much each row matters".

Both hold ``k`` items and nothing else, which is the point: the stream can be a
terabyte and the sample is a list.

References: Vitter (1985), "Random sampling with a reservoir"; Efraimidis and
Spirakis (2006), "Weighted random sampling with a reservoir".
"""

from __future__ import annotations

import heapq
import random
from math import log

_DEFAULT_RNG = random.Random()


class Reservoir:
    """A uniformly random subset of ``k`` items from a stream."""

    __slots__ = ("k", "_pool", "_seen", "_rng")

    def __init__(self, k: int, *, rng: random.Random | None = None) -> None:
        if isinstance(k, bool) or not isinstance(k, int):
            raise TypeError("k must be an int")
        if k < 1:
            raise ValueError("a reservoir of size 0 holds nothing; use a counter")
        self.k = k
        self._pool: list[object] = []
        self._seen = 0
        self._rng = rng if rng is not None else _DEFAULT_RNG

    def add(self, item: object) -> None:
        self._seen += 1
        if len(self._pool) < self.k:
            self._pool.append(item)
            return
        # The incoming item is the `self._seen`-th; it takes a random slot and
        # keeps it with probability k/seen, which is exactly the update the
        # induction on uniformity needs.
        slot = self._rng.randrange(self._seen)
        if slot < self.k:
            self._pool[slot] = item

    def add_all(self, items: object) -> None:
        for item in items:  # type: ignore[union-attr]
            self.add(item)

    def sample(self) -> list:
        """The current k items, in arbitrary order."""
        return list(self._pool)

    @property
    def seen(self) -> int:
        return self._seen

    def __len__(self) -> int:
        return len(self._pool)

    def __repr__(self) -> str:
        return f"Reservoir(k={self.k}, seen={self._seen}, held={len(self._pool)})"


class WeightedReservoir:
    """A weighted random subset of ``k`` items (sampling with distinct keys)."""

    __slots__ = ("k", "_heap", "_seen", "_rng", "_tie")

    def __init__(self, k: int, *, rng: random.Random | None = None) -> None:
        if isinstance(k, bool) or not isinstance(k, int):
            raise TypeError("k must be an int")
        if k < 1:
            raise ValueError("k must be at least 1")
        self.k = k
        self._heap: list[tuple[float, int, object]] = []
        self._seen = 0
        self._tie = 0
        self._rng = rng if rng is not None else _DEFAULT_RNG

    def add(self, item: object, weight: float) -> None:
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise TypeError("weight must be a number")
        if not weight > 0:
            raise ValueError("weights must be positive; a zero-weight item is never selected")
        self._seen += 1
        self._tie += 1
        # A-Res: key = u ** (1/w). Maximising that key is the same as minimising
        # -log(u)/w, which avoids both an underflow in the power and a log(0).
        u = self._rng.random()
        while u <= 0.0:
            u = self._rng.random()
        priority = -log(u) / weight
        entry = (-priority, self._tie, item)
        if len(self._heap) < self.k:
            heapq.heappush(self._heap, entry)
        elif priority < -self._heap[0][0]:
            heapq.heapreplace(self._heap, entry)

    def sample(self) -> list:
        """The retained items, worst key first."""
        return [entry[2] for entry in sorted(self._heap)]

    @property
    def seen(self) -> int:
        return self._seen

    def __len__(self) -> int:
        return len(self._heap)

    def __repr__(self) -> str:
        return f"WeightedReservoir(k={self.k}, seen={self._seen}, held={len(self._heap)})"


def reservoir_sample(iterable: object, k: int, *, rng: random.Random | None = None) -> list:
    """One-shot :class:`Reservoir` over an iterable; never holds more than ``k``."""
    pool = Reservoir(k, rng=rng)
    for item in iterable:  # type: ignore[union-attr]
        pool.add(item)
    return pool.sample()
