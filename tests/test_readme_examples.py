"""Every example in the documentation is executed here.

SPDX-License-Identifier: MIT OR Apache-2.0

A README that no longer runs is worse than no README: it is a bug report that
has already been ignored. Each ```python block in `README.md` is compiled and
executed; when it is followed by a ```text block, the transcript is compared
byte for byte. Numbers that can rot — the test count in the badges — are
asserted here too.
"""

from __future__ import annotations

import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import sift

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")

PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.S)
TRANSCRIPT = re.compile(r"```python\n(.*?)```\n\n```text\n(.*?)```\n", re.S)


def run(code: str) -> str:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exec(compile(code, "<documentation>", "exec"), {"sift": sift})
    return stdout.getvalue()


class TestReadmeRuns(unittest.TestCase):
    def test_there_are_python_examples_to_check(self) -> None:
        self.assertGreaterEqual(len(PYTHON_BLOCK.findall(README)), 2)

    def test_every_python_block_runs(self) -> None:
        for index, code in enumerate(PYTHON_BLOCK.findall(README)):
            with self.subTest(block=index):
                run(code)

    def test_transcripts_match_a_real_run(self) -> None:
        pairs = TRANSCRIPT.findall(README)
        self.assertGreaterEqual(len(pairs), 1, "no python/text transcript to check")
        for index, (code, expected) in enumerate(pairs):
            with self.subTest(block=index):
                self.assertEqual(run(code).strip(), expected.strip())


class TestReadmeNumbers(unittest.TestCase):
    """Documentation figures that a commit can silently invalidate."""

    def test_the_test_count_badge_matches_the_suite(self) -> None:
        match = re.search(r"badge/tests-(\d+)", README)
        self.assertIsNotNone(match, "no test-count badge in the README")
        count = unittest.TestLoader().discover(
            str(ROOT / "tests"), top_level_dir=str(ROOT)
        ).countTestCases()
        self.assertEqual(int(match.group(1)), count)  # type: ignore[union-attr]

    def test_the_headline_cardinality_numbers_are_consistent(self) -> None:
        """4 KiB is p=12, and 1.62% is what p=12 promises, not some other size."""
        self.assertIn("registers       4096 (4096 bytes)", README)
        self.assertIn("expected error  1.62%", README)
        self.assertAlmostEqual(100 * sift.HyperLogLog(12).standard_error, 1.62, places=2)
        self.assertEqual(sift.HyperLogLog(12).memory_usage(), 4096)

    def test_the_cited_measurements_name_their_run(self) -> None:
        self.assertRegex(README, r"seed\s*`?20251118`?")
        self.assertRegex(README, r"Python 3\.11\.9")

    def test_the_install_recipe_is_not_a_pypi_claim(self) -> None:
        """Not published on PyPI yet, so the README must not say `pip install sift`."""
        self.assertNotIn("$ pip install sift\n", README)
        self.assertIn("pip install git+https://github.com/fj-8/sift.git@v0.1.0", README)


class TestDocCrossReferences(unittest.TestCase):
    def test_linked_documents_exist(self) -> None:
        for target in set(re.findall(r"\]\(([^)]+\.md)\)", README)):
            if target.startswith("http"):
                continue
            with self.subTest(target=target):
                self.assertTrue((ROOT / target).is_file(), f"{target} does not exist")

    def test_the_docs_directory_is_referenced_from_the_readme(self) -> None:
        for name in ("ARCHITECTURE.md", "ALGORITHMS.md", "CLI.md", "PERFORMANCE.md"):
            with self.subTest(name=name):
                self.assertTrue((ROOT / "docs" / name).is_file())


if __name__ == "__main__":
    unittest.main()
