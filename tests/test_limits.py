"""The two promises the README makes about memory: structures stay bounded,
frames never allocate from a claim.

SPDX-License-Identifier: MIT OR Apache-2.0
"""

from __future__ import annotations

import random
import struct
import sys
import time
import unittest
from itertools import count

from sift import CountMinSketch, Ewma, HyperLogLog, Reservoir, RunningStats, TDigest
from sift import _serial


def cm_frame(depth: int, width: int, mass: int, payload: bytes = b"") -> bytes:
    """A hand-built Count-Min frame: the config is the only thing under attack."""
    config = (
        depth.to_bytes(4, "big")
        + width.to_bytes(4, "big")
        + mass.to_bytes(8, "big")
    )
    return _serial.encode(_serial._KIND_CM, config, payload)


class TestFrameGeometry(unittest.TestCase):
    """A hostile buffer may claim any size; none of these may cost memory."""

    def test_count_min_cannot_borrow_memory_from_its_own_claim(self) -> None:
        frame = cm_frame(depth=8, width=2**31 - 1, mass=0)
        started = time.perf_counter()
        with self.assertRaises(_serial.FrameError) as caught:
            CountMinSketch.from_bytes(frame)
        elapsed = time.perf_counter() - started
        message = str(caught.exception)
        self.assertIn("expected", message)
        self.assertIn(str(8 * (2**31 - 1) * 8), message)
        # Two gigabytes of array("q) would take far longer than this to build,
        # so the timing is the proof that nothing was allocated.
        self.assertLess(elapsed, 0.5, f"rejected a bogus frame in {elapsed:.3f}s")

    def test_count_min_depth_is_checked_against_the_same_byte_count(self) -> None:
        with self.assertRaises(_serial.FrameError):
            CountMinSketch.from_bytes(cm_frame(depth=2**31, width=8, mass=0))

    def test_count_min_rejects_a_payload_longer_than_the_geometry(self) -> None:
        sketch = CountMinSketch(8, 2)
        counters = sketch.cells().tobytes()
        with self.assertRaises(_serial.FrameError):
            CountMinSketch.from_bytes(cm_frame(2, 8, 0, counters + b"\x00" * 8))

    def test_hyperloglog_precision_byte_must_be_buildable(self) -> None:
        for bad in (0, 3, 17, 255):
            frame = _serial.encode(_serial._KIND_HLL, bytes((bad,)), b"")
            started = time.perf_counter()
            with self.subTest(precision=bad):
                with self.assertRaises(_serial.FrameError) as caught:
                    HyperLogLog.from_bytes(frame)
                self.assertIn("out of range", str(caught.exception))
                self.assertLess(time.perf_counter() - started, 0.2)

    def test_hyperloglog_register_array_is_the_declared_size(self) -> None:
        frame = _serial.encode(_serial._KIND_HLL, b"\x0c", b"\x01" * 100)
        with self.assertRaises(ValueError) as caught:
            HyperLogLog.from_bytes(frame)
        self.assertIn("expected 4096 registers", str(caught.exception))

    def test_tdigest_cannot_claim_centroids_it_does_not_have(self) -> None:
        body = struct.pack(">QI", 10, 2**31) + b"\x00" * 16
        frame = _serial.encode(_serial._KIND_TDIGEST, struct.pack(">d", 100.0), body)
        started = time.perf_counter()
        with self.assertRaises(_serial.FrameError) as caught:
            TDigest.from_bytes(frame)
        self.assertIn("truncated", str(caught.exception))
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_tdigest_buffer_claim_is_bounded_the_same_way(self) -> None:
        body = struct.pack(">QI", 8, 1) + struct.pack(">dQ", 1.0, 8)
        body += struct.pack(">I", 2**30) + struct.pack(">dQ", 2.0, 1)
        frame = _serial.encode(_serial._KIND_TDIGEST, struct.pack(">d", 100.0), body)
        started = time.perf_counter()
        with self.assertRaises(_serial.FrameError):
            TDigest.from_bytes(frame)
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_every_rejection_is_a_value_error(self) -> None:
        """Callers who only catch ValueError must not be surprised by a new class."""
        for frame, loader in (
            (cm_frame(4, 2**20, 0), CountMinSketch.from_bytes),
            (_serial.encode(_serial._KIND_HLL, b"\xff", b""), HyperLogLog.from_bytes),
            (_serial.MAGIC + b"\x00" * 8, TDigest.from_bytes),
        ):
            with self.subTest(loader=loader.__qualname__):
                with self.assertRaises(ValueError):
                    loader(frame)


class TestGrowthBounds(unittest.TestCase):
    """A stream of any length must not change what an ``add()`` costs."""

    def test_hyperloglog_is_the_same_size_at_one_and_two_hundred_thousand(self) -> None:
        sketch = HyperLogLog(10)
        base = sketch.memory_usage()
        for index in range(200_000):
            sketch.add(index)
        self.assertEqual(sketch.memory_usage(), base)
        self.assertEqual(base, 1024)

    def test_count_min_never_adds_a_cell(self) -> None:
        sketch = CountMinSketch(64, 3)
        for index in range(100_000):
            sketch.add(index)
        self.assertEqual(len(sketch.cells()), 192)
        self.assertEqual(sketch.mass, 100_000)

    def test_reservoir_holds_exactly_k_after_a_long_stream(self) -> None:
        pool = Reservoir(20, rng=random.Random(1))
        for index in range(500_000):
            pool.add(index)
        self.assertEqual(len(pool), 20)
        self.assertEqual(pool.seen, 500_000)

    def test_tdigest_centroids_track_compression_not_stream_length(self) -> None:
        small = TDigest(20.0)
        large = TDigest(200.0)
        rng_values = iter(count(1.0))
        for _ in range(100_000):
            value = next(rng_values)
            small.add(value)
            large.add(value)
        self.assertLess(small.centroid_count, 400)
        self.assertLess(large.centroid_count, 4000)
        self.assertLess(small.centroid_count, large.centroid_count)

    def test_the_pending_buffer_is_flushed_at_its_declared_size(self) -> None:
        digest = TDigest(100.0)
        high_water = 0
        for index in range(50_000):
            digest.add(index)
            high_water = max(high_water, digest.buffered)
        self.assertLessEqual(high_water, max(64, int(2 * digest.compression)))

    def test_running_stats_holds_nothing_but_scalars(self) -> None:
        stats = RunningStats()
        for index in range(200_000):
            stats.add(index)
        self.assertEqual(stats.count, 200_000)
        self.assertEqual(sys.getsizeof(stats), sys.getsizeof(RunningStats()))
        for name in RunningStats.__slots__:
            value = getattr(stats, name)
            with self.subTest(slot=name):
                self.assertIsInstance(value, (int, float))
                self.assertNotIsInstance(value, (list, dict, set, tuple, bytearray))

    def test_ewma_holds_no_history(self) -> None:
        stream = Ewma(0.1)
        for index in range(100_000):
            stream.add(index)
        self.assertEqual(stream.count, 100_000)
        self.assertEqual(sys.getsizeof(stream), sys.getsizeof(Ewma(0.1)))
        self.assertNotIn("_history", dir(stream))


if __name__ == "__main__":
    unittest.main()
