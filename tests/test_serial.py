"""Byte-level tests for the shared frame and the deterministic hash.

SPDX-License-Identifier: MIT OR Apache-2.0
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest

from sift import CountMinSketch, HyperLogLog, TDigest
from sift import _hash, _serial


class TestEncoding(unittest.TestCase):
    def test_each_type_gets_its_own_tag(self) -> None:
        self.assertEqual(_hash.encode(None), b"N")
        self.assertEqual(_hash.encode(True), b"b\x01")
        self.assertEqual(_hash.encode(False), b"b\x00")
        self.assertEqual(_hash.encode(5), b"i\x05")
        self.assertEqual(_hash.encode("5"), b"u5")
        self.assertEqual(_hash.encode(b"5"), b"s5")

    def test_integers_encode_as_minimal_signed_little_endian(self) -> None:
        vectors = {
            0: b"i\x00",
            -1: b"i\xff",
            127: b"i\x7f",
            128: b"i\x80\x00",
            -128: b"i\x80\xff",
            -129: b"i\x7f\xff",
            255: b"i\xff\x00",
            65535: b"i\xff\xff\x00",
            -(2**40): b"i\x00\x00\x00\x00\x00\xff",
            2**63: b"i\x00\x00\x00\x00\x00\x00\x00\x80\x00",  # nine bytes
        }
        for value, expected in vectors.items():
            self.assertEqual(_hash.encode(value), expected, value)

    def test_bytes_like_share_one_tag_and_strings_another(self) -> None:
        self.assertEqual(_hash.encode(b"abc"), _hash.encode(bytearray(b"abc")))
        self.assertEqual(_hash.encode(b"abc"), _hash.encode(memoryview(b"abc")))
        self.assertNotEqual(_hash.encode(b"abc"), _hash.encode("abc"))

    def test_the_empty_key_is_a_key(self) -> None:
        self.assertEqual(_hash.encode(""), b"u")
        self.assertEqual(_hash.encode(b""), b"s")
        self.assertNotEqual(_hash.digest(""), _hash.digest(b""))

    def test_rejects_types_with_no_stable_bytes_form(self) -> None:
        for bad in (1.5, (1, 2), frozenset({1}), object(), {"a": 1}):
            with self.assertRaises(TypeError):
                _hash.encode(bad)

    def test_bool_and_int_one_are_distinct_items(self) -> None:
        self.assertNotEqual(_hash.digest(True), _hash.digest(1))
        self.assertNotEqual(_hash.digest(False), _hash.digest(0))

    def test_non_ascii_strings_encode_as_utf8(self) -> None:
        self.assertEqual(_hash.encode("привет"), b"u" + "привет".encode("utf-8"))
        self.assertEqual(len(_hash.digest("привет")), _hash.DIGEST_SIZE)

    def test_a_sketch_refuses_a_key_it_cannot_encode(self) -> None:
        sketch = HyperLogLog(6)
        with self.assertRaises(TypeError):
            sketch.add(1.5)
        with self.assertRaises(TypeError):
            CountMinSketch(64, 2).add((1, 2))


class TestDigestVector(unittest.TestCase):
    #: Generated once from this construction; if it changes, frames written by
    #: an older sift describe different items to a newer one.
    VECTOR_5 = "9625eac65d7ca9478ec1fd871f61e776"
    VECTOR_USER_42 = "3de81ca50d6d46ebc92f24a939fdfe88"

    def test_pinned_vectors(self) -> None:
        self.assertEqual(_hash.digest(5).hex(), self.VECTOR_5)
        self.assertEqual(_hash.digest("user-42").hex(), self.VECTOR_USER_42)

    def test_digest_is_blake2b_with_the_project_key(self) -> None:
        expected = hashlib.blake2b(
            _hash.encode("user-42"), digest_size=_hash.DIGEST_SIZE, key=_hash.KEY
        ).digest()
        self.assertEqual(_hash.digest("user-42"), expected)

    def test_the_two_halves_are_the_digest_split(self) -> None:
        raw = _hash.digest("row-key")
        k1, k2 = _hash.halves("row-key")
        self.assertEqual(k1, int.from_bytes(raw[:8], "big"))
        self.assertEqual(k2, int.from_bytes(raw[8:], "big"))
        self.assertLess(k1, 2**64)
        self.assertLess(k2, 2**64)

    def test_the_low_bits_behave_like_a_fair_coin(self) -> None:
        # HLL reads the run of zeros in the low 56 bits, so the mean rank over a
        # large key set has to sit at 2.0; a biased hash shows up here first.
        precision, keys = 8, 30_000
        low_bits = 64 - precision
        total = 0
        for index in range(keys):
            k1, _ = _hash.halves(index)
            tail = k1 & ((1 << low_bits) - 1)
            total += _hash.leading_zeros(tail, low_bits) + 1
        self.assertAlmostEqual(total / keys, 2.0, delta=0.05)


class TestDerivation(unittest.TestCase):
    def test_positions_stay_in_range(self) -> None:
        for index in range(200):
            k1, k2 = _hash.halves(index)
            for modulus in (1, 2, 8, 1000, 2048, 65536):
                for row in range(8):
                    position = _hash.derive(k1, k2, row, modulus)
                    self.assertGreaterEqual(position, 0)
                    self.assertLess(position, modulus)

    def test_rows_land_on_different_cells(self) -> None:
        spread = 0
        for index in range(50):
            k1, k2 = _hash.halves(index)
            positions = [_hash.derive(k1, k2, row, 2048) for row in range(8)]
            if len(set(positions)) >= 6:
                spread += 1
        self.assertGreater(spread, 40)

    def test_derivation_is_a_pure_function_of_the_two_halves(self) -> None:
        first = _hash.halves("same")
        second = _hash.halves("same")
        self.assertEqual(first, second)
        for row in range(4):
            self.assertEqual(
                _hash.derive(*first, row=row, modulus=1024),
                _hash.derive(*second, row=row, modulus=1024),
            )


class TestBitHelpers(unittest.TestCase):
    def test_leading_zeros(self) -> None:
        self.assertEqual(_hash.leading_zeros(0, 8), 8)
        self.assertEqual(_hash.leading_zeros(1, 8), 7)
        self.assertEqual(_hash.leading_zeros(0x80, 8), 0)
        self.assertEqual(_hash.leading_zeros(0xFF, 52), 44)

    def test_mask64_wraps(self) -> None:
        self.assertEqual(_hash.mask64(-1), 2**64 - 1)
        self.assertEqual(_hash.mask64(2**64), 0)


class TestFrameFormat(unittest.TestCase):
    def setUp(self) -> None:
        self.hll = HyperLogLog(8)
        self.hll.add_all(range(1000))
        self.cm = CountMinSketch(64, 4)
        self.cm.add_all(["a", "b", "c"])
        self.digest = TDigest(20.0)
        self.digest.add_all([1.0, 2.0, 3.0, 4.0] * 30)

    def test_layout_is_header_config_length_payload_crc(self) -> None:
        frame = self.hll.to_bytes()
        self.assertEqual(frame[:4], _serial.MAGIC)
        self.assertEqual(frame[4], _serial._KIND_HLL)
        self.assertEqual(frame[5], _serial.VERSION)
        self.assertEqual(frame[6], 1)  # one config byte: the precision
        self.assertEqual(frame[7], 8)  # ... which is p=8
        self.assertEqual(len(frame), 7 + 1 + 4 + 256 + 4)
        self.assertEqual(frame[12:268], self.hll.registers())

    def test_framing_overhead_is_16_31_and_23_bytes(self) -> None:
        # Measured, not asserted from the header struct: the config block is
        # what makes Count-Min the most expensive of the three to persist.
        self.assertEqual(len(HyperLogLog(10).to_bytes()) - 2**10, 16)
        self.assertEqual(len(CountMinSketch(256, 5).to_bytes()) - 8 * 256 * 5, 31)
        self.assertEqual(len(TDigest(100.0).to_bytes()), 39)  # empty: 16-byte payload
        one_point = TDigest(100.0)
        one_point.add(5.0)
        self.assertEqual(len(one_point.to_bytes()), 39 + 16)

    def test_all_three_kinds_round_trip(self) -> None:
        self.assertEqual(HyperLogLog.from_bytes(self.hll.to_bytes()), self.hll)
        self.assertEqual(CountMinSketch.from_bytes(self.cm.to_bytes()), self.cm)
        self.assertEqual(TDigest.from_bytes(self.digest.to_bytes()), self.digest)

    def test_round_trip_preserves_the_answers(self) -> None:
        self.assertEqual(
            HyperLogLog.from_bytes(self.hll.to_bytes()).count(), self.hll.count()
        )
        self.assertEqual(CountMinSketch.from_bytes(self.cm.to_bytes()).query("a"), 1)
        reloaded = TDigest.from_bytes(self.digest.to_bytes())
        self.assertEqual(reloaded.quantile(0.5), self.digest.quantile(0.5))
        self.assertEqual(reloaded.compression, 20.0)

    def test_frame_names_the_kind_it_holds(self) -> None:
        frames = {
            "HyperLogLog": self.hll.to_bytes(),
            "CountMinSketch": self.cm.to_bytes(),
            "TDigest": self.digest.to_bytes(),
        }
        readers = {
            "HyperLogLog": HyperLogLog.from_bytes,
            "CountMinSketch": CountMinSketch.from_bytes,
            "TDigest": TDigest.from_bytes,
        }
        checked = 0
        for got_name, frame in frames.items():
            for want_name, reader in readers.items():
                if want_name == got_name:
                    continue
                checked += 1
                with self.assertRaises(_serial.FrameError) as caught:
                    reader(frame)
                message = str(caught.exception)
                self.assertIn(got_name, message)
                self.assertIn(want_name, message)
        self.assertEqual(checked, 6)

    def test_bad_magic_is_reported_as_magic(self) -> None:
        frame = bytearray(self.hll.to_bytes())
        frame[0] = ord("X")
        with self.assertRaises(_serial.FrameError) as caught:
            HyperLogLog.from_bytes(bytes(frame))
        self.assertIn("magic", str(caught.exception))

    def test_version_byte_is_reported_as_version(self) -> None:
        frame = bytearray(self.hll.to_bytes())
        frame[5] = 3
        with self.assertRaises(_serial.FrameError) as caught:
            HyperLogLog.from_bytes(bytes(frame))
        self.assertIn("version 3", str(caught.exception))

    def test_declared_length_must_match_the_buffer(self) -> None:
        frame = bytearray(self.hll.to_bytes())
        frame[8:8] = b"\x00"  # one byte inserted before the payload
        with self.assertRaises(_serial.FrameError) as caught:
            HyperLogLog.from_bytes(bytes(frame))
        self.assertIn("declared payload", str(caught.exception))

    def test_trailing_bytes_are_reported(self) -> None:
        self.assertRaises(
            _serial.FrameError, TDigest.from_bytes, self.digest.to_bytes() + b"\x00"
        )

    def test_edits_inside_the_payload_fail_the_checksum(self) -> None:
        frame = bytearray(self.hll.to_bytes())
        frame[100] ^= 0xFF
        with self.assertRaises(_serial.FrameError) as caught:
            HyperLogLog.from_bytes(bytes(frame))
        self.assertIn("checksum", str(caught.exception))

    def test_truncation_fails_too(self) -> None:
        frame = self.cm.to_bytes()
        for cut in (1, 20, len(frame) - 1):
            with self.assertRaises(_serial.FrameError):
                CountMinSketch.from_bytes(frame[:cut])

    def test_short_buffers_are_not_parsed(self) -> None:
        for too_short in (b"", b"SFT", b"SFT1", b"SFT1\x01\x01\x01\x00", b"nope" * 3):
            with self.assertRaises(_serial.FrameError):
                HyperLogLog.from_bytes(too_short)

    def test_accepts_bytearray_and_memoryview(self) -> None:
        frame = self.hll.to_bytes()
        self.assertEqual(HyperLogLog.from_bytes(bytearray(frame)), self.hll)
        self.assertEqual(HyperLogLog.from_bytes(memoryview(frame)), self.hll)

    def test_oversized_config_is_refused_at_encode_time(self) -> None:
        with self.assertRaises(ValueError):
            _serial.encode(_serial._KIND_HLL, b"\x08" * 256, b"")

    def test_frame_error_is_a_value_error(self) -> None:
        # The CLI relies on this: one except clause covers sketch misuse and
        # unreadable buffers alike.
        self.assertTrue(issubclass(_serial.FrameError, ValueError))
        with self.assertRaises(ValueError):
            HyperLogLog.from_bytes(b"nope" * 8)


class TestCrossProcessDeterminism(unittest.TestCase):
    SCRIPT = """
import json, sys
sys.path.insert(0, "src")
import sift
keys = ["a", "b", "c", 1, 2, True, None, "aa", "ab"]
hll = sift.HyperLogLog(10)
cm = sift.CountMinSketch(256, 4)
digest = sift.TDigest(50.0)
for index, key in enumerate(keys):
    hll.add(key)
    cm.add(key, index + 1)
    digest.add(float(index))
print(json.dumps({
    "hll": hll.count(),
    "hll_frame": hll.to_bytes().hex(),
    "cm": [cm.query(key) for key in keys],
    "cm_frame": cm.to_bytes().hex(),
    "digest_frame": digest.to_bytes().hex(),
    "q50": digest.quantile(0.5),
    "vector": sift._hash.digest("user-42").hex(),
}))
"""

    def _run(self) -> dict:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        result = subprocess.run(
            [sys.executable, "-c", self.SCRIPT],
            capture_output=True,
            text=True,
            cwd=root,
            check=True,
        )
        self.assertEqual(result.stderr, "", result.stderr)
        return json.loads(result.stdout.strip())

    def test_two_fresh_interpreters_agree_byte_for_byte(self) -> None:
        first, second = self._run(), self._run()
        self.assertEqual(first, second)
        self.assertGreater(len(first["hll_frame"]), 2048)

    def test_a_subprocess_matches_this_one(self) -> None:
        from_subprocess = self._run()
        keys = ["a", "b", "c", 1, 2, True, None, "aa", "ab"]
        sketch = HyperLogLog(10)
        sketch.add_all(keys)
        self.assertEqual(sketch.to_bytes().hex(), from_subprocess["hll_frame"])
        self.assertEqual(sketch.count(), from_subprocess["hll"])

    def test_the_blake2b_construction_survives_a_new_process(self) -> None:
        self.assertEqual(
            self._run()["vector"], hashlib.blake2b(b"uuser-42", digest_size=16, key=b"sift.v1").hexdigest()
        )


if __name__ == "__main__":
    unittest.main()
