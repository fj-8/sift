"""Welford/Chan moments and EWMA tests. SPDX-License-Identifier: MIT OR Apache-2.0"""

from __future__ import annotations

import math
import random
import statistics
import unittest

from sift import Ewma, RunningStats


def close(self, value: float, expected: float, rel: float = 1e-10) -> None:
    """Relative comparison: the reference moments are exact-rational."""
    span = max(abs(expected), 1.0) * rel
    self.assertAlmostEqual(value, expected, delta=span)



class TestValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.stats = RunningStats()

    def test_empty_stream_answers_nothing(self) -> None:
        empty = RunningStats()
        self.assertEqual(empty.count, 0)
        self.assertEqual(repr(empty), "RunningStats(empty)")
        for accessor in (
            lambda: empty.mean,
            lambda: empty.variance,
            lambda: empty.population_variance,
            lambda: empty.stdev,
            lambda: empty.min,
            lambda: empty.max,
        ):
            with self.assertRaises(ValueError):
                accessor()

    def test_rejects_non_numbers_and_bools(self) -> None:
        for bad in (True, False, "1", None, [1]):
            with self.assertRaises(TypeError):
                self.stats.add(bad)

    def test_rejects_nan_before_it_spreads(self) -> None:
        with self.assertRaises(ValueError):
            self.stats.add(float("nan"))
        self.assertEqual(self.stats.count, 0)

    def test_sample_variance_needs_two_points(self) -> None:
        self.stats.add(5.0)
        self.assertEqual(self.stats.mean, 5.0)
        self.assertEqual(self.stats.population_variance, 0.0)
        with self.assertRaises(ValueError):
            self.stats.variance

    def test_accepts_ints_and_bool_like_numbers(self) -> None:
        self.stats.add_all([1, 2, 3])
        self.assertEqual(self.stats.count, 3)
        self.assertAlmostEqual(self.stats.mean, 2.0)


class TestAgainstStatisticsModule(unittest.TestCase):
    def test_matches_the_reference_implementation(self) -> None:
        rng = random.Random(2024)
        data = [rng.gauss(12.5, 3.25) for _ in range(10_000)]
        data += [rng.randint(-50, 50) for _ in range(500)]
        stats = RunningStats()
        stats.add_all(data)
        self.assertEqual(stats.count, len(data))
        close(self, stats.mean, statistics.fmean(data))
        close(self, stats.variance, statistics.variance(data), rel=1e-9)
        close(self, stats.stdev, statistics.stdev(data), rel=1e-9)
        close(self, stats.population_variance, statistics.pvariance(data), rel=1e-9)
        close(self, stats.pstdev, statistics.pstdev(data), rel=1e-9)
        self.assertEqual(stats.min, min(data))
        self.assertEqual(stats.max, max(data))

    def test_repr_shows_the_summary(self) -> None:
        stats = RunningStats()
        stats.add_all([1.0, 2.0, 3.0])
        self.assertEqual(
            repr(stats), "RunningStats(n=3, mean=2, stdev=1, min=1, max=3)"
        )


class TestNumericalStability(unittest.TestCase):
    def test_large_offset_does_not_break_the_variance(self) -> None:
        # sum(x) and sum(x*x) cancel catastrophically here; Welford does not.
        base = 1e9
        data = [base + value for value in (1.0, 2.0, 3.0, 4.0, 5.0)]
        stats = RunningStats()
        stats.add_all(data)
        n = len(data)
        total = sum(data)
        total_squares = sum(value * value for value in data)
        naive = total_squares / n - (total / n) ** 2
        self.assertLess(abs(stats.population_variance - 2.0), 1e-6)
        self.assertLess(abs(stats.variance - 2.5), 1e-6)
        self.assertGreater(abs(naive - 2.0), 0.5)
        self.assertGreater(abs(naive - 2.0), abs(stats.population_variance - 2.0) + 0.5)

    def test_exact_arithmetic_on_integers(self) -> None:
        stats = RunningStats()
        stats.add_all([10, 20, 30, 40])
        self.assertEqual(stats.mean, 25.0)
        self.assertEqual(stats.variance, 166 + 2.0 / 3.0)


class TestCombine(unittest.TestCase):
    def test_combine_reproduces_a_single_pass(self) -> None:
        rng = random.Random(99)
        data = [rng.gauss(3.0, 7.0) for _ in range(9000)]
        whole = RunningStats()
        whole.add_all(data)
        left, right = RunningStats(), RunningStats()
        left.add_all(data[:4000])
        right.add_all(data[4000:])
        merged = RunningStats.combine(left, right)
        self.assertEqual(merged.count, whole.count)
        close(self, merged.mean, whole.mean, rel=1e-12)
        close(self, merged.variance, whole.variance, rel=1e-9)
        self.assertEqual(merged.min, whole.min)
        self.assertEqual(merged.max, whole.max)

    def test_combine_is_order_insensitive(self) -> None:
        rng = random.Random(7)
        chunks = [[rng.random() * 100.0 for _ in range(300)] for _ in range(5)]
        partials = []
        for chunk in chunks:
            stats = RunningStats()
            stats.add_all(chunk)
            partials.append(stats)
        forward = partials[0]
        for stats in partials[1:]:
            forward = RunningStats.combine(forward, stats)
        backward = partials[-1]
        for stats in reversed(partials[:-1]):
            backward = RunningStats.combine(backward, stats)
        close(self, forward.mean, backward.mean, rel=1e-12)
        close(self, forward.variance, backward.variance, rel=1e-9)

    def test_balanced_tree_matches_the_stream(self) -> None:
        rng = random.Random(1234)
        data = [rng.gauss(0.0, 1.0) for _ in range(2048)]
        level = []
        for start in range(0, len(data), 16):
            leaf = RunningStats()
            leaf.add_all(data[start : start + 16])
            level.append(leaf)
        while len(level) > 1:
            level = [
                RunningStats.combine(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)
            ]
        whole = RunningStats()
        whole.add_all(data)
        close(self, level[0].mean, whole.mean, rel=1e-12)
        close(self, level[0].variance, whole.variance, rel=1e-9)

    def test_combine_with_empty_sides(self) -> None:
        empty = RunningStats()
        one = RunningStats()
        one.add_all([2.0, 4.0])
        self.assertEqual(RunningStats.combine(empty, one), one)
        self.assertEqual(RunningStats.combine(one, empty), one)
        self.assertEqual(RunningStats.combine(empty, empty).count, 0)
        with self.assertRaises(ValueError):
            RunningStats.combine(empty, empty).mean

    def test_combine_rejects_other_types(self) -> None:
        stats = RunningStats()
        stats.add(1.0)
        with self.assertRaises(TypeError):
            RunningStats.combine(stats, [1, 2, 3])  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            RunningStats.combine(1.0, stats)  # type: ignore[arg-type]


class TestState(unittest.TestCase):
    def test_state_round_trip_preserves_everything(self) -> None:
        rng = random.Random(5)
        stats = RunningStats()
        stats.add_all([rng.random() for _ in range(50)])
        resumed = RunningStats.from_state(stats.state())
        self.assertEqual(resumed, stats)
        self.assertEqual(resumed.state(), stats.state())
        resumed.add(1.0)
        stats.add(1.0)
        self.assertEqual(resumed, stats)

    def test_empty_state_round_trips(self) -> None:
        self.assertEqual(RunningStats.from_state(RunningStats().state()), RunningStats())


class TestEwma(unittest.TestCase):
    def test_alpha_validation(self) -> None:
        for bad in (0.0, -0.5, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                Ewma(bad)
        for bad in ("0.3", True, None):
            with self.assertRaises(TypeError):
                Ewma(bad)

    def test_with_span_maps_to_the_usual_alpha(self) -> None:
        self.assertAlmostEqual(Ewma.with_span(9).alpha, 0.2)
        self.assertAlmostEqual(Ewma.with_span(1).alpha, 1.0)
        with self.assertRaises(ValueError):
            Ewma.with_span(0)

    def test_constant_stream_reports_that_constant_from_the_first_sample(self) -> None:
        ewma = Ewma(0.1)
        for _ in range(20):
            value = ewma.add(1000.0)
        self.assertAlmostEqual(value, 1000.0, places=12)
        first = Ewma(0.1).add(1000.0)
        self.assertAlmostEqual(first, 1000.0, places=12)

    def test_debiased_value_is_the_exact_weighted_mean(self) -> None:
        alpha = 0.15
        data = [float(index) for index in range(1, 41)]
        ewma = Ewma(alpha)
        for count, value in enumerate(data, start=1):
            ewma.add(value)
            weights = [alpha * (1.0 - alpha) ** (count - 1 - i) for i in range(count)]
            expected = sum(w * x for w, x in zip(weights, data[:count])) / sum(weights)
            self.assertAlmostEqual(ewma.value, expected, places=10)

    def test_without_debiasing_the_first_samples_are_pulled_to_zero(self) -> None:
        biased = Ewma(0.1, debias=False)
        self.assertAlmostEqual(biased.add(1000.0), 100.0)
        unbiased = Ewma(0.1)
        self.assertAlmostEqual(unbiased.add(1000.0), 1000.0)

    def test_alpha_one_tracks_the_last_value(self) -> None:
        ewma = Ewma(1.0)
        for value in (3.0, 1.0, 4.0):
            self.assertEqual(ewma.add(value), value)

    def test_step_response_moves_monotonically_toward_the_new_level(self) -> None:
        ewma = Ewma(0.25)
        for _ in range(50):
            ewma.add(0.0)
        previous = ewma.value
        for _ in range(10):
            ewma.add(100.0)
            self.assertGreater(ewma.value, previous)
            self.assertLess(ewma.value, 100.0)
            previous = ewma.value
        # alpha 0.25 after 10 steps reaches 100 * (1 - 0.75**10), not 100. The
        # tolerance is the residual debias denominator from the 50 warm-up
        # samples, which is 1 - 0.75**50 rather than exactly 1.
        close(self, ewma.value, 100.0 * (1.0 - 0.75**10), rel=1e-5)

    def test_rejects_non_numbers(self) -> None:
        ewma = Ewma(0.5)
        with self.assertRaises(TypeError):
            ewma.add("1")
        with self.assertRaises(TypeError):
            ewma.add(True)
        with self.assertRaises(ValueError):
            ewma.value

    def test_repr(self) -> None:
        empty = Ewma(0.3)
        self.assertEqual(repr(empty), "Ewma(alpha=0.3, empty)")
        empty.add(2.0)
        self.assertEqual(repr(empty), "Ewma(alpha=0.3, n=1, value=2)")


class TestDocs(unittest.TestCase):
    def test_the_readme_example_runs_as_written(self) -> None:
        stats = RunningStats()
        ewma = Ewma(0.1)
        for value in (12.0, 14.0, 11.0, 9.0, 15.0):
            stats.add(value)
            ewma.add(value)
        self.assertEqual(stats.count, 5)
        self.assertAlmostEqual(stats.mean, 12.2, places=12)
        self.assertAlmostEqual(stats.variance, 5.7, places=12)
        self.assertLessEqual(ewma.value, 12.2 + 0.5)
        self.assertGreater(ewma.value, 9.0)
        self.assertEqual(stats.min, 9.0)
        self.assertEqual(stats.max, 15.0)
        self.assertTrue(math.isfinite(ewma.value))


if __name__ == "__main__":
    unittest.main()
