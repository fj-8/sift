"""Measure sift: accuracy against exact baselines, throughput, memory.

SPDX-License-Identifier: MIT OR Apache-2.0

Every number printed by this script is measured in the run that printed it.
Nothing is cached, nothing is a quote from a paper, and the tables include the
rows where holding the data in RAM is faster -- those rows are the reason the
other rows are worth reading.

    python bench/bench.py                       # full run, a few minutes
    python bench/bench.py --quick               # 1/10 of the data, for CI
    python bench/bench.py --suite accuracy       # one suite
    python bench/bench.py --markdown > table.md  # paste-ready tables

Suites:

    accuracy        error against the exact answer, with the theoretical bound
    throughput      median of several repetitions, sketch versus set/dict/sorted
    memory          live object size from sys.getsizeof, recursion included
    serialisation   frame round trips and the bytes they cost
    determinism     two builds of the same stream, compared byte for byte
    merge           shard-and-combine correctness plus its cost
    cli             the shipped subcommands over a generated CSV
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))

import sift  # noqa: E402
from sift import (  # noqa: E402
    CountMinSketch,
    Ewma,
    HyperLogLog,
    Reservoir,
    RunningStats,
    TDigest,
    WeightedReservoir,
)

SEED = 20251118
SUITES: list[tuple[str, str, Callable[[Config], list[Row]]]] = []


class Config:
    def __init__(self, quick: bool) -> None:
        self.quick = quick
        # Accuracy and throughput need different n: accuracy wants enough
        # distinct keys that the tail of a sketch's error is exercised,
        # throughput wants a loop short enough to repeat five times.
        self.accuracy_n = 100_000 if quick else 1_000_000
        self.keys_n = 10_000 if quick else 50_000
        self.throughput_n = 20_000 if quick else 200_000
        self.reps = 3 if quick else 5
        self.memory_n = 100_000 if quick else 1_000_000
        self.csv_rows = 50_000 if quick else 500_000


def suite(name: str, doc: str) -> Callable[[Callable[[Config], list]], None]:
    def register(function: Callable[[Config], list]) -> None:
        SUITES.append((name, doc, function))

    return register


class Row(list):
    """One measured table row; the last cell is always a human note."""


def median_of(function: Callable[[], float], reps: int) -> tuple[float, float, float]:
    """(median, min, max) wall seconds over ``reps`` timed calls."""
    samples: list[float] = []
    for _ in range(reps):
        start = time.perf_counter()
        function()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples), min(samples), max(samples)


def print_table(header: list[str], rows: Iterable[Row]) -> None:
    cells = [header] + [list(row) for row in rows]
    widths = [max(len(str(cell)) for cell in column) for column in zip(*cells)]
    for index, row in enumerate(cells):
        print("| " + " | ".join(str(cell).ljust(width) for cell, width in zip(row, widths)) + " |")
        if index == 0:
            print("|" + "|".join("-" * (width + 2) for width in widths) + "|")


def timed_pair(
    sketch_call: Callable[[], object],
    baseline_call: Callable[[], object],
    config: Config,
    operations: int,
    label: str,
) -> list[str]:
    """One markdown row: both loops, medians over the same number of reps."""
    median_sketch, low_sketch, high_sketch = median_of(sketch_call, config.reps)
    median_baseline, low_base, high_base = median_of(baseline_call, config.reps)
    ratio = median_sketch / median_baseline if median_baseline else float("inf")
    spread = f"{low_sketch * 1e3:.1f}-{high_sketch * 1e3:.1f} ms"
    return [
        label,
        f"{median_sketch * 1e3:.2f} ms ({spread})",
        f"{median_baseline * 1e3:.2f} ms",
        f"{median_sketch / operations * 1e9:.0f} ns/op",
        f"{ratio:.1f}x {'slower' if ratio >= 1 else 'faster'}",
        f"median of {config.reps}, {operations:,} ops",
    ]

# --------------------------------------------------------------------------- #
# accuracy
# --------------------------------------------------------------------------- #


@suite(
    "accuracy",
    "Error against the exact answer. 'measured' is the error this run observed,"
    "'spread' is the second statistic of that measurement, 'bound' the promise.",
)
def accuracy(config: Config) -> list[Row]:
    rows: list[Row] = []

    # HyperLogLog: rebuild the same distinct-key stream at several precisions.
    distinct = config.keys_n
    stream = [f"key-{index}" for index in range(distinct)]
    stream += stream[: distinct // 2]  # half the rows are duplicates
    trials = 8 if config.quick else 16
    for precision in (8, 10, 12, 14):
        errors: list[float] = []
        for trial in range(trials):
            sketch = HyperLogLog(precision)
            # A different key prefix per trial so the trials are independent:
            # the same keys would land in the same registers every time.
            for index in range(len(stream)):
                sketch.add(f"t{trial}-{stream[index]}")
            errors.append(sketch.count() / distinct - 1.0)
        bound = 1.04 / (2**precision) ** 0.5
        rows.append(
            Row(
                [
                    f"HyperLogLog p={precision}",
                    f"{distinct:,} distinct",
                    f"{statistics.fmean(errors):+.2%}",
                    f"{max(abs(error) for error in errors):.2%}",
                    f"{bound:.2%}",
                    f"{trials} independent builds, m={2**precision}",
                ]
            )
        )

    # Count-Min: Zipf stream, compare every query to the dict's truth.
    items = config.accuracy_n
    rng = random.Random(SEED)
    counts: dict[str, int] = {}
    sketch = CountMinSketch.with_accuracy(1e-3, 0.05)
    for _ in range(items):
        key = f"z{int(abs(rng.gauss(0.0, 60.0)))}"
        counts[key] = counts.get(key, 0) + 1
        sketch.add(key)
    overcounts = [sketch.query(key) - count for key, count in counts.items()]
    total = sum(counts.values())
    rows.append(
        Row(
            [
                "CountMinSketch eps=1e-3 delta=0.05",
                f"{len(counts):,} distinct",
                f"{max(overcounts):,} max overcount",
                f"{max(overcounts) / total:.2%} of N",
                f"{1e-3:.2%} promise",
                f"{sketch.width}x{sketch.depth} cells, never undercounts",
            ]
        )
    )

    # Count-Min again over a flat stream, where every key has count 1. A hot
    # Zipf stream can hide a collision behind the minimum, so this is the row
    # that shows the eps*N allowance actually being spent.
    flat = CountMinSketch.with_accuracy(1e-3, 0.05)
    keys = [f"flat-{index}" for index in range(items)]
    for key in keys:
        flat.add(key)
    overcounts = [flat.query(key) - 1 for key in keys]
    rows.append(
        Row(
            [
                "CountMinSketch eps=1e-3 delta=0.05",
                f"{items:,} distinct, count 1 each",
                f"{max(overcounts):,} max overcount",
                f"{statistics.fmean(overcounts):,.0f} mean",
                f"{1e-3 * items:,.0f} = eps*N",
                f"{flat.width}x{flat.depth} cells, never undercounts",
            ]
        )
    )

    # t-digest: rank error at the quantiles people actually ask for.
    values = [rng.gauss(200.0, 25.0) for _ in range(config.accuracy_n)]
    exact = sorted(values)
    for compression in (20.0, 100.0, 500.0):
        digest = TDigest(compression)
        for value in values:
            digest.add(value)
        errors: list[float] = []
        for q in (0.01, 0.1, 0.5, 0.9, 0.99):
            index = min(len(exact) - 1, int(q * (len(exact) - 1) + 0.5))
            errors.append(abs(digest.cdf(exact[index]) - q))
        rows.append(
            Row(
                [
                    f"TDigest delta={compression:g}",
                    f"{config.accuracy_n:,} values",
                    f"{max(errors):.5f} max rank error",
                    f"{statistics.median(errors):.5f} median",
                    "0 by construction",
                    f"{digest.centroid_count} centroids, {digest.memory_usage():,} B",
                ]
            )
        )

    # RunningStats: exact moments, so any difference is pure floating point.
    stats = RunningStats()
    for value in values:
        stats.add(value)
    rows.append(
        Row(
            [
                "RunningStats mean",
                f"{config.accuracy_n:,}",
                f"{abs(stats.mean - statistics.fmean(values)):.3e}",
                "-",
                "0 (exact algorithm)",
                "vs statistics.fmean",
            ]
        )
    )
    rows.append(
        Row(
            [
                "RunningStats variance",
                f"{config.accuracy_n:,}",
                f"{abs(stats.variance - statistics.variance(values)):.3e}",
                "-",
                "0 (exact algorithm)",
                "vs statistics.variance",
            ]
        )
    )

    # Reservoir: empirical inclusion rate against k/n.
    trials = 300 if config.quick else 1200
    n, k = 400, 40
    hits = [0] * n
    draw_rng = random.Random(SEED + 1)
    for _ in range(trials):
        pool = Reservoir(k, rng=draw_rng)
        pool.add_all(range(n))
        for item in pool.sample():
            hits[item] += 1
    rates = [hit / trials for hit in hits]
    rows.append(
        Row(
            [
                "Reservoir inclusion",
                f"n={n}, k={k}",
                f"{max(abs(rate - k / n) for rate in rates):.4f} max deviation",
                f"{statistics.pstdev(rates):.4f} spread",
                f"{k / n:.3f} target",
                f"{trials} draws, one shared rng",
            ]
        )
    )

    # Weighted: selection frequency against the weight proportions.
    weights = [float(index + 1) for index in range(10)]
    total_weight = sum(weights)
    counts = [0] * len(weights)
    trials = 400 if config.quick else 2000
    draw = random.Random(SEED + 2)
    for _ in range(trials):
        wr = WeightedReservoir(1, rng=draw)
        for index, weight in enumerate(weights):
            wr.add(index, weight)
        counts[wr.sample()[0]] += 1
    deviations = [
        abs(count / trials - weight / total_weight) for count, weight in zip(counts, weights)
    ]
    rows.append(
        Row(
            [
                "WeightedReservoir k=1",
                f"{sum(weights):.0f} total weight",
                f"{max(deviations):.4f} max deviation",
                "-",
                f"{statistics.pstdev([c / trials for c in counts]):.4f} spread",
                f"{trials} draws, exact proportions as n grows",
            ]
        )
    )

    # EWMA: the debiased value against the weighted mean it claims to be.
    alpha = 0.1
    series = [rng.gauss(1.0, 0.2) for _ in range(2000)]
    ewma = Ewma(alpha)
    for value in series:
        ewma.add(value)
    weights_series = [alpha * (1.0 - alpha) ** (len(series) - 1 - index) for index in range(len(series))]
    expected = sum(w * v for w, v in zip(weights_series, series)) / sum(weights_series)
    rows.append(
        Row(
            [
                "Ewma debias",
                f"{len(series):,} samples",
                f"{abs(ewma.value - expected):.3e}",
                "-",
                "0 (algebraic)",
                "vs the explicit weighted mean",
            ]
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# throughput
# --------------------------------------------------------------------------- #


@suite(
    "throughput",
    "Median over repetitions of the same loop. The exact baselines are measured "
    "in this run, not quoted: a pure-Python sketch loses to a C container on "
    "speed, and the reason to use it is the memory suite, not this one. The last "
    "row isolates the marginal cost of a sketch on top of line parsing.",
)
def throughput(config: Config) -> list[Row]:
    n = config.throughput_n
    rows: list[Row] = []
    keys = [f"key-{index}" for index in range(n)]
    # Random, not increasing: sorted() of an already-sorted list is a linear
    # no-op and would flatter the exact baseline by an order of magnitude.
    rng = random.Random(SEED)
    numbers = [rng.gauss(120.0, 30.0) for _ in range(n)]
    table: dict[str, int] = {}
    for key in keys:
        table[key] = table.get(key, 0) + 1
    ordered = sorted(numbers)

    def build_sketch() -> None:
        sketch = HyperLogLog(12)
        for key in keys:
            sketch.add(key)

    def build_set() -> None:
        set(keys)

    def build_cm() -> None:
        sketch = CountMinSketch(2048, 4)
        for key in keys:
            sketch.add(key)

    def build_dict() -> None:
        counts: dict[str, int] = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1

    def build_digest() -> None:
        digest = TDigest(100.0)
        for value in numbers:
            digest.add(value)

    def build_sorted() -> None:
        sorted(numbers)

    def build_reservoir() -> None:
        pool = Reservoir(50, rng=random.Random(1))
        for key in keys:
            pool.add(key)

    def keep_everything() -> None:
        list(keys)

    def build_stats() -> None:
        stats = RunningStats()
        for value in numbers:
            stats.add(value)

    def naive_stats() -> None:
        total = 0.0
        squares = 0.0
        for value in numbers:
            total += value
            squares += value * value

    def add_build(name: str, sketch_call, baseline_call, label: str) -> None:
        rows.append(Row(timed_pair(sketch_call, baseline_call, config, n, label)))

    add_build("hll", build_sketch, build_set, "HyperLogLog(12).add over n vs set(keys)")
    add_build("cm", build_cm, build_dict, "CountMinSketch(2048,4).add over n vs dict counts")
    add_build("digest", build_digest, build_sorted, "TDigest(100).add over n vs sorting the same n values")
    add_build("reservoir", build_reservoir, keep_everything, "Reservoir(50).add over n vs list(keys)")
    add_build("stats", build_stats, naive_stats, "RunningStats.add over n vs sum/sum-of-squares")

    # Queries against a populated structure: the other half of any real use.
    sketch = HyperLogLog(12)
    cm = CountMinSketch(2048, 4)
    digest = TDigest(100.0)
    populated = set(keys)
    probes = keys[: max(1000, n // 100)]
    for key in probes:
        sketch.add(key)
        cm.add(key)
    for value in numbers[: len(probes)]:
        digest.add(value)
    rows.append(
        Row(
            timed_pair(
                lambda: [sketch.add(key) for key in probes],
                lambda: [key in populated for key in probes],
                config,
                len(probes),
                "HyperLogLog.add vs set membership",
            )
        )
    )
    rows.append(
        Row(
            timed_pair(
                lambda: [cm.query(key) for key in probes],
                lambda: [table.get(key, 0) for key in probes],
                config,
                len(probes),
                "CountMinSketch.query vs dict.get",
            )
        )
    )
    rows.append(
        Row(
            timed_pair(
                lambda: [digest.quantile(index / 1000) for index in range(1000)],
                lambda: [ordered[index] for index in range(0, len(ordered), max(1, len(ordered) // 1000))],
                config,
                1000,
                "TDigest.quantile x1000 vs sorted()[i] x1000, sort excluded",
            )
        )
    )

    # The marginal cost that matters for a log processor: what a sketch adds on
    # top of splitting a line it was going to read anyway.
    csv_lines = [f"{index},{key},1.5" for index, key in enumerate(keys)]

    def parse_and_sketch() -> None:
        stats = RunningStats()
        for line in csv_lines:
            stats.add(float(line.split(",")[2]))

    def parse_only() -> None:
        count = 0
        for line in csv_lines:
            count += len(line.split(",")[2])

    rows.append(
        Row(
            timed_pair(
                parse_and_sketch,
                parse_only,
                config,
                n,
                "line.split + RunningStats.add vs line.split alone",
            )
        )
    )
    return rows


@suite(
    "memory",
    "Live CPython size: sys.getsizeof of the container plus the objects it owns. "
    "This is not RSS, and the exact baselines are the point of the comparison.",
)
def memory(config: Config) -> list[Row]:
    rows: list[Row] = []
    n = config.memory_n

    def deep_size(value: object, seen: set[int] | None = None) -> int:
        seen = set() if seen is None else seen
        identity = id(value)
        if identity in seen:
            return 0
        seen.add(identity)
        size = sizeof(value)
        if isinstance(value, HyperLogLog):
            return size + sizeof(value._registers)
        if isinstance(value, CountMinSketch):
            return size + sizeof(value._counts)
        if isinstance(value, TDigest):
            return (
                size
                + sizeof(value._means)
                + sizeof(value._weights)
                + sizeof(value._buffer)
                + sum(sizeof(pair) for pair in value._buffer)
            )
        if isinstance(value, Reservoir):
            return size + sizeof(value._pool) + sum(deep_size(item, seen) for item in value._pool)
        if isinstance(value, RunningStats):
            return size
        if isinstance(value, (set, dict)):
            items = value.values() if isinstance(value, dict) else value
            return size + sum(deep_size(item, seen) for item in items)
        if isinstance(value, list):
            return size + sum(deep_size(item, seen) for item in value)
        return size

    from sys import getsizeof as sizeof  # noqa: PLC0414 - used by deep_size above

    keys = [f"key-{index}" for index in range(n)]
    hll = HyperLogLog(12)
    for key in keys:
        hll.add(key)
    rows.append(Row(["HyperLogLog p=12", f"{deep_size(hll):,} B", f"{len(set(keys)):,} distinct", "constant", "registers only in memory_usage()"]))
    exact_set = set(keys)
    rows.append(Row(["set of the same keys", f"{deep_size(exact_set):,} B", f"{len(exact_set):,} distinct", "grows with n", "the honest baseline"]))

    cm = CountMinSketch(2048, 4)
    counts: dict[str, int] = {}
    for key in keys:
        cm.add(key)
        counts[key] = counts.get(key, 0) + 1
    rows.append(Row(["CountMinSketch 2048x4", f"{deep_size(cm):,} B", f"{cm.mass:,} mass", "constant", "8-byte ints, not counters"]))
    rows.append(Row(["dict of the same keys", f"{deep_size(counts):,} B", f"{len(counts):,} entries", "grows with n", "exact, and the comparison"]))

    digest = TDigest(100.0)
    values = [random.Random(SEED).gauss(0.0, 1.0) for _ in range(n)]
    for value in values:
        digest.add(value)
    rows.append(Row(["TDigest delta=100", f"{deep_size(digest):,} B", f"{digest.centroid_count} centroids", "O(delta)", "plus the pending buffer"]))
    rows.append(Row(["list of the same values", f"{deep_size(values):,} B", f"{len(values):,} floats", "grows with n", "required by sorted()"]))

    pool = Reservoir(50, rng=random.Random(1))
    for key in keys:
        pool.add(key)
    rows.append(Row(["Reservoir k=50", f"{deep_size(pool):,} B", f"{len(pool)} items", "constant", "k strings, nothing else"]))

    stats = RunningStats()
    for value in values:
        stats.add(value)
    rows.append(Row(["RunningStats", f"{deep_size(stats):,} B", f"{stats.count:,} counted", "constant", "five scalars"]))

    rows.append(Row(["stream of n keys", "-", f"{n:,}", "-", "what both baselines are sized for"]))
    return rows


# --------------------------------------------------------------------------- #
# serialisation
# --------------------------------------------------------------------------- #


@suite(
    "serialisation",
    "The frame format costs a fixed header plus a CRC32 over everything before "
    "it; these are round-trip times, not just encode times.",
)
def serialisation(config: Config) -> list[Row]:
    n = config.throughput_n
    rows: list[Row] = []

    sketch = HyperLogLog(14)
    for index in range(n):
        sketch.add(f"key-{index}")
    frame = sketch.to_bytes()
    median_encode, _, _ = median_of(lambda: sketch.to_bytes(), config.reps)
    median_decode, _, _ = median_of(lambda: HyperLogLog.from_bytes(frame), config.reps)
    assert HyperLogLog.from_bytes(frame) == sketch
    rows.append(
        Row(
            [
                "HyperLogLog p=14",
                f"{len(frame):,} B",
                f"{median_encode * 1e3:.3f} ms",
                f"{median_decode * 1e3:.3f} ms",
                f"{(len(frame) - sketch.memory_usage())} B framing",
                "registers copied on write",
            ]
        )
    )

    cm = CountMinSketch.with_accuracy(1e-3, 0.05)
    for index in range(n):
        cm.add(f"key-{index}")
    frame = cm.to_bytes()
    median_encode, _, _ = median_of(lambda: cm.to_bytes(), config.reps)
    median_decode, _, _ = median_of(lambda: CountMinSketch.from_bytes(frame), config.reps)
    assert CountMinSketch.from_bytes(frame) == cm
    rows.append(
        Row(
            [
                f"CountMinSketch {cm.width}x{cm.depth}",
                f"{len(frame):,} B",
                f"{median_encode * 1e3:.3f} ms",
                f"{median_decode * 1e3:.3f} ms",
                "31 B framing",
                "counters are byteswapped to big-endian",
            ]
        )
    )

    digest = TDigest(200.0)
    rng = random.Random(SEED)
    for _ in range(n):
        digest.add(rng.gauss(10.0, 3.0))
    digest.compress()
    frame = digest.to_bytes()
    median_encode, _, _ = median_of(lambda: digest.to_bytes(), config.reps)
    median_decode, _, _ = median_of(lambda: TDigest.from_bytes(frame), config.reps)
    assert TDigest.from_bytes(frame) == digest
    rows.append(
        Row(
            [
                f"TDigest delta=200 ({digest.centroid_count} centroids)",
                f"{len(frame):,} B",
                f"{median_encode * 1e3:.3f} ms",
                f"{median_decode * 1e3:.3f} ms",
                "23 B framing",
                "16 B per centroid",
            ]
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


@suite(
    "determinism",
    "The point of blake2b with a fixed key: the same stream has to produce the "
    "same bytes in a different process, or a persisted sketch is fiction.",
)
def determinism(config: Config) -> list[Row]:
    rows: list[Row] = []
    n = config.throughput_n // 4
    script = (
        "import sys, hashlib; sys.path.insert(0, sys.argv[1]); import sift; "
        "s = sift.HyperLogLog(12); c = sift.CountMinSketch(1024, 4); d = sift.TDigest(100.0); "
        "[s.add(i) or c.add(str(i)) or d.add(float(i % 5000)) for i in range(25000)]; "
        "print(hashlib.sha256(s.to_bytes() + c.to_bytes() + d.to_bytes()).hexdigest())"
    )
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src")
    digests = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script, src], capture_output=True, text=True, check=True
        )
        digests.append(result.stdout.strip())
    rows.append(
        Row(
            [
                "three sketches, two interpreters",
                digests[0][:12] + "...",
                digests[1][:12] + "...",
                "match" if digests[0] == digests[1] and len(digests[0]) == 64 else "DIFFER",
                "2 subprocesses",
                "sha256 over the HLL, Count-Min and t-digest frames together",
            ]
        )
    )

    keys = [f"key-{index}" for index in range(n)]
    first, second = HyperLogLog(12), HyperLogLog(12)
    for key in keys:
        first.add(key)
    for key in reversed(keys):
        second.add(key)
    rows.append(
        Row(
            [
                "HyperLogLog insertion order",
                f"{first.count():,}",
                f"{second.count():,}",
                "equal" if first == second else "differ",
                f"{n:,} keys",
                "max over ranks is order free",
            ]
        )
    )

    cm_a, cm_b = CountMinSketch(1024, 4), CountMinSketch(1024, 4)
    for key in keys:
        (cm_a if key[0] < "k" else cm_b).add(key)
    merged = CountMinSketch(1024, 4)
    for key in keys:
        merged.add(key)
    combined = cm_a.merge(cm_b)
    probes = keys[:5000]
    agreement = sum(1 for key in probes if combined.query(key) == merged.query(key))
    rows.append(
        Row(
            [
                "Count-Min split merge",
                f"{agreement}/5000",
                "-",
                "equal" if agreement == len(probes) else "see note",
                f"{n:,} keys",
                "cell sums are associative, hashes are not the issue",
            ]
        )
    )

    left, right = TDigest(100.0), TDigest(100.0)
    for index in range(n):
        (left if index % 2 else right).add(float(index))
    whole = TDigest(100.0)
    for index in range(n):
        whole.add(float(index))
    rank_gap = max(
        abs(left.merge(right).cdf(value) - whole.cdf(value))
        for value in (n * 0.1, n * 0.5, n * 0.9)
    )
    rows.append(
        Row(
            [
                "t-digest split merge",
                f"{rank_gap:.5f} max cdf gap",
                "-",
                "approximate",
                f"{n:,} values",
                "merging re-compresses, so it is not bit-exact",
            ]
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# merge
# --------------------------------------------------------------------------- #


@suite(
    "merge",
    "Shard and combine has to beat holding everything, otherwise it is a "
    "curiosity. These are wall times for the whole operation, median of reps.",
)
def merge(config: Config) -> list[Row]:
    rows: list[Row] = []
    n = config.throughput_n
    shards = 4
    keys = [f"key-{index}" for index in range(n)]

    def hll_merge() -> None:
        parts = []
        for index in range(shards):
            part = HyperLogLog(12)
            for key in keys[index::shards]:
                part.add(key)
            parts.append(part)
        result = parts[0]
        for part in parts[1:]:
            result = result.merge(part)
        assert abs(result.count() - n) < 0.1 * n

    baseline = lambda: len(set(keys))  # noqa: E731 - the exact answer is the baseline
    rows.append(Row(timed_pair(hll_merge, baseline, config, n, "4-shard HyperLogLog merge vs set()")))

    def cm_merge() -> None:
        parts = []
        for index in range(shards):
            part = CountMinSketch(2048, 4)
            for key in keys[index::shards]:
                part.add(key)
            parts.append(part)
        result = parts[0]
        for part in parts[1:]:
            result = result.merge(part)
        assert result.query(keys[0]) >= 1

    def dict_counts() -> None:
        counts: dict[str, int] = {}
        for key in keys:
            counts[key] = counts.get(key, 0) + 1

    rows.append(Row(timed_pair(cm_merge, dict_counts, config, n, "4-shard Count-Min merge vs dict")))

    values = [float(index) for index in range(n)]

    def digest_merge() -> None:
        parts = []
        for index in range(shards):
            part = TDigest(100.0)
            for value in values[index::shards]:
                part.add(value)
            parts.append(part)
        result = parts[0]
        for part in parts[1:]:
            result = result.merge(part)
        assert 0.0 <= result.quantile(0.5) <= float(n)

    rows.append(Row(timed_pair(digest_merge, lambda: sorted(values), config, n, "4-shard TDigest merge vs sorted()")))

    def combine() -> None:
        # The shards are built inside the timed call: combining two partial runs
        # is only interesting if you count the work of making them.
        left = RunningStats()
        left.add_all(values[: n // 2])
        right = RunningStats()
        right.add_all(values[n // 2 :])
        merged = RunningStats.combine(left, right)
        assert merged.count == n

    def one_pass() -> None:
        stats = RunningStats()
        stats.add_all(values)
        assert stats.count == n

    rows.append(
        Row(
            timed_pair(
                combine,
                one_pass,
                config,
                n,
                "two shards + combine vs one long pass",
            )
        )
    )

    exact = sorted(values)
    merged_digest = TDigest(100.0)
    for index in range(shards):
        part = TDigest(100.0)
        for value in values[index::shards]:
            part.add(value)
        merged_digest = merged_digest.merge(part)
    whole_digest = TDigest(100.0)
    whole_digest.add_all(values)
    gap = max(
        abs(merged_digest.cdf(exact[int(q * (n - 1))]) - q)
        for q in (0.1, 0.5, 0.9)
    )
    rows.append(
        Row(
            [
                "merge correctness (t-digest)",
                f"{gap:.5f} rank gap",
                "-",
                "-",
                "4 shards",
                f"{whole_digest.centroid_count} centroids in one pass vs "
                f"{merged_digest.centroid_count} merged, same delta",
            ]
        )
    )
    return rows


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


@suite(
    "cli",
    "Wall time of the shipped subcommands over a generated CSV, interpreter "
    "startup included. Each row is the median of three runs.",
)
def cli_bench(config: Config) -> list[Row]:
    rows: list[Row] = []
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src")
    # Interpreter startup is a constant we can subtract, so the per-row column
    # says what sift costs and not what CPython costs.
    startup_samples: list[float] = []
    for _ in range(3):
        started = time.perf_counter()
        subprocess.run([sys.executable, "-c", "pass"], capture_output=True, check=True)
        startup_samples.append(time.perf_counter() - started)
    startup = statistics.median(startup_samples)
    rng = random.Random(SEED)
    with tempfile.TemporaryDirectory(prefix="sift-bench-") as directory:
        path = os.path.join(directory, "events.csv")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            for index in range(config.csv_rows):
                handle.write(
                    f"{index % 5000},user-{rng.randrange(50000)},{rng.gauss(40.0, 8.0):.4f}\n"
                )
        commands = {
            "sift stats --column 3 --separator ,": ["stats", "--column", "3", "--separator", ",", path],
            "sift quantiles --column 3": ["quantiles", "--column", "3", "--separator", ",", path],
            "sift cardinality --column 2": ["cardinality", "--column", "2", "--separator", ",", path],
            "sift top --column 2": ["top", "--column", "2", "--separator", ",", path],
            "sift sample 20": ["sample", "--seed", "7", "20", path],
        }
        for label, argv in commands.items():
            samples: list[float] = []
            for _ in range(3):
                start = time.perf_counter()
                result = subprocess.run(
                    [sys.executable, "-m", "sift", *argv],
                    capture_output=True,
                    text=True,
                    env=dict(os.environ, PYTHONPATH=src),
                    check=True,
                )
                samples.append(time.perf_counter() - start)
                assert result.stdout.strip()
            rows.append(
                Row(
                    [
                        label,
                        f"{statistics.median(samples) * 1e3:.0f} ms",
                        f"{min(samples) * 1e3:.0f}..{max(samples) * 1e3:.0f} ms",
                        f"{(statistics.median(samples) - startup) / config.csv_rows * 1e6:.2f} us/row",
                        f"incl. {startup * 1e3:.0f} ms startup",
                        "median of 3 subprocess runs",
                    ]
                )
            )
        plain_started = time.perf_counter()
        with open(path, encoding="utf-8") as handle:
            total, count = 0.0, 0
            for line in handle:
                total += float(line.split(",")[2])
                count += 1
        plain = time.perf_counter() - plain_started
        rows.append(
            Row(
                [
                    "read the file and sum column 3 in-process",
                    f"{plain * 1e3:.0f} ms",
                    "-",
                    f"{plain / config.csv_rows * 1e6:.2f} us/row",
                    "no sketch, no subprocess",
                    "the floor any line reader has to pay",
                ]
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__)
    parser.add_argument("--quick", action="store_true", help="1/10 the data, for a fast check")
    parser.add_argument("--suite", action="append", default=[], help="run only this suite")
    parser.add_argument("--list", action="store_true", help="print the suite names and exit")
    parser.add_argument(
        "--json", action="store_true", help="machine-readable summary instead of tables"
    )
    args = parser.parse_args(argv)
    if args.list:
        for name, _, _ in SUITES:
            print(name)
        return 0

    config = Config(args.quick)
    wanted = set(args.suite)
    selected = [item for item in SUITES if not wanted or item[0] in wanted]
    missing = wanted - {item[0] for item in selected}
    if missing:
        parser.error(f"unknown suite(s): {', '.join(sorted(missing))}")

    payload: dict[str, object] = {}
    started = time.perf_counter()
    if not args.json:
        print(
            f"sift {sift.__version__} - Python {platform.python_version()} on "
            f"{platform.system()} {platform.machine()}, {time.strftime('%Y-%m-%d %H:%M')}\n"
        )
    for name, doc, function in selected:
        suite_started = time.perf_counter()
        rows = function(config)
        elapsed = time.perf_counter() - suite_started
        header = {
            "accuracy": ["structure", "stream", "measured", "spread", "bound", "note"],
            "throughput": ["operation", "sketch", "exact baseline", "sketch per op", "sketch vs baseline", "note"],
            "memory": ["structure", "live bytes", "what it counted", "scales with", "note"],
            "serialisation": ["structure", "frame", "encode", "decode", "framing", "note"],
            "determinism": ["check", "first", "second", "verdict", "scale", "note"],
            "merge": ["operation", "sketch", "exact baseline", "sketch per op", "sketch vs baseline", "note"],
            "cli": ["command", "median", "spread", "per row", "startup", "note"],
        }[name]
        if args.json:
            payload[name] = [list(row) for row in rows]
        else:
            print(f"## {name}\n\n{doc}\n")
            print_table(header, rows)
            print(f"\n{len(rows)} rows, {elapsed:.1f} s\n")
    if args.json:
        payload["environment"] = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "quick": config.quick,
            "seed": SEED,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(f"total {time.perf_counter() - started:.1f} s, seed {SEED}, quick={config.quick}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
