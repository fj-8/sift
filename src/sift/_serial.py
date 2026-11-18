"""The shared byte frame for serialisable sketches. SPDX-License-Identifier: MIT OR Apache-2.0.

Every persisted sketch uses one layout so a caller can tell "wrong kind of
buffer" from "corrupt buffer" from "written by a different sift version"
instead of getting a confusing struct error halfway through a payload.

``magic | kind | version | config_len | config | payload_len | payload | crc32``

All integers are big-endian. ``crc32`` covers every preceding byte, so a
one-character edit anywhere in the frame is caught before it is parsed.
"""

from __future__ import annotations

import struct
import zlib

MAGIC = b"SFT1"
VERSION = 1

_KIND_HLL = 1
_KIND_CM = 2
_KIND_TDIGEST = 3

_KIND_NAMES = {
    _KIND_HLL: "HyperLogLog",
    _KIND_CM: "CountMinSketch",
    _KIND_TDIGEST: "TDigest",
}
_HEADER = struct.Struct(">4sBBB")
_TRAILER = struct.Struct(">I")
_TRAILER_FIELD = struct.calcsize(">I")


class FrameError(ValueError):
    """A buffer is not a frame, is not the expected kind, or failed its checksum."""


def encode(kind: int, config: bytes, payload: bytes) -> bytes:
    if len(config) > 255:
        raise ValueError("frame config exceeds 255 bytes")
    body = _HEADER.pack(MAGIC, kind, VERSION, len(config)) + config
    body += struct.pack(">I", len(payload)) + payload
    return body + _TRAILER.pack(zlib.crc32(body) & 0xFFFFFFFF)


def decode(buf: bytes | bytearray | memoryview, kind: int) -> tuple[bytes, bytes]:
    """Return ``(config, payload)`` for a frame of ``kind``, or raise FrameError."""
    data = bytes(buf)
    if len(data) < _HEADER.size + 4 + _TRAILER.size:
        raise FrameError("buffer is too short to be a sift frame")
    magic, got_kind, version, config_len = _HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise FrameError("bad magic: this is not a sift frame")
    if got_kind != kind:
        raise FrameError(
            f"buffer holds a {_KIND_NAMES.get(got_kind, f'kind {got_kind}')} "
            f"frame, expected {_KIND_NAMES[kind]}"
        )
    if version != VERSION:
        raise FrameError(f"unsupported frame version {version}, this build reads {VERSION}")
    at = _HEADER.size
    if at + config_len + _TRAILER_FIELD > len(data):
        raise FrameError(
            f"buffer is too short to hold the declared {config_len}-byte config"
        )
    config = data[at : at + config_len]
    at += config_len
    (payload_len,) = struct.unpack_from(">I", data, at)
    at += _TRAILER_FIELD
    if at + payload_len + _TRAILER.size != len(data):
        raise FrameError(
            f"declared payload is {payload_len} bytes, the buffer leaves "
            f"{len(data) - at - _TRAILER.size}"
        )
    payload = data[at : at + payload_len]
    (crc_want,) = _TRAILER.unpack_from(data, at + payload_len)
    if zlib.crc32(data[: at + payload_len]) & 0xFFFFFFFF != crc_want:
        raise FrameError("checksum mismatch: the buffer was modified or truncated")
    return config, payload
