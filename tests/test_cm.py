"""Count-Min sketch tests. SPDX-License-Identifier: MIT OR Apache-2.0"""

from __future__ import annotations

import random
import unittest
from collections import Counter
from math import ceil, e, log

from sift import CountMinSketch
from sift._serial import FrameError


def zipf_stream(seed: int, rows: int, keys: int, s: float = 1.1) -> list[str]:
    """A skewed stream: real logs are never uniform, and uniform streams hide
    the collisions this sketch is actually being tested against."""
    rng = random.Random(seed)
    weights = [1.0 / (i ** s) for i in range(1, keys + 1)]
    total = sum(weights)
    cut = []
    walked = 0.0
    for weight in weights:
        walked += weight
        cut.append(walked / total)
    out = []
    for _ in range(rows):
        u = rng.random()
        lo, hi = 0, len(cut) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if cut[mid] < u:
                lo = mid + 1
            else:
                hi = mid
        out.append(f"key-{lo}")
    return out


class TestConstruction(unittest.TestCase):
    def test_dimensions_must_be_positive_ints(self) -> None:
        for width, depth in ((0, 3), (3, 0), (-1, 3), (3, -1)):
            with self.assertRaises(ValueError):
                CountMinSketch(width, depth)
        for bad in ((2.0, 3), (3, 2.0), (True, 3), (3, True)):
            with self.assertRaises(TypeError):
                CountMinSketch(*bad)

    def test_with_accuracy_uses_the_published_sizing(self) -> None:
        for eps, delta in ((1e-2, 0.1), (1e-3, 0.05), (1e-4, 0.01)):
            sketch = CountMinSketch.with_accuracy(eps, delta)
            self.assertEqual(sketch.width, ceil(e / eps))
            self.assertEqual(sketch.depth, ceil(log(1.0 / delta)))

    def test_with_accuracy_rejects_nonsense_parameters(self) -> None:
        for eps, delta in ((0.0, 0.1), (1.0, 0.1), (-0.5, 0.1), (0.1, 0.0), (0.1, 1.0)):
            with self.assertRaises(ValueError):
                CountMinSketch.with_accuracy(eps, delta)

    def test_starts_empty(self) -> None:
        sketch = CountMinSketch(64, 4)
        self.assertEqual(sketch.query("anything"), 0)
        self.assertEqual(sketch.mass, 0)
        self.assertNotIn("anything", sketch)


class TestGuarantee(unittest.TestCase):
    def test_estimate_is_never_below_the_truth(self) -> None:
        """The one-sided property is the contract; it must hold for every item."""
        sketch = CountMinSketch(2048, 4)
        stream = zipf_stream(1, 50000, 4000)
        truth = Counter(stream)
        for item in stream:
            sketch.add(item)
        self.assertTrue(all(sketch.query(item) >= count for item, count in truth.items()))
        self.assertEqual(sketch.mass, len(stream))

    def test_error_stays_inside_the_announced_bound(self) -> None:
        eps, delta = 1e-3, 0.05
        rows = 20000
        sketch = CountMinSketch.with_accuracy(eps, delta)
        truth = Counter(zipf_stream(2, rows, 5000))
        for item in truth.elements():
            sketch.add(item)
        budget = eps * rows
        # The guarantee is probabilistic, so the test allows a margin and a fixed
        # seed; a systematic blow-up would still fail it immediately.
        breaches = sum(1 for item, n in truth.items() if sketch.query(item) - n > budget)
        self.assertLess(breaches, max(2.0, (delta * 2.0) * len(truth)))

    def test_counted_add_accumulates(self) -> None:
        sketch = CountMinSketch(1024, 4)
        sketch.add("a", 7)
        sketch.add("a", 3)
        sketch.add("b")
        self.assertEqual(sketch.query("a"), 10)
        self.assertEqual(sketch.query("b"), 1)
        self.assertEqual(sketch.mass, 11)
        self.assertIn("a", sketch)

    def test_zero_count_is_a_no_op_and_negatives_are_refused(self) -> None:
        sketch = CountMinSketch(64, 3)
        sketch.add("a", 0)
        self.assertEqual(sketch.query("a"), 0)
        self.assertEqual(sketch.mass, 0)
        with self.assertRaises(ValueError):
            sketch.add("a", -1)
        for bad in (1.0, True, "2"):
            with self.assertRaises(TypeError):
                sketch.add("a", bad)


class TestMerge(unittest.TestCase):
    def test_merge_adds_counters_and_mass(self) -> None:
        a, b = CountMinSketch(256, 3), CountMinSketch(256, 3)
        for _ in range(5):
            a.add("x")
        for _ in range(2):
            b.add("x")
        b.add("y")
        both = a.merge(b)
        self.assertEqual(both.query("x"), 7)
        self.assertEqual(both.query("y"), 1)
        self.assertEqual(both.mass, a.mass + b.mass)

    def test_merge_is_associative_over_three_streams(self) -> None:
        streams = [zipf_stream(seed, 2000, 500) for seed in (5, 6, 7)]
        sketches = []
        for stream in streams:
            sketch = CountMinSketch(1024, 4)
            for item in stream:
                sketch.add(item)
            sketches.append(sketch)
        left = sketches[0].merge(sketches[1]).merge(sketches[2])
        right = sketches[0].merge(sketches[1].merge(sketches[2]))
        self.assertEqual(left.cells(), right.cells())
        self.assertEqual(left.mass, right.mass)

    def test_merge_rejects_mismatched_geometry(self) -> None:
        for other in (CountMinSketch(256, 4), CountMinSketch(128, 4), CountMinSketch(64, 3)):
            with self.assertRaises(ValueError):
                CountMinSketch(128, 3).merge(other)
        with self.assertRaises(TypeError):
            CountMinSketch(128, 3).merge("no")  # type: ignore[arg-type]


class TestState(unittest.TestCase):
    def test_cells_returns_a_copy(self) -> None:
        sketch = CountMinSketch(32, 3)
        sketch.add("a", 4)
        snapshot = sketch.cells()
        snapshot[0] = 999
        self.assertNotEqual(sketch.cells()[0], 999)
        self.assertEqual(sketch.query("a"), 4)

    def test_memory_usage_is_the_counter_grid(self) -> None:
        sketch = CountMinSketch(1000, 5)
        self.assertEqual(sketch.memory_usage(), 8 * 1000 * 5)

    def test_repr_shows_geometry_and_mass(self) -> None:
        text = repr(CountMinSketch(16, 2))
        self.assertIn("width=16", text)
        self.assertIn("depth=2", text)


class TestSerialisation(unittest.TestCase):
    def setUp(self) -> None:
        self.sketch = CountMinSketch(48, 3)
        for item in ("a", "b", "a", "c", "a"):
            self.sketch.add(item)
        self.frame = self.sketch.to_bytes()

    def test_round_trip(self) -> None:
        back = CountMinSketch.from_bytes(self.frame)
        self.assertEqual(back, self.sketch)
        self.assertEqual(back.query("a"), 3)
        self.assertEqual(back.mass, 5)

    def test_golden_bytes_pin_the_hash_construction(self) -> None:
        """Any change to hashing, framing or sizing must be a deliberate one."""
        sketch = CountMinSketch(8, 3)
        for item in ("alpha", "beta", "alpha", 7):
            sketch.add(item)
        self.assertEqual(
            sketch.to_bytes().hex(),
            "5346543102011000000003000000080000000000000004000000c00000000000"
            "0000000000000000000001000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000030000000000"
            "0000010000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000002000000000000000100000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000010000000000"
            "000000000000000000000300000000000000000000000000000000cbcad7e8",
        )

    def test_every_single_byte_edit_is_caught(self) -> None:
        for index in range(len(self.frame)):
            mutated = bytearray(self.frame)
            mutated[index] ^= 0x40
            with self.assertRaises(FrameError):
                CountMinSketch.from_bytes(bytes(mutated))

    def test_truncated_counter_payload_is_rejected(self) -> None:
        wide = CountMinSketch(100, 2).to_bytes()
        with self.assertRaises(FrameError):
            CountMinSketch.from_bytes(wide[:-8])

    def test_other_kinds_are_rejected_by_name(self) -> None:
        from sift import HyperLogLog

        with self.assertRaises(FrameError) as caught:
            CountMinSketch.from_bytes(HyperLogLog(6).to_bytes())
        self.assertIn("HyperLogLog", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
