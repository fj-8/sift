"""Reservoir sampling tests. SPDX-License-Identifier: MIT OR Apache-2.0"""

from __future__ import annotations

import math
import random
import statistics
import unittest

from sift import Reservoir, WeightedReservoir, reservoir_sample


class TestConstruction(unittest.TestCase):
    def test_k_must_be_a_positive_int(self) -> None:
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                Reservoir(bad)
        with self.assertRaises(ValueError):
            WeightedReservoir(0)
        for bad in (10.0, "10", True, None):
            with self.assertRaises(TypeError):
                Reservoir(bad)
            with self.assertRaises(TypeError):
                WeightedReservoir(bad)

    def test_a_seeded_reservoir_is_reproducible(self) -> None:
        data = [index for index in range(5000)]
        first = Reservoir(25, rng=random.Random(4))
        second = Reservoir(25, rng=random.Random(4))
        for value in data:
            first.add(value)
            second.add(value)
        self.assertEqual(first.sample(), second.sample())

    def test_different_seeds_give_different_samples(self) -> None:
        data = list(range(5000))
        samples = set()
        for seed in range(8):
            pool = Reservoir(25, rng=random.Random(seed))
            pool.add_all(data)
            samples.add(tuple(pool.sample()))
        self.assertEqual(len(samples), 8)


class TestShortStreams(unittest.TestCase):
    def test_holds_everything_when_n_is_below_k(self) -> None:
        pool = Reservoir(10, rng=random.Random(1))
        pool.add_all([1, 2, 3])
        self.assertEqual(pool.sample(), [1, 2, 3])
        self.assertEqual(pool.seen, 3)
        self.assertEqual(len(pool), 3)

    def test_exactly_k_keeps_the_whole_stream(self) -> None:
        pool = Reservoir(4, rng=random.Random(1))
        pool.add_all("abcd")
        self.assertEqual(sorted(pool.sample()), ["a", "b", "c", "d"])

    def test_repr_reports_size_and_occupancy(self) -> None:
        pool = Reservoir(3, rng=random.Random(1))
        pool.add_all([1, 2, 3, 4])
        self.assertEqual(repr(pool), "Reservoir(k=3, seen=4, held=3)")
        weighted = WeightedReservoir(2, rng=random.Random(1))
        weighted.add("x", 1.0)
        self.assertEqual(repr(weighted), "WeightedReservoir(k=2, seen=1, held=1)")


class TestCap(unittest.TestCase):
    def test_never_holds_more_than_k(self) -> None:
        pool = Reservoir(7, rng=random.Random(2))
        for index in range(100_000):
            pool.add(index)
            self.assertLessEqual(len(pool), 7)
        self.assertEqual(len(pool), 7)
        self.assertEqual(pool.seen, 100_000)

    def test_held_items_always_come_from_the_stream(self) -> None:
        pool = Reservoir(5, rng=random.Random(3))
        seen = set()
        for index in range(2000):
            pool.add(index)
            seen.add(index)
        self.assertTrue(set(pool.sample()).issubset(seen))

    def test_sample_is_a_copy(self) -> None:
        pool = Reservoir(3, rng=random.Random(3))
        pool.add_all([1, 2, 3])
        snapshot = pool.sample()
        snapshot.append(99)
        self.assertEqual(len(pool.sample()), 3)

    def test_items_are_stored_untouched_and_unhashable_is_fine(self) -> None:
        sentinel = ["a", {"b": 1}]
        pool = Reservoir(2, rng=random.Random(3))
        pool.add(sentinel)
        self.assertIs(pool.sample()[0], sentinel)


class TestUniformity(unittest.TestCase):
    def test_every_item_lands_in_the_sample_at_k_over_n(self) -> None:
        n, k, trials = 200, 20, 1500
        expected = k / n
        sd = math.sqrt(expected * (1 - expected) / trials)
        hits = [0] * n
        rng = random.Random(11)
        for _ in range(trials):
            pool = Reservoir(k, rng=rng)
            pool.add_all(range(n))
            for item in pool.sample():
                hits[item] += 1
        worst = max(abs(count / trials - expected) for count in hits)
        self.assertLess(worst, 5.0 * sd, f"worst inclusion rate off by {worst:.5f}, sd {sd:.5f}")
        self.assertTrue(all(count > 0 for count in hits))
        self.assertEqual(sum(hits), trials * k)

    def test_the_last_item_is_included_with_probability_k_over_n(self) -> None:
        n, k, trials = 500, 25, 1500
        expected = k / n
        sd = math.sqrt(expected * (1 - expected) / trials)
        rng = random.Random(12)
        present = 0
        for _ in range(trials):
            pool = Reservoir(k, rng=rng)
            pool.add_all(range(n))
            present += n - 1 in pool.sample()
        self.assertLess(abs(present / trials - expected), 5.0 * sd)

    def test_the_first_items_are_not_sticky(self) -> None:
        # Items that entered the pool before it filled up have to be displaced at
        # the same rate as any other, or the head of the stream would be favoured.
        n, k, trials = 400, 20, 1200
        expected = k / n
        sd = math.sqrt(expected * (1 - expected) / trials)
        rng = random.Random(13)
        hits = [0] * n
        for _ in range(trials):
            pool = Reservoir(k, rng=rng)
            pool.add_all(range(n))
            for item in pool.sample():
                hits[item] += 1
        head = statistics.fmean(hits[:k]) / trials
        tail = statistics.fmean(hits[n - k :]) / trials
        self.assertLess(abs(head - expected), 4.0 * sd)
        self.assertLess(abs(tail - expected), 4.0 * sd)


class TestWeighted(unittest.TestCase):
    def test_weight_validation(self) -> None:
        pool = WeightedReservoir(2, rng=random.Random(1))
        for bad in (0, 0.0, -1, -0.5, float("nan")):
            with self.assertRaises(ValueError):
                pool.add("x", bad)
        for bad in ("1", True, None, [1]):
            with self.assertRaises(TypeError):
                pool.add("x", bad)
        self.assertEqual(len(pool), 0)

    def test_infinite_weight_always_wins(self) -> None:
        # -log(u) / inf is 0, the smallest possible key, so the item is certain.
        pool = WeightedReservoir(2, rng=random.Random(21))
        pool.add("anchor", float("inf"))
        for index in range(5000):
            pool.add(index, 1.0)
        self.assertIn("anchor", pool.sample())

    def test_selection_frequency_tracks_weight_for_k_equal_one(self) -> None:
        weights = [1.0, 2.0, 3.0, 4.0, 10.0]
        total = sum(weights)
        trials = 6000
        rng = random.Random(14)
        counts = [0] * len(weights)
        for _ in range(trials):
            pool = WeightedReservoir(1, rng=rng)
            for index, weight in enumerate(weights):
                pool.add(index, weight)
            counts[pool.sample()[0]] += 1
        for index, weight in enumerate(weights):
            expected = weight / total
            sd = math.sqrt(expected * (1 - expected) / trials)
            self.assertLess(
                abs(counts[index] / trials - expected),
                5.0 * sd,
                f"item {index} rate {counts[index] / trials:.4f}, expected {expected:.4f}",
            )

    def test_heavy_items_displace_light_ones(self) -> None:
        rng = random.Random(15)
        pool = WeightedReservoir(3, rng=rng)
        for index in range(500):
            pool.add(("light", index), 1.0)
        for index in range(3):
            pool.add(("heavy", index), 1e6)
        sample = pool.sample()
        self.assertEqual(len(sample), 3)
        self.assertTrue(all(kind == "heavy" for kind, _ in sample), sample)

    def test_never_holds_more_than_k(self) -> None:
        rng = random.Random(16)
        pool = WeightedReservoir(5, rng=rng)
        for index in range(20_000):
            pool.add(index, 1.0 + index % 7)
            self.assertLessEqual(len(pool), 5)
        self.assertEqual(len(pool), 5)

    def test_sample_order_is_stable_and_reproducible(self) -> None:
        def run() -> list:
            pool = WeightedReservoir(4, rng=random.Random(17))
            for index in range(400):
                pool.add(index, float(index + 1))
            return pool.sample()

        first, second = run(), run()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertEqual(len(set(first)), 4)

    def test_short_stream_keeps_everything(self) -> None:
        pool = WeightedReservoir(10, rng=random.Random(1))
        pool.add("a", 1.0)
        pool.add("b", 3.0)
        self.assertEqual(pool.sample(), ["a", "b"])
        self.assertEqual(pool.seen, 2)


class TestOneShot(unittest.TestCase):
    def test_reservoir_sample_matches_the_class(self) -> None:
        data = list(range(4000))
        direct = reservoir_sample(data, 30, rng=random.Random(77))
        pool = Reservoir(30, rng=random.Random(77))
        pool.add_all(data)
        self.assertEqual(direct, pool.sample())
        self.assertEqual(len(direct), 30)

    def test_accepts_a_generator_without_buffering_it(self) -> None:
        pulled = 0

        def stream():
            nonlocal pulled
            for index in range(100_000):
                pulled += 1
                yield index

        sample = reservoir_sample(stream(), 4, rng=random.Random(1))
        self.assertEqual(len(sample), 4)
        self.assertEqual(pulled, 100_000)

    def test_k_larger_than_the_stream_returns_it_all(self) -> None:
        self.assertEqual(reservoir_sample([1, 2], 9, rng=random.Random(1)), [1, 2])

    def test_empty_stream(self) -> None:
        self.assertEqual(reservoir_sample([], 5, rng=random.Random(1)), [])

    def test_rejects_k_below_one(self) -> None:
        with self.assertRaises(ValueError):
            reservoir_sample([1, 2, 3], 0)


if __name__ == "__main__":
    unittest.main()
