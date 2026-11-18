"""The package's own promises: exports, version, and the table in the docstring.

SPDX-License-Identifier: MIT OR Apache-2.0
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

import sift
from sift import cm, hll, moments, quantile, reservoir

ROOT = Path(__file__).resolve().parents[1]

MODULES = (hll, cm, quantile, moments, reservoir)


class TestExports(unittest.TestCase):
    def test_all_is_sorted_and_importable(self) -> None:
        self.assertEqual(sift.__all__, sorted(sift.__all__))
        for name in sift.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(sift, name), f"{name} is listed but not bound")

    def test_every_public_class_is_exported(self) -> None:
        """A structure you have to reach into a module for is an API accident."""
        for module in MODULES:
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    with self.subTest(name=f"{module.__name__}.{node.name}"):
                        self.assertIn(node.name, sift.__all__)
                        self.assertIs(getattr(sift, node.name), getattr(module, node.name))

    def test_no_module_imports_a_third_party_package(self) -> None:
        """The dependency list is empty, so every import must resolve locally."""
        for path in sorted((ROOT / "src" / "sift").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.extend(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names.append(node.module.split(".")[0])
            for name in names:
                if name in {"sift"} or name.startswith("sift."):
                    continue
                with self.subTest(module=path.name, imported=name):
                    self.assertIn(name, sys.stdlib_module_names, f"{path.name} imports {name}")

    def test_every_source_file_carries_the_spdx_line(self) -> None:
        for path in sorted((ROOT / "src" / "sift").glob("*.py")):
            with self.subTest(path=path.name):
                head = path.read_text(encoding="utf-8")[:600]
                self.assertIn("MIT OR Apache-2.0", head)


class TestVersion(unittest.TestCase):
    def test_version_tuple_matches_the_string(self) -> None:
        self.assertEqual(sift.version(), tuple(int(part) for part in sift.__version__.split(".")))

    def test_pyproject_agrees_with_the_package(self) -> None:
        import tomllib

        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["name"], "sift")
        self.assertEqual(data["project"]["version"], sift.__version__)
        self.assertEqual(data["project"]["dependencies"], [])
        self.assertEqual(data["project"]["requires-python"], ">=3.11")

    def test_console_script_reports_the_same_version(self) -> None:
        out = subprocess.run(
            [sys.executable, "-m", "sift", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), f"sift {sift.__version__}")


class TestDocstringTable(unittest.TestCase):
    """The table in ``sift.__doc__`` is the pitch; it has to stay true."""

    def table(self) -> str:
        return sift.__doc__ or ""

    def test_lists_every_structure(self) -> None:
        for name in ("HyperLogLog", "CountMinSketch", "TDigest", "Reservoir", "RunningStats"):
            with self.subTest(name=name):
                self.assertIn(f":class:`sift.{name}`", self.table())

    def test_the_hyperloglog_range_is_what_the_constructor_allows(self) -> None:
        self.assertIn("16 B .. 64 KiB", self.table())
        self.assertEqual(hll.HyperLogLog(hll.MIN_PRECISION).memory_usage(), 16)
        self.assertEqual(hll.HyperLogLog(hll.MAX_PRECISION).memory_usage(), 64 * 1024)

    def test_runningstats_is_described_as_five_scalars(self) -> None:
        self.assertIn("5 scalars", self.table())
        self.assertEqual(len(moments.RunningStats.__slots__), 5)

    def test_named_frame_and_hash_are_what_the_docs_say(self) -> None:
        self.assertIn("crc32", self.table().lower())
        self.assertIn("blake2b", self.table())


class TestModuleSurface(unittest.TestCase):
    def test_modules_are_importable_by_name(self) -> None:
        for name in ("sift.hll", "sift.cm", "sift.quantile", "sift.moments",
                     "sift.reservoir", "sift.cli", "sift._serial", "sift._hash"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(importlib.import_module(name), "__file__"))

    def test_frame_kinds_are_unique_and_named(self) -> None:
        from sift import _serial

        self.assertEqual(
            sorted(_serial._KIND_NAMES), sorted([_serial._KIND_HLL, _serial._KIND_CM, _serial._KIND_TDIGEST])
        )
        self.assertEqual(len(set(_serial._KIND_NAMES.values())), 3)


if __name__ == "__main__":
    unittest.main()
