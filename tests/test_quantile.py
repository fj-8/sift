"""t-digest tests. SPDX-License-Identifier: MIT OR Apache-2.0"""

from __future__ import annotations

import random
import unittest
from statistics import median

from sift import TDigest
from sift._serial import FrameError


def gaussian(n: int, seed: int, mu: float = 50.0, sigma: float = 10.0) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


class TestConstruction(unittest.TestCase):
    def test_compression_must_be_a_real_number_at_least_one(self) -> None:
        for bad in (0.5, 0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                TDigest(bad)
        for bad in ("100", True, None):
            with self.assertRaises(TypeError):
                TDigest(bad)

    def test_empty_digest_has_no_quantile(self) -> None:
        digest = TDigest()
        self.assertEqual(len(digest), 0)
        with self.assertRaises(ValueError):
            digest.quantile(0.5)
        with self.assertRaises(ValueError):
            digest.cdf(1.0)

    def test_single_value_answer_any_query(self) -> None:
        digest = TDigest(100.0)
        digest.add(7.0)
        self.assertEqual(len(digest), 1)
        for q in (0.0, 0.25, 0.5, 0.99, 1.0):
            self.assertEqual(digest.quantile(q), 7.0)
        # Regression: at a single centroid cdf() used to answer w0/(4n) instead
        # of that centroid's centre rank, which made it the wrong way round from
        # quantile() exactly at the joints.
        self.assertAlmostEqual(digest.cdf(7.0), 0.5, places=6)


class TestInputValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.digest = TDigest(50.0)

    def test_rejects_values_with_no_rank(self) -> None:
        for bad in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                self.digest.add(bad)

    def test_rejects_non_numbers_and_bools(self) -> None:
        for bad in ("1", True, None, [1]):
            with self.assertRaises(TypeError):
                self.digest.add(bad)

    def test_rejects_silly_weights(self) -> None:
        for bad in (0, -3, 1.5, True, "2"):
            with self.assertRaises((ValueError, TypeError)):
                self.digest.add(1.0, bad)

    def test_accepts_ints_and_promotes_them(self) -> None:
        self.digest.add(3)
        self.digest.add(4)
        self.assertEqual(len(self.digest), 2)
        self.assertLessEqual(self.digest.quantile(0.0), 4.0)


class TestAccuracy(unittest.TestCase):
    def test_rank_error_stays_small_across_the_distribution(self) -> None:
        data = gaussian(20000, seed=101)
        digest = TDigest(100.0)
        digest.add_all(data)
        exact = sorted(data)
        worst = 0.0
        for q in (0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999):
            estimate = digest.quantile(q)
            # rank error: what fraction of the data sits below the reported value?
            rank = sum(1 for value in exact if value <= estimate) / len(exact)
            worst = max(worst, abs(rank - q))
        self.assertLess(worst, 0.01, f"worst rank error {worst:.4f}")

    def test_median_matches_the_exact_median(self) -> None:
        data = gaussian(10001, seed=202)
        digest = TDigest(100.0)
        digest.add_all(data)
        self.assertAlmostEqual(digest.quantile(0.5), median(data), delta=0.05)

    def test_quantiles_are_monotone(self) -> None:
        digest = TDigest(20.0)
        rng = random.Random(7)
        for _ in range(5000):
            digest.add(rng.expovariate(1.0))
        values = [digest.quantile(q / 100.0) for q in range(101)]
        self.assertEqual(values, sorted(values))

    def test_extremes_are_inside_the_observed_range(self) -> None:
        data = gaussian(4000, seed=303)
        digest = TDigest(100.0)
        digest.add_all(data)
        self.assertGreaterEqual(digest.quantile(0.0), min(data) - 1e-9)
        self.assertLessEqual(digest.quantile(1.0), max(data) + 1e-9)

    def test_weights_behave_like_repetition(self) -> None:
        repeated, weighted = TDigest(100.0), TDigest(100.0)
        rng = random.Random(404)
        for _ in range(5000):
            value = rng.gauss(0.0, 1.0)
            weight = rng.choice([1, 1, 2, 5])
            for _ in range(weight):
                repeated.add(value)
            weighted.add(value, weight)
        self.assertEqual(len(weighted), len(repeated))
        for q in (0.1, 0.5, 0.9):
            self.assertAlmostEqual(weighted.quantile(q), repeated.quantile(q), delta=0.05)

    def test_cdf_inverts_the_quantile(self) -> None:
        digest = TDigest(100.0)
        digest.add_all(gaussian(8000, seed=505))
        for q in (0.1, 0.3, 0.5, 0.7, 0.9):
            self.assertAlmostEqual(digest.cdf(digest.quantile(q)), q, delta=0.02)

    def test_memory_is_bounded_by_compression_not_by_n(self) -> None:
        small = TDigest(100.0)
        small.add_all(gaussian(2000, seed=606))
        large = TDigest(100.0)
        large.add_all(gaussian(200000, seed=607))
        # Both should sit near the O(compression) budget, not near 200 000.
        self.assertLess(large.centroid_count, 4000)
        self.assertGreater(small.centroid_count, 0)


class TestMerge(unittest.TestCase):
    def test_merge_matches_one_stream_over_the_concatenation(self) -> None:
        left, right = gaussian(5000, seed=707), gaussian(5000, seed=708, mu=70.0)
        a, b = TDigest(100.0), TDigest(100.0)
        a.add_all(left)
        b.add_all(right)
        split = a.merge(b)
        whole = TDigest(100.0)
        whole.add_all(left + right)
        for q in (0.25, 0.5, 0.75):
            self.assertAlmostEqual(split.quantile(q), whole.quantile(q), delta=1.0)
        self.assertEqual(len(split), 10000)

    def test_merge_rejects_mismatched_compression_and_type(self) -> None:
        with self.assertRaises(ValueError):
            TDigest(100.0).merge(TDigest(50.0))
        with self.assertRaises(TypeError):
            TDigest(100.0).merge([1, 2, 3])  # type: ignore[arg-type]

    def test_copy_is_independent(self) -> None:
        original = TDigest(100.0)
        original.add_all(gaussian(500, seed=808))
        clone = original.copy()
        clone.add(10_000.0)
        self.assertNotEqual(clone, original)
        self.assertEqual(len(original), 500)


class TestSerialisation(unittest.TestCase):
    def setUp(self) -> None:
        self.digest = TDigest(40.0)
        self.digest.add_all(gaussian(700, seed=909))
        self.frame = self.digest.to_bytes()

    def test_round_trip_is_byte_identical(self) -> None:
        back = TDigest.from_bytes(self.frame)
        self.assertEqual(back, self.digest)
        self.assertEqual(back.to_bytes(), self.frame)
        self.assertAlmostEqual(back.quantile(0.9), self.digest.quantile(0.9), places=12)

    def test_round_trip_keeps_pending_buffer(self) -> None:
        fresh = TDigest(200.0)
        for value in (1.0, 2.0, 3.0):
            fresh.add(value)
        self.assertGreater(fresh.buffered, 0)
        back = TDigest.from_bytes(fresh.to_bytes())
        self.assertEqual(back.buffered, fresh.buffered)
        self.assertEqual(back.quantile(0.5), fresh.quantile(0.5))

    def test_single_byte_edits_are_rejected(self) -> None:
        for index in range(len(self.frame)):
            mutated = bytearray(self.frame)
            mutated[index] ^= 0x02
            with self.assertRaises(FrameError):
                TDigest.from_bytes(bytes(mutated))

    def test_truncated_centroid_list_is_rejected(self) -> None:
        with self.assertRaises(FrameError):
            TDigest.from_bytes(self.frame[:-4])
        with self.assertRaises(FrameError):
            TDigest.from_bytes(self.frame[: len(self.frame) - 20])

    def test_other_kinds_are_rejected_by_name(self) -> None:
        from sift import HyperLogLog

        with self.assertRaises(FrameError) as caught:
            TDigest.from_bytes(HyperLogLog(6).to_bytes())
        self.assertIn("HyperLogLog", str(caught.exception))

    def test_repr_reports_the_state_that_matters(self) -> None:
        text = repr(self.digest)
        self.assertIn("compression=40", text)
        self.assertIn("n=700", text)


if __name__ == "__main__":
    unittest.main()
