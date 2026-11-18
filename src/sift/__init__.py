"""sift — fixed-memory statistics over streams you cannot hold in RAM.

SPDX-License-Identifier: MIT OR Apache-2.0

Five structures, no dependencies beyond the standard library:

===========================  ==================================  ===============
:class:`sift.HyperLogLog`    distinct-item count                 16 B .. 64 KiB
:class:`sift.CountMinSketch` per-item frequency, never low       width x depth
:class:`sift.TDigest`        quantiles and CDF                   ~compression
:class:`sift.Reservoir`      a fair k-subset                     k items
:class:`sift.RunningStats`   exact mean/variance/min/max         5 scalars
===========================  ==================================  ===============

The HLL range is ``2**precision`` registers for ``precision`` in
``MIN_PRECISION .. MAX_PRECISION`` (4 to 16); ``memory_usage()`` is the register
array and nothing else.

Everything that can be serialised does so through one frame format
(``sift._serial``) with a CRC32 trailer, and everything that hashes uses
``blake2b`` with a fixed key rather than :func:`hash`, so a sketch built in one
process means the same thing in the next one.
"""

from __future__ import annotations

from .cm import CountMinSketch
from .hll import MAX_PRECISION, MIN_PRECISION, HyperLogLog
from .moments import Ewma, RunningStats
from .quantile import TDigest
from .reservoir import Reservoir, WeightedReservoir, reservoir_sample
from ._serial import FrameError

__version__ = "0.1.0"


def version() -> tuple[int, int, int]:
    """The version as a tuple, for code that would rather not parse a string."""
    return tuple(int(part) for part in __version__.split("."))  # type: ignore[return-value]


__all__ = [
    "CountMinSketch",
    "Ewma",
    "FrameError",
    "HyperLogLog",
    "MAX_PRECISION",
    "MIN_PRECISION",
    "Reservoir",
    "RunningStats",
    "TDigest",
    "WeightedReservoir",
    "__version__",
    "reservoir_sample",
    "version",
]
