# Contributing to sift

Thanks for taking an interest. This is a small library with three rules, and
they are the reason it is worth using.

## The three rules

1. **The standard library is the whole dependency list.** No `numpy`, no
   `mmh3`, no `pytest`, not even for tests. If a change adds a line to
   `dependencies`, it is not a change to `sift`.
2. **Every claim is a test or a measurement.** Error bounds, byte counts,
   "never undercounts", "same bytes in another process" — each one is pinned
   in `tests/`. A pull request that changes behaviour without touching a test
   will be asked to add one.
3. **No fabricated performance.** Numbers in the README and in
   `docs/PERFORMANCE.md` come from a run of `bench/bench.py` on a real
   machine, in this repo, with the exact baseline measured in the same run.
   `sift` is pure Python and loses to a `set` on speed; the tables say so.
   A patch that makes a number worse should paste both runs.

## Getting set up

Python 3.11 or newer. There is nothing to install:

```console
$ git clone https://github.com/fj-8/sift && cd sift
$ PYTHONPATH=src python -m unittest discover -s tests -t .
Ran 231 tests in 12.9s
```

`src` layout means the package is not importable until you put `src` on the
path, which keeps tests honest about which copy they exercise. On Windows use
`set PYTHONPATH=src`; on POSIX, `export`.

To use the CLI the way a caller would:

```console
$ PYTHONPATH=src python -m sift --version
sift 0.1.0
```

## Where the code lives

| File | Holds |
| --- | --- |
| `src/sift/_hash.py` | the tagged encoding and the keyed `blake2b` |
| `src/sift/_serial.py` | the frame: `magic kind version config payload crc32` |
| `src/sift/hll.py` | HyperLogLog |
| `src/sift/cm.py` | Count-Min |
| `src/sift/quantile.py` | t-digest |
| `src/sift/moments.py` | Welford `RunningStats`, `Ewma` |
| `src/sift/reservoir.py` | Algorithm R, A-Res |
| `src/sift/cli.py` | the five subcommands and the line reader |
| `bench/bench.py` | the seven suites behind every published number |
| `tests/` | one module per structure, plus frames, CLI and growth bounds |

## Adding a structure

Five things, or it does not fit the shape of the library:

- `__slots__`, and a `memory_usage()` that counts what the structure owns.
- An `add()` that validates its argument the way the others do: `TypeError`
  for the wrong kind, `ValueError` for a wrong value, never a traceback from
  inside a math expression.
- If it can be persisted, a `_serial` kind and a config block whose geometry
  is checked against the payload length *before* anything is allocated.
  `tests/test_limits.py` is the model.
- Round-trip equality: `X.from_bytes(x.to_bytes()) == x`, plus a test that a
  single edited byte is rejected.
- A row in `bench/bench.py`'s accuracy suite comparing it to an exact
  baseline, and a row in the README table.

## Running the benchmarks

```console
$ python bench/bench.py --quick       # about 15 seconds, for a sanity check
$ python bench/bench.py               # the full run, about 2.5 minutes
$ python bench/bench.py --suite memory --json
```

`--quick` shrinks the sizes; never publish quick-mode numbers, they are the
wrong scale. The full run prints a header with the interpreter, platform and
date, and a seed, so a result is reproducible on the same machine.

## Commit style

Imperative, scoped, and about the why: `quantile: flush the buffer before cdf
so a fresh digest answers`. One logical change per commit. If a commit exists
because a benchmark moved, say how much: `bench: subtract interpreter startup
from the cli suite (was +43ms per row)`.

## Tests before anything else

If you found a bug, the first commit should be a test that fails. Several
existing tests were written exactly that way and are marked with the bug they
pinned — for example a `cdf()` that was not the inverse of `quantile()` at a
centroid mean.

## License

By submitting a pull request you agree to license your contribution under both
MIT and Apache-2.0, the same dual licence as the rest of the project. Every
source file carries `SPDX-License-Identifier: MIT OR Apache-2.0`.
