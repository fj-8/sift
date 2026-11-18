# Architecture

`sift` is four layers, and the layering is the interesting part: nothing above
a layer reaches into one below it, which is why five unrelated estimators can
share one byte format and one hash.

```
                 sift.cli
                    |
   hll · cm · quantile · moments · reservoir
                    |
              _serial  (frames)
                    |
               _hash   (bytes -> 128 bits)
```

## Layer 1 — `_hash.py`

One job: turn any accepted Python value into 128 deterministic bits.

* The value is first **tagged**: `N` for `None`, `b\x00`/`b\x01` for bools,
  `i` plus a minimal signed little-endian byte string for ints, `s` for bytes,
  `u` for `str`. Without tags, `b"x"` and `"x"` would collide, and so would
  `1` and `True`.
* The tagged bytes go through `blake2b(digest_size=16, key=b"sift.v1")`.
* `halves()` splits the 16 bytes into two 64-bit ints. Everything downstream
  derives what it needs from those two halves rather than calling the hash
  again.

Two things follow from this layer being separate:

1. **Nothing uses the builtin `hash()`.** Its str seed is randomised per
   process, so a sketch written today would be a different sketch tomorrow.
   `tests/test_serial.py` proves determinism across a real process boundary.
2. **Count-Min needs no salt table.** Row *i*'s cell is
   `(k1 + i*k2 + i*i) % width` — a quadratic per row, derived arithmetically
   from the same two halves. Four rows cost two integers, not four keys.

Values that cannot be tagged raise `TypeError` here, once, rather than
producing a quietly wrong digest somewhere above.

## Layer 2 — `_serial.py`

Every persisted structure writes the same frame:

```
magic "SFT1" | kind | version | config_len | config | payload_len(u32) | payload | crc32
```

All big-endian; the CRC32 covers every preceding byte. The header and trailer
cost 15 bytes (4 magic + kind + version + config_len + 4 payload_len + 4 crc),
and each structure adds a fixed-size config block, so what `to_bytes()` costs
over the bare data is 16 B for an HLL, 31 B for Count-Min and 23 B for a
t-digest. The `config_len` byte caps a config block at 255 bytes.

* `kind` is what makes "wrong buffer" a different answer from "corrupt
  buffer": decoding a Count-Min frame as an HLL says so by name.
* `version` is 1 and nothing else has ever been written.
* The **config block is the geometry** (precision, or depth/width/mass, or
  compression plus counts) and the payload is the data. `decode()` validates
  the whole declared layout before any structure is sized, so a hostile frame
  cannot make the process allocate on its say-so. `tests/test_limits.py`
  pins this with a frame that claims `width = 2**31`.
* `FrameError` subclasses `ValueError`, so callers who only catch `ValueError`
  are already correct.

Adding a structure means adding a `_KIND_*` constant and a name. The framing,
checksum and rejection messages come free.

## Layer 3 — the structures

`hll.py`, `cm.py`, `quantile.py`, `moments.py`, `reservoir.py`. They do not
import each other. What they share is convention:

| Convention | Why |
| --- | --- |
| `__slots__` everywhere | the memory table in `docs/PERFORMANCE.md` is a measurement of those slots, so it cannot drift |
| `add(value)` accepts one item, `add_all(iterable)` loops | a stream caller and a collection caller both get the natural call |
| `memory_usage()` returns the container's own bytes | not RSS, not `sizeof` of the object header, and documented as such |
| `merge(other)` returns a new sketch and rejects a mismatched one | in-place merging of two geometries has no correct answer |
| `to_bytes()`/`from_bytes()` round-trip byte-identically | enforced by a test per structure |
| `__eq__` compares state, not identity | so `from_bytes(x.to_bytes()) == x` is a real assertion |
| `TypeError` for wrong type, `ValueError` for wrong value | and nothing else escapes |

The internal shapes differ, as they must:

* **HyperLogLog** is a `bytearray` of ranks. Update is one hash, one shift, one
  `leading_zeros`, one max. Merge is a register-wise max, which is why order
  does not matter.
* **CountMinSketch** is one flat `array("q")` of `width * depth` counters. Row
  positions are computed, so there is no per-row object and no salt to keep.
* **TDigest** is two parallel lists — means and integer weights — plus a
  pending buffer of `(value, weight)` pairs. Queries rebuild the cumulative
  centres on the fly; that is `O(centroids)` per query with no cached state to
  invalidate when the buffer flushes.
* **RunningStats** and **Ewma** hold only scalars, which is the whole point of
  Welford and of an exponential average.
* **Reservoir** holds `k` items and a counter; **WeightedReservoir** holds a
  heap of `k` `(priority, tie, item)` tuples.

## Layer 4 — `sift.cli`

`cli.py` is a thin, deliberate layer: `argparse`, five subcommands, and one
line reader. It streams input — a `for line in stream` over files or stdin —
so `sift stats` over a 40 GB log costs the sketch's fixed memory and nothing
else. It splits each line once, picks a field, and hands it to exactly the
same `add()` the library users call. There is no second implementation of
anything in this layer, which is why the CLI's numbers in the benchmark are
useful: they are the library plus I/O.

The CLI exits 2 on a bad row or a missing file with a one-line message and no
traceback, and treats a closed stdout pipe (`sift top … | head`) as success.

## What is deliberately not here

* **No windowed or decayed digests.** A t-digest with a sliding window needs a
  different data structure; `TDigest` is honest about being cumulative.
* **No NumPy.** Vectorised updates would make the throughput table look
  respectable and would add a dependency, which is the opposite trade.
* **No distributed sharding protocol.** `merge()` is the primitive; who calls
  it over the network is an application question.
* **No serialisation for `Reservoir`/`RunningStats`.** A sample is an answer,
  and `RunningStats.state`/`from_state` is the explicit checkpoint instead.
* **No encryption or signing of frames.** See `SECURITY.md`: a sketch is not an
  authorisation check.

## Testing strategy

One test module per layer: `_hash`+`_serial` in `test_serial.py`, each
structure in its own file, the CLI in `test_cli.py` (which asserts exact output
lines, because a CLI that changes its format has changed its API), and the
library-wide promises in `test_limits.py`. The package itself is checked in
`test_package.py` — `__all__`, the docstring table and the version — and every
README example is executed by `test_readme_examples.py`, so documentation
cannot rot into fiction.
