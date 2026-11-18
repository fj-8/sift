"""End-to-end tests for the ``sift`` command.

SPDX-License-Identifier: MIT OR Apache-2.0
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from sift import cli

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")


def run(argv: list[str]) -> tuple[int, str, str]:
    """``cli.main`` with the streams captured, exit code and all."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse's own exits
            code = exc.code if isinstance(exc.code, int) else 0
    return code, out.getvalue(), err.getvalue()


class Fixture(unittest.TestCase):
    """Writes the input files once per test case."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="sift-cli-")
        self.addCleanup(self.tmp.cleanup)

    def write(self, name: str, text: str) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        return path


class TestInvocation(Fixture):
    def test_version_flag(self) -> None:
        code, out, err = run(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("sift 0.1.0", out)

    def test_help_lists_every_subcommand(self) -> None:
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0)
        for word in ("cardinality", "top", "quantiles", "stats", "sample"):
            self.assertIn(word, out)

    def test_no_command_is_an_argument_error(self) -> None:
        code, _, err = run([])
        self.assertEqual(code, 2)
        self.assertIn("required", err)

    def test_unknown_subcommand(self) -> None:
        code, _, err = run(["bogus"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_module_entry_point_works(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "sift", "--version"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=dict(os.environ, PYTHONPATH=SRC),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "sift 0.1.0")

    def test_missing_file_is_reported_not_raised(self) -> None:
        code, _, err = run(["stats", "does-not-exist.txt"])
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("sift: "), err)
        self.assertNotIn("Traceback", err)


class TestCardinality(Fixture):
    def test_reports_the_four_lines(self) -> None:
        path = self.write("keys.txt", "\n".join(["a", "b", "a", "c", "b", "a"]))
        code, out, err = run(["cardinality", path])
        self.assertEqual(code, 0, err)
        self.assertIn("rows            6", out)
        self.assertIn("distinct        ", out)
        self.assertIn("registers       4096 (4096 bytes)", out)
        self.assertIn("expected error  1.62%", out)
        self.assertIn("duplication     50.00% of rows", out)

    def test_estimate_is_inside_four_standard_errors(self) -> None:
        rng = random.Random(5)
        keys = [f"user-{rng.randrange(50_000)}" for _ in range(100_000)]
        exact = len(set(keys))
        path = self.write("keys.txt", "\n".join(keys))
        _, out, _ = run(["cardinality", "--precision", "12", path])
        estimate = int(next(
            line.split()[1] for line in out.splitlines() if line.startswith("distinct")
        ))
        self.assertLess(abs(estimate - exact), 4 * 0.0163 * exact)

    def test_column_and_separator(self) -> None:
        path = self.write(
            "rows.csv", "\n".join(["1,x,shared", "2,y,shared", "3,z,unique"])
        )
        _, whole, _ = run(["cardinality", "--column", "3", "--separator", ",", path])
        estimate = int(next(
            line.split()[1] for line in whole.splitlines() if line.startswith("distinct")
        ))
        self.assertAlmostEqual(estimate, 2, delta=2)

    def test_missing_column_exits_two(self) -> None:
        path = self.write("rows.txt", "one two\nthree\nfour five\n")
        code, _, err = run(["cardinality", "--column", "4", path])
        self.assertEqual(code, 2)
        self.assertIn("expected column 4", err)
        self.assertIn("rows.txt:1:", err)

    def test_skip_bad_tolerates_short_rows(self) -> None:
        path = self.write("rows.txt", "one two\nthree\nfour five\n")
        code, out, err = run(["cardinality", "--column", "2", "--skip-bad", path])
        self.assertEqual(code, 0, err)
        self.assertIn("rows            2", out)

    def test_precision_is_validated(self) -> None:
        path = self.write("keys.txt", "a\nb")
        for bad in ("2", "17", "0"):
            code, _, err = run(["cardinality", "--precision", bad, path])
            self.assertEqual(code, 2)
            self.assertIn("precision", err)

    def test_empty_input(self) -> None:
        path = self.write("empty.txt", "")
        code, out, err = run(["cardinality", path])
        self.assertEqual(code, 0, err)
        self.assertIn("rows            0", out)
        self.assertIn("distinct        0", out)


class TestTop(Fixture):
    def test_counts_are_exact_and_the_sketch_is_never_low(self) -> None:
        lines = []
        for index, (key, times) in enumerate([("hot", 500), ("warm", 120), ("cold", 9)]):
            lines += [key] * times
            lines += [f"filler-{index}"] * 40
        path = self.write("events.txt", "\n".join(lines))
        code, out, err = run(["top", "--items", "3", path])
        self.assertEqual(code, 0, err)
        self.assertIn("count      sketch    error  item", out)
        rows = [line for line in out.splitlines() if line[:10].strip().isdigit()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].split()[-1] == "hot")
        for row in rows:
            count, sketch = int(row.split()[0]), int(row.split()[1])
            self.assertLessEqual(count, sketch)
        self.assertIn("distinct", out)

    def test_accuracy_flags_resize_the_sketch(self) -> None:
        path = self.write("events.txt", "a\nb\na")
        _, tight, _ = run(["top", "--eps", "0.01", "--delta", "0.001", path])
        _, loose, _ = run(["top", "--eps", "0.5", "--delta", "0.5", path])
        bytes_tight = int(tight.split("sketch holds ")[1].split(" bytes")[0])
        bytes_loose = int(loose.split("sketch holds ")[1].split(" bytes")[0])
        self.assertGreater(bytes_tight, bytes_loose)

    def test_bad_eps_exits_two(self) -> None:
        path = self.write("events.txt", "a\nb")
        code, _, err = run(["top", "--eps", "1.5", path])
        self.assertEqual(code, 2)
        self.assertIn("eps", err)

    def test_empty_input_is_a_blank_table_not_a_crash(self) -> None:
        path = self.write("events.txt", "")
        code, out, err = run(["top", path])
        self.assertEqual(code, 0, err)
        self.assertIn("0 rows, 0 distinct", out)


class TestQuantiles(Fixture):
    def setUp(self) -> None:
        super().setUp()
        rng = random.Random(11)
        self.values = [rng.gauss(200.0, 25.0) for _ in range(20_000)]
        self.path = self.write("lat.txt", "\n".join(f"{value:.4f}" for value in self.values))

    def test_digest_and_exact_columns_agree(self) -> None:
        code, out, err = run(["quantiles", "--qs", "0.5,0.9,0.99", "--exact", self.path])
        self.assertEqual(code, 0, err)
        self.assertIn("q          digest  rank error", out)
        with open(self.path, encoding="utf-8") as handle:
            exact = sorted(float(line) for line in handle)
        for q, reported in zip([0.5, 0.9, 0.99], out.splitlines()[1:4]):
            value = float(reported.split()[1])
            index = min(len(exact) - 1, int(q * (len(exact) - 1) + 0.5))
            self.assertLess(abs(value - exact[index]), 2.0)
        self.assertIn("held in RAM", out)

    def test_streaming_mode_reports_centroids(self) -> None:
        _, out, _ = run(["quantiles", self.path])
        self.assertIn("20000 values", out)
        self.assertIn("centroids", out)
        self.assertNotIn("rank error\n", out.splitlines()[0] + "\n")

    def test_compression_trades_size_for_accuracy(self) -> None:
        _, coarse, _ = run(["quantiles", "--compression", "5", self.path])
        _, fine, _ = run(["quantiles", "--compression", "500", "--exact", self.path])
        self.assertIn("centroids", coarse)
        self.assertIn("rank error", fine.splitlines()[0])

    def test_empty_qs_entries_are_ignored(self) -> None:
        code, out, err = run(["quantiles", "--qs", "0.5,,0.9,", self.path])
        self.assertEqual(code, 0, err)
        rows = [line.split()[0] for line in out.splitlines()[1:3]]
        self.assertEqual(rows, ["0.500", "0.900"])

    def test_out_of_range_qs_exits_two(self) -> None:
        code, _, err = run(["quantiles", "--qs", "5", self.path])
        self.assertEqual(code, 2)
        self.assertIn("in [0, 1]", err)

    def test_non_numeric_line_exits_two(self) -> None:
        path = self.write("mixed.txt", "1.0\nnope\n2.0")
        code, _, err = run(["quantiles", path])
        self.assertEqual(code, 2)
        self.assertIn("'nope' is not a number", err)
        self.assertIn("mixed.txt:2", err)

    def test_skip_bad_silences_it(self) -> None:
        path = self.write("mixed.txt", "1.0\nnope\n2.0")
        code, out, err = run(["quantiles", "--skip-bad", path])
        self.assertEqual(code, 0, err)
        self.assertIn("2 values", out)

    def test_bad_compression_exits_two(self) -> None:
        code, _, err = run(["quantiles", "--compression", "0.5", self.path])
        self.assertEqual(code, 2)
        self.assertIn("compression", err)


class TestStats(Fixture):
    def test_matches_the_reference_moments(self) -> None:
        path = self.write("nums.txt", "\n".join(["1", "2", "3", "4", "10"]))
        code, out, err = run(["stats", path])
        self.assertEqual(code, 0, err)
        self.assertIn("count   5", out)
        self.assertIn("mean    4", out)
        self.assertIn("stdev   3.53553", out)
        self.assertIn("min     1", out)
        self.assertIn("max     10", out)

    def test_single_row_has_no_sample_stdev(self) -> None:
        path = self.write("one.txt", "7")
        code, out, err = run(["stats", path])
        self.assertEqual(code, 0, err)
        self.assertIn("stdev   (needs 2+ rows)", out)

    def test_empty_input_exits_two(self) -> None:
        path = self.write("none.txt", "")
        code, _, err = run(["stats", path])
        self.assertEqual(code, 2)
        self.assertIn("no numeric rows", err)

    def test_columns_and_mixed_garbage(self) -> None:
        path = self.write("rows.txt", "a 1.5 x\nb 2.5 x\nc oops x\n")
        code, _, err = run(["stats", "--column", "2", path])
        self.assertEqual(code, 2)
        self.assertIn("'oops'", err)
        code, out, _ = run(["stats", "--column", "2", "--skip-bad", path])
        self.assertEqual(code, 0)
        self.assertIn("count   2", out)


class TestSample(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.write("lines.txt", "\n".join(f"line-{index}" for index in range(1000)))

    def test_prints_k_lines_and_the_ratio_to_stderr(self) -> None:
        code, out, err = run(["sample", "--seed", "7", "20", self.path])
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(len(lines), 20)
        self.assertEqual(len(set(lines)), 20)
        self.assertIn("# kept 20 of 1000", err)

    def test_seed_makes_it_reproducible(self) -> None:
        _, first, _ = run(["sample", "--seed", "99", "15", self.path])
        _, second, _ = run(["sample", "--seed", "99", "15", self.path])
        self.assertEqual(first, second)
        _, other, _ = run(["sample", "--seed", "100", "15", self.path])
        self.assertNotEqual(first, other)

    def test_an_option_between_k_and_the_file_is_an_argument_error(self) -> None:
        # Python 3.11 argparse cannot resume a variadic positional after an
        # option, so this ordering is rejected rather than silently misread.
        code, _, err = run(["sample", "20", "--seed", "7", self.path])
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments", err)

    def test_without_a_seed_it_still_samples(self) -> None:
        code, out, err = run(["sample", "5", self.path])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.splitlines()), 5)
        self.assertIn("# kept 5 of 1000", err)

    def test_k_below_one_exits_two(self) -> None:
        code, _, err = run(["sample", "0", self.path])
        self.assertEqual(code, 2)
        self.assertIn("reservoir of size 0", err)

    def test_k_larger_than_the_file(self) -> None:
        code, out, err = run(["sample", "5000", self.path])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.splitlines()), 1000)
        self.assertIn("# kept 1000 of 1000", err)


class TestStdinAndMultipleFiles(Fixture):
    def test_multiple_files_concatenate(self) -> None:
        first = self.write("a.txt", "1\n2\n3")
        second = self.write("b.txt", "4\n5")
        code, out, err = run(["stats", first, second])
        self.assertEqual(code, 0, err)
        self.assertIn("count   5", out)
        self.assertIn("max     5", out)

    def test_stdin_when_no_file_is_given(self) -> None:
        buffer = io.StringIO("alpha\nbeta\nalpha\ngamma\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch("sys.stdin", buffer):
                code = cli.main(["cardinality"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("rows            4", out.getvalue())

    def test_stdin_is_not_closed_by_the_reader(self) -> None:
        """``iter_values`` must not close a stream it does not own."""
        state = {"closed": False}

        class Probe(io.StringIO):
            def close(self) -> None:  # type: ignore[override]
                state["closed"] = True
                super().close()

        probe = Probe("one" + chr(10) + "two" + chr(10))
        namespace = argparse.Namespace(column=None, separator=None, skip_bad=False, files=[])
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(io.StringIO()):
                with mock.patch("sys.stdin", probe):
                    items = list(cli.iter_values(namespace, numeric=False))
        self.assertEqual(items, ["one", "two"])
        self.assertFalse(state["closed"])
        probe.close()
        self.assertTrue(state["closed"])


class TestBrokenPipe(Fixture):
    def test_a_closed_pipe_exits_zero(self) -> None:
        path = self.write("nums.txt", "1\n2\n3")

        class Closed:
            def write(self, _text: str) -> int:
                raise BrokenPipeError

            def flush(self) -> None:
                raise BrokenPipeError

        with contextlib.redirect_stdout(Closed()):
            self.assertEqual(cli.main(["sample", "2", path]), 0)



if __name__ == "__main__":
    unittest.main()
