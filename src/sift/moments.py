"""Exact one-pass moments and exponential smoothing. SPDX-License-Identifier: MIT OR Apache-2.0.

No sketch here: a running mean and variance can be carried in nine floats and
still be numerically sound, so the only honest reason to approximate them is
memory you do not have -- and you do not, for ``sum(x)`` and ``sum(x*x)`` in
float, which lose catastrophically to Welford's update on data with a large
offset. :class:`RunningStats` is the online form, plus Chan's parallel merge so
two partial runs combine without revisiting either.

References: Welford (1962); Chan, Golub and LeVeque (1983), "Algorithms for
computing the sample variance: analysis and recommendations".
"""

from __future__ import annotations


class RunningStats:
    """Count, mean, variance, min and max of a stream of numbers."""

    __slots__ = ("count", "_mean", "_m2", "_min", "_max")

    def __init__(
        self,
        *,
        count: int = 0,
        mean: float = 0.0,
        m2: float = 0.0,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> None:
        self.count = count
        self._mean = float(mean)
        self._m2 = float(m2)
        self._min = minimum
        self._max = maximum

    # --- update -----------------------------------------------------------

    def add(self, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("RunningStats.add needs a real number")
        if value != value:  # NaN
            raise ValueError("NaN would silently poison every moment")
        number = float(value)
        self.count += 1
        delta = number - self._mean
        self._mean += delta / self.count
        # The second factor uses the *updated* mean; that is the whole trick and
        # why this stays accurate where sum(x**2) does not.
        self._m2 += delta * (number - self._mean)
        if self._min is None or number < self._min:
            self._min = number
        if self._max is None or number > self._max:
            self._max = number

    def add_all(self, values: object) -> None:
        for value in values:  # type: ignore[union-attr]
            self.add(value)

    @classmethod
    def combine(cls, a: "RunningStats", b: "RunningStats") -> "RunningStats":
        """The statistics of the concatenation of two streams."""
        if not isinstance(a, RunningStats) or not isinstance(b, RunningStats):
            raise TypeError("combine takes two RunningStats")
        count = a.count + b.count
        if count == 0:
            return cls()
        delta = b._mean - a._mean
        mean = a._mean + delta * (b.count / count)
        m2 = a._m2 + b._m2 + delta * delta * (a.count * b.count / count)
        minimum = min(v for v in (a._min, b._min) if v is not None)
        maximum = max(v for v in (a._max, b._max) if v is not None)
        return cls(count=count, mean=mean, m2=m2, minimum=minimum, maximum=maximum)

    # --- query ------------------------------------------------------------

    @property
    def mean(self) -> float:
        if self.count == 0:
            raise ValueError("mean of an empty stream")
        return self._mean

    @property
    def variance(self) -> float:
        """Unbiased (``n-1``) sample variance. Requires two observations."""
        if self.count == 0:
            raise ValueError("variance of an empty stream")
        if self.count < 2:
            raise ValueError("sample variance needs at least two observations")
        return self._m2 / (self.count - 1)

    @property
    def population_variance(self) -> float:
        """The ``n``-divisor variance, defined for a single observation."""
        if self.count == 0:
            raise ValueError("variance of an empty stream")
        return self._m2 / self.count

    @property
    def stdev(self) -> float:
        return self.variance ** 0.5

    @property
    def pstdev(self) -> float:
        return self.population_variance ** 0.5

    @property
    def min(self) -> float:
        if self._min is None:
            raise ValueError("min of an empty stream")
        return self._min

    @property
    def max(self) -> float:
        if self._max is None:
            raise ValueError("max of an empty stream")
        return self._max

    def state(self) -> tuple:
        """The full state, enough to resume or to compare for equality."""
        return (self.count, self._mean, self._m2, self._min, self._max)

    @classmethod
    def from_state(cls, state: tuple) -> "RunningStats":
        count, mean, m2, minimum, maximum = state
        return cls(count=count, mean=mean, m2=m2, minimum=minimum, maximum=maximum)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, RunningStats) and self.state() == other.state()

    def __repr__(self) -> str:
        if self.count == 0:
            return "RunningStats(empty)"
        return (
            f"RunningStats(n={self.count}, mean={self._mean:.6g}, "
            f"stdev={self.stdev:.6g}, min={self._min:.6g}, max={self._max:.6g})"
        )


class Ewma:
    """Exponentially weighted moving average.

    Old observations are not forgotten, they are down-weighted, so the smoother
    answers "what is the level right now" in constant memory. ``alpha`` is the
    weight of the newest sample; the alternative constructor takes a span, the
    way a trading desk asks for it.
    """

    __slots__ = ("alpha", "_value", "_weight", "count", "_debias")

    def __init__(self, alpha: float, *, debias: bool = True) -> None:
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
            raise TypeError("alpha must be a number")
        if not 0.0 < float(alpha) <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self.alpha = float(alpha)
        self._value = 0.0
        self._weight = 0.0
        self.count = 0
        self._debias = debias

    @classmethod
    def with_span(cls, span: float, *, debias: bool = True) -> "Ewma":
        """``alpha = 2 / (span + 1)``, the spreadsheet-style parameterisation."""
        if span <= 0:
            raise ValueError("span must be positive")
        return cls(2.0 / (float(span) + 1.0), debias=debias)

    def add(self, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("Ewma.add needs a real number")
        self.count += 1
        self._value += self.alpha * (float(value) - self._value)
        if self._debias:
            # The weight totalled so far, on the same decay: without dividing it
            # out, the first samples of an all-1000 stream report "about 300".
            self._weight += self.alpha * (1.0 - self._weight)
        return self.value

    @property
    def value(self) -> float:
        """The smoothed level, corrected for the zero initialisation bias."""
        if self.count == 0:
            raise ValueError("Ewma has seen no values")
        if self._debias and 0.0 < self._weight < 1.0:
            return self._value / self._weight
        return self._value

    def __repr__(self) -> str:
        if self.count == 0:
            return f"Ewma(alpha={self.alpha:g}, empty)"
        return f"Ewma(alpha={self.alpha:g}, n={self.count}, value={self.value:.6g})"
