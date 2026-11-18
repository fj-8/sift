"""HyperLogLog tests. SPDX-License-Identifier: MIT OR Apache-2.0"""

from __future__ import annotations

import random
import unittest

from sift import HyperLogLog
from sift._serial import FrameError


def distinct_stream(seed: int, draws: int, universe: int) -> tuple[list[int], int]:
    rng = random.Random(seed)
    values = [rng.randrange(universe) for _ in range(draws)]
    return values, len(set(values))


class TestConstruction(unittest.TestCase):
    def test_default_precision_is_12(self) -> None:
        self.assertEqual(HyperLogLog().precision, 12)
        self.assertEqual(HyperLogLog().register_count, 4096)

    def test_precision_bounds_are_enforced(self) -> None:
        for bad in (3, 17, -1, 0):
            with self.assertRaises(ValueError):
                HyperLogLog(bad)

    def test_precision_must_be_an_int(self) -> None:
        for bad in (12.0, "12", True):
            with self.assertRaises(TypeError):
                HyperLogLog(bad)

    def test_empty_estimates_zero(self) -> None:
        self.assertEqual(HyperLogLog(10).count(), 0)

    def test_standard_error_matches_theory(self) -> None:
        for precision in (4, 8, 12, 16):
            sketch = HyperLogLog(precision)
            self.assertAlmostEqual(
                sketch.standard_error, 1.04 / (2**precision) ** 0.5, places=12
            )


class TestUpdate(unittest.TestCase):
    def test_one_item_estimates_one(self) -> None:
        # The raw estimator is wildly wrong at this size; linear counting is the
        # branch that makes it right, so this pins that branch down.
        sketch = HyperLogLog(12)
        sketch.add("only")
        self.assertEqual(sketch.count(), 1)

    def test_duplicates_do_not_move_the_state(self) -> None:
        sketch = HyperLogLog(10)
        sketch.add("x")
        before = sketch.to_bytes()
        for _ in range(1000):
            sketch.add("x")
        self.assertEqual(sketch.to_bytes(), before)

    def test_int_and_str_five_are_different_items(self) -> None:
        sketch = HyperLogLog(14)
        for value in (5, "5", b"5", True):
            sketch.add(value)
        # 5, "5", b"5" and True are four distinct encodings; the repeated 5 is not.
        self.assertLessEqual(abs(sketch.count() - 4), 60)
        self.assertEqual(HyperLogLog(14).count(), 0)

    def test_rejects_types_it_cannot_encode(self) -> None:
        sketch = HyperLogLog(8)
        for bad in ([1, 2], {"a": 1}, object()):
            with self.assertRaises(TypeError):
                sketch.add(bad)

    def test_none_is_a_value_not_a_missing_one(self) -> None:
        sketch = HyperLogLog(14)
        sketch.add(None)
        sketch.add(None)
        self.assertLess(sketch.count(), 60)

    def test_register_values_stay_in_range(self) -> None:
        values, _ = distinct_stream(3, 20000, 1 << 30)
        sketch = HyperLogLog(8)
        sketch.add_all(values)
        registers = sketch.registers()
        self.assertEqual(len(registers), sketch.register_count)
        # A rank is "leading zeros + 1" over the 64-p bits below the bucket
        # index, so that sum is the hard ceiling on any register value.
        self.assertLessEqual(max(registers), 64 - sketch.precision + 1)
        self.assertGreaterEqual(min(registers), 0)


class TestAccuracy(unittest.TestCase):
    def test_mean_error_is_small_and_unbiased(self) -> None:
        """Across seeds the average signed error stays well inside one stderr."""
        for precision in (8, 12):
            errors = []
            for seed in range(12):
                values, truth = distinct_stream(seed, 20000, 4_000_000)
                sketch = HyperLogLog(precision)
                sketch.add_all(values)
                errors.append((sketch.count() - truth) / truth)
            stderr = 1.04 / (2**precision) ** 0.5
            mean = sum(errors) / len(errors)
            spread = (sum((e - mean) ** 2 for e in errors) / (len(errors) - 1)) ** 0.5
            # |mean| must not look like a constant offset: it has to be smaller
            # than the trial-to-trial scatter divided by sqrt(n) times two.
            self.assertLess(abs(mean), 2.0 * spread / len(errors) ** 0.5 + stderr * 0.5)
            self.assertLess(spread, 2.5 * stderr)

    def test_estimate_within_three_sigma_on_a_clean_stream(self) -> None:
        values, truth = distinct_stream(11, 100000, 10_000_000)
        sketch = HyperLogLog(14)
        sketch.add_all(values)
        self.assertLess(abs(sketch.count() - truth) / truth, 3 * sketch.standard_error)


class TestMerge(unittest.TestCase):
    def test_merge_is_the_union_of_two_streams(self) -> None:
        left_values, left_distinct = distinct_stream(21, 20000, 2_000_000)
        right_values, right_distinct = distinct_stream(22, 20000, 2_000_000)
        both = left_values + right_values
        truth = len(set(both))

        a, b = HyperLogLog(12), HyperLogLog(12)
        a.add_all(left_values)
        b.add_all(right_values)
        merged = a.merge(b)
        whole = HyperLogLog(12)
        whole.add_all(both)

        self.assertLess(abs(merged.count() - truth), 4 * truth * whole.standard_error)
        self.assertEqual(merged.to_bytes(), whole.to_bytes(), "merge must be exact per register")
        # The union can never exceed the sum of the two cardinalities, but this
        # side is an estimate, so the check carries its own error budget.
        ceiling = (left_distinct + right_distinct) * (1.0 + 4.0 * merged.standard_error)
        self.assertLessEqual(merged.count(), ceiling)

    def test_merge_is_commutative_and_idempotent(self) -> None:
        a, b = HyperLogLog(10), HyperLogLog(10)
        a.add_all(distinct_stream(31, 5000, 1 << 20)[0])
        b.add_all(distinct_stream(32, 5000, 1 << 20)[0])
        self.assertEqual(a.merge(b).to_bytes(), b.merge(a).to_bytes())
        self.assertEqual(a.merge(a).to_bytes(), a.to_bytes())

    def test_merge_rejects_mismatched_configuration(self) -> None:
        a, b = HyperLogLog(10), HyperLogLog(11)
        with self.assertRaises(ValueError):
            a.merge(b)
        with self.assertRaises(TypeError):
            a.merge("not a sketch")  # type: ignore[arg-type]


class TestSerialisation(unittest.TestCase):
    def setUp(self) -> None:
        self.sketch = HyperLogLog(8)
        self.sketch.add_all(distinct_stream(41, 4000, 1 << 22)[0])
        self.frame = self.sketch.to_bytes()

    def test_round_trip_preserves_every_register(self) -> None:
        back = HyperLogLog.from_bytes(self.frame)
        self.assertEqual(back, self.sketch)
        self.assertEqual(back.count(), self.sketch.count())
        self.assertEqual(back.precision, self.sketch.precision)

    def test_frame_is_self_describing(self) -> None:
        self.assertEqual(self.frame[:4], b"SFT1")
        self.assertEqual(len(self.frame), 4 + 1 + 1 + 1 + 1 + 4 + 256 + 4)

    def test_truncation_is_rejected(self) -> None:
        for cut in (1, 8, len(self.frame) - 1, len(self.frame) // 2):
            with self.assertRaises(FrameError):
                HyperLogLog.from_bytes(self.frame[:cut])

    def test_single_byte_edits_are_rejected(self) -> None:
        caught = 0
        for index in range(len(self.frame)):
            mutated = bytearray(self.frame)
            mutated[index] ^= 0x01
            try:
                HyperLogLog.from_bytes(bytes(mutated))
            except FrameError:
                caught += 1
        self.assertEqual(caught, len(self.frame), "every byte is covered by the checksum")

    def test_wrong_kind_is_named_not_confused(self) -> None:
        from sift import CountMinSketch

        cm = CountMinSketch(16, 3).to_bytes()
        with self.assertRaises(FrameError) as caught:
            HyperLogLog.from_bytes(cm)
        self.assertIn("CountMinSketch", str(caught.exception))
        self.assertIn("HyperLogLog", str(caught.exception))

    def test_bad_magic_is_rejected(self) -> None:
        with self.assertRaises(FrameError):
            HyperLogLog.from_bytes(b"XXXX" + self.frame[4:])

    def test_unsupported_version_is_rejected(self) -> None:
        bumped = bytearray(self.frame)
        bumped[5] = 99
        with self.assertRaises(FrameError):
            HyperLogLog.from_bytes(bytes(bumped))


class TestIntrospection(unittest.TestCase):
    def test_memory_usage_is_the_register_file(self) -> None:
        for precision in (4, 8, 16):
            self.assertEqual(HyperLogLog(precision).memory_usage(), 2**precision)

    def test_repr_reports_the_useful_numbers(self) -> None:
        sketch = HyperLogLog(10)
        sketch.add("a")
        text = repr(sketch)
        self.assertIn("precision=10", text)
        self.assertIn("estimate=", text)

    def test_empty_register_counting(self) -> None:
        sketch = HyperLogLog(6)
        self.assertEqual(sketch.empty_registers(), 64)
        sketch.add("a")
        self.assertEqual(sketch.empty_registers(), 63)


if __name__ == "__main__":
    unittest.main()
