# sift

**Numbers about streams you cannot hold in RAM.**

<p>
  <a href="https://github.com/fj-8/sift/actions/workflows/ci.yml"><img src="https://github.com/fj-8/sift/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="#license"><img src="https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue" alt="License: MIT OR Apache-2.0"></a>
  <img src="https://img.shields.io/badge/dependencies-0-informational" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/python-3.11%2B-orange" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/tests-231%20passing-brightgreen" alt="231 tests">
  <img src="https://img.shields.io/badge/stdlib-only-green" alt="Standard library only">
</p>

Counting distinct users in a 40 GB log, or p99 latency over a stream, normally
means either sorting something that does not fit in memory or reaching for a
service. `sift` is the third option: five estimators that hold a fixed few
kilobytes and give an answer with a **stated error bound** instead of a silent
approximation.

No dependencies. Not one — no NumPy, no `mmh3`, no `pytest`. The hash is
`hashlib.blake2b`, the counters are `array("q")`, the tests are `unittest`.

```console
$ sift cardinality --column 2 --separator , --skip-bad access.log.csv
rows            50001
distinct        247
registers       4096 (4096 bytes)
expected error  1.62%
duplication     99.51% of rows
```

247 for 250 real users, in 4 KiB, in one pass, with the error printed.

---

## Table of contents

- [Install](#install)
- [Quick start](#quick-start)
- [The five structures](#the-five-structures)
- [Which one do I need](#which-one-do-i-need)
- [Measured, not promised](#measured-not-promised)
- [The honest part: speed](#the-honest-part-speed)
- [One byte format](#one-byte-format)
- [The CLI](#the-cli)
- [Guarantees and non-goals](#guarantees-and-non-goals)
- [Development](#development)
- [License](#license)

## Install

Python 3.11 or newer. It is not on PyPI yet, so install from the tag — this
fetches `setuptools` to build, and builds nothing else:

```console
$ python -m pip install git+https://github.com/fj-8/sift.git@v0.1.0
```

Or from a clone (`pip install .` has been verified from a clean venv), or with
no installation at all, since the package is pure Python:

```console
$ git clone https://github.com/fj-8/sift && cd sift
$ PYTHONPATH=src python -m sift --version
sift 0.1.0
```

Installing builds a `sift` console script. The module is also runnable as
`python -m sift`.

## Quick start

```python
import sift

digest = sift.TDigest(100)
users = sift.HyperLogLog(12)
stats = sift.RunningStats()

for index in range(100_000):
    latency = 120.0 + (index % 97) - (index % 13)
    digest.add(latency)
    users.add(f"user-{index % 250}")
    stats.add(latency)

print(f"p99    {digest.quantile(0.99):6.1f} ms")
print(f"mean   {stats.mean:6.1f} ms  sd {stats.stdev:4.1f}")
print(f"users  {users.count():6d}  +/- {users.standard_error:.2%}")
```

```text
p99     212.0 ms
mean    162.0 ms  sd 28.2
users     248  +/- 1.62%
```

Every structure takes one item at a time (`add`), so the same code works for a
socket, a generator, or a file you are reading line by line. `add_all` is the
loop for the case where you already have a collection.

## The five structures

| | Answers | Memory | Error |
| --- | --- | --- | --- |
| `HyperLogLog` | how many **distinct** items | 16 B – 64 KiB (`precision` 4–16) | ~`1.04/sqrt(m)`, one standard deviation, not a bound |
| `CountMinSketch` | how often **each** item occurred | `width × depth` × 8 B | never low; at most `eps·N` high with probability `1−delta` |
| `TDigest` | **quantiles** and CDF | ~`compression` centroids | rank error, measured below at 2.4e-4 with δ=20 |
| `Reservoir` | a fair **k-subset** | k items | none — the sample is exact, its variance is the cost |
| `RunningStats` | mean, variance, min, max | 72 B | floating point only: 3.6e-12 measured vs `statistics` |

`Ewma` sits alongside `RunningStats` for smoothed series, with `with_span` and
optional bias correction; `WeightedReservoir` is the A-Res sampler for when
items have weights.

```python
counts = sift.CountMinSketch.with_accuracy(eps=1e-3, delta=0.05)
counts.add_all(["GET /health", "GET /health", "POST /orders"])
counts.query("GET /health")          # >= 2, always
```

## Which one do I need

- "How many users did we see today?" → `HyperLogLog`. If the answer might be
  under a few hundred, use a higher precision or just use a `set`.
- "Which of these 400 known endpoints is hot?" → `CountMinSketch`. If you are
  asking about keys you have never seen, remember the estimate is an upper
  bound and an unseen key can look popular.
- "What is my p99?" → `TDigest`. If you need the exact p99 of a batch you can
  sort, sort.
- "Give me 50 rows to eyeball" → `Reservoir`, or `sift sample 50 file.csv`.
- "Just the mean and spread" → `RunningStats`. It is exact, cheap, and merges
  across shards with `RunningStats.combine`, which is the one case where the
  sketch is strictly better than the naive `sum(x**2)/n - mean**2`.

And if the data fits in memory and you can hold the whole thing: use `set`,
`dict`, `sorted` and `statistics`. They are faster *and* exact. `sift` is for
the case where that option has already been taken away from you.

## Measured, not promised

From `bench/bench.py` on Python 3.11.9 / Windows AMD64, 2026-11-18, seed
`20251118`, full mode. The exact baseline in each row is measured in the same
run, not quoted from a paper.

HyperLogLog, 50,000 distinct keys, mean and worst of 16 independent builds:

| precision | registers | mean error | worst build | 1σ |
| --- | --- | --- | --- | --- |
| 8 | 256 | +0.10% | 11.63% | 6.50% |
| 10 | 1,024 | +0.38% | 4.86% | 3.25% |
| 12 | 4,096 | +0.12% | 2.87% | 1.62% |
| 14 | 16,384 | +0.99% | 2.30% | 0.81% |

The worst build at `p=8` came in at nearly twice its bound. That is what one
standard deviation means, and it is why the report prints `expected error`
instead of the word "accurate", and why `docs/PERFORMANCE.md` shows the mean
and the worst build rather than the mean alone.

Count-Min with `eps=1e-3, delta=0.05` (2,719 × 3 cells) over 1,000,000
increments: over a flat stream of a million count-1 keys the **maximum
overcount was 413, mean 352, against an `eps·N` allowance of 1,000**, and no
estimate was ever below truth. Over a Zipf stream of 267 hot keys the maximum
overcount was 0 — a hot key rarely shows a collision, because the minimum
across three rows only reports pollution when every row is polluted, which is
why the flat stream is the row that matters.

t-digest rank error over 1,000,000 Gaussian values, at the 21 quantiles from
p05 to p100:

| compression | centroids | live bytes | max rank error | median |
| --- | --- | --- | --- | --- |
| 20 | 175 | 2,800 B | 0.00024 | 0.00010 |
| 100 | 742 | 11,872 B | 0.00005 | 0.00003 |
| 500 | 3,185 | 50,960 B | 0.00003 | 0.00001 |

These are *rank* errors: the p99 that `TDigest(20)` returned is the value at
rank `0.9900 ± 0.00024` of the true distribution — on a million values, that is
a couple of samples either side.

Memory, all four baselines sized for the same 1,000,000-item stream:

| structure | live bytes | exact baseline it replaces | ratio |
| --- | --- | --- | --- |
| `HyperLogLog(12)` | 4,201 B | `set` of 1M keys: 92,443,538 B | 22,005× |
| `CountMinSketch(2048, 4)` | 69,800 B | `dict` of 1M keys: 30,758,348 B | 440× |
| `TDigest(100)` | 6,640 B | `list` of 1M floats: 32,448,728 B | 4,886× |
| `Reservoir(50)` | 3,481 B | the list it samples from | — |
| `RunningStats` | 72 B | 1M floats | — |

Sizes are `sys.getsizeof` of the container plus the objects it owns; this is
not RSS, and the t-digest figure includes its pending buffer. That row's digest
holds 371 centroids while the accuracy table above reports 742 — centroid count
depends on the stream, not only on `compression`, which is why the bytes are
measured rather than computed from a formula.

## The honest part: speed

Pure Python loses to C containers by two orders of magnitude, and pretending
otherwise would be the first thing to date this README.

| operation | sketch | exact baseline | verdict |
| --- | --- | --- | --- |
| `HyperLogLog(12).add` ×200,000 | 2,952 ns/op | 20.70 ms to build a `set` | 28.5× slower |
| `CountMinSketch(2048,4).add` ×200,000 | 4,721 ns/op | 73.40 ms to count into a `dict` | 12.9× slower |
| `TDigest(100).add` ×200,000 | 5,589 ns/op | 50.14 ms to `sorted()` the same values | 22.3× slower |
| `Reservoir(50).add` ×200,000 | 662 ns/op | 2.33 ms to keep the list | 56.9× slower |
| `RunningStats.add` ×200,000 | 583 ns/op | 14.69 ms of naive sums | 7.9× slower |
| `CountMinSketch.query` vs `dict.get` | 4,747 ns | 70 ns | 65.7× slower |
| parsing a CSV line, then `add` | 1,014 ns/op | 228 ns for the split | 4.5× the parse alone |
| `TDigest.quantile` | 48,025 ns | 62 ns (`sorted()[i]`, sort excluded) | 759.9× slower |

Two numbers worth reading carefully. The marginal row: once you are parsing a
line at all, folding it into a `RunningStats` costs 4.5× the parse, so on a
real log the I/O and the splitting still dominate. And the last row: a t-digest
**query** is a linear scan of the centroid list — 742 centroids at δ=100 — so
ask it once per report, not once per request. A sorted array is 760× faster at
the lookup you already have the data for, and needs 4,886× the memory to have
it available.

The CLI numbers, 500,000-row CSV, median of 3 subprocess runs (interpreter
startup of 48 ms included in the wall time): `stats` 1.75 µs/row, `sample`
1.30 µs/row, `cardinality` 3.92 µs/row, `top` 6.60 µs/row, `quantiles`
8.01 µs/row. Reading the same file and summing one column in-process costs
0.52 µs/row — that is the floor, and `stats` is 3.4× above it, most of the
difference `str.split` and `float()`.

`docs/PERFORMANCE.md` has every table, the methodology, and the exact
`bench/bench.py` invocation for each.

## One byte format

`HyperLogLog`, `CountMinSketch` and `TDigest` all persist through the same
frame:

```
magic "SFT1" | kind | version | config_len | config | payload_len | payload | crc32
```

with a CRC32 over every preceding byte, so a single flipped bit is a
`FrameError` naming the structure rather than a sketch full of garbage. The
framing costs 16 bytes for HLL, 31 for Count-Min, 23 for the t-digest; a
p14 HLL frame is 16,400 bytes and round-trips byte-identically.

Nothing here depends on `PYTHONHASHSEED`, because nothing calls `hash()`:
keys are tagged and run through keyed BLAKE2b. A sketch built in one process
means the same thing in the next, and the benchmark checks that by hashing a
stream in two interpreters and comparing the frames.

A hostile frame cannot make you allocate: the declared geometry is checked
against the bytes actually present before any counter array is sized. A
crafted Count-Min frame claiming `width = 2**31` is rejected with
`FrameError` in well under a millisecond, and `tests/test_limits.py` asserts
that.

## The CLI

Five subcommands, each reading stdin or files line by line — never the whole
file — and each printing an aligned report:

```console
$ cat access.log.csv | sift quantiles --column 3 --skip-bad
     q          digest
 0.500         120.019
 0.900         158.826
 0.990         190.421

50000 values, 547 centroids
$ sift top --column 2 --separator , --skip-bad --items 5 access.log.csv | head -3
     count      sketch    error  item
       260         260        0  u0080
       245         245        0  u0218
```

`top` runs two passes and shows both answers: `count` from a `dict`, `sketch`
from Count-Min, `error` = the difference. It is the only subcommand that does
not have to be that way — it exists so you can see how much the sketch would
have cost you on data you can still hold. Bad rows stop the command with
`sift: file:1: 'latency_ms' is not a number` and exit code 2; `--skip-bad`
opts out. Full documentation in [`docs/CLI.md`](docs/CLI.md).

## Guarantees and non-goals

Guaranteed, and tested:

- `CountMinSketch.query` never underestimates. Ever.
- Frames round-trip byte-identically, and a one-byte edit is always rejected.
- `HyperLogLog.merge` is order-free: any partition of a stream merges to the
  same registers.
- `RunningStats.combine(a, b)` equals a single pass over `a + b`.
- Structures do not grow with `n`: `tests/test_limits.py` updates each one
  100,000–500,000 times and asserts it does not.
- Same input, same bytes, in a different process.

Not guaranteed, by design:

- **Sketches are not tamper-proof.** `query()` is an upper bound, and someone
  who controls the keys and knows your geometry can aim collisions at a cell.
  Never use a sketch as an authorisation check. See [`SECURITY.md`](SECURITY.md).
- **Estimates are not exact**, including at "round" numbers: `count()` is a
  rounded float, so 250 real users can print as 247.
- **No sliding window.** Nothing here forgets; a decayed or windowed digest is
  a different data structure and is not in this release.
- **`Reservoir` and `RunningStats` are not serialisable.** A sample is an
  answer, not a state. `RunningStats.state` / `from_state` is the checkpoint
  API for the one case where you need one.
- No async API, no thread safety, no `numpy` interop.

## Development

```console
$ PYTHONPATH=src python -m unittest discover -s tests -t .
Ran 231 tests in 12.9s
$ python bench/bench.py --quick        # 15 s sanity pass
$ python bench/bench.py > bench/bench-full.txt   # the run behind every number
```

`bench/bench-full.txt` is that transcript, committed: the run in it is the one
every table in `docs/PERFORMANCE.md` and the README was copied from (seed
`20251118`, Python 3.11.9, Windows AMD64, 149.8 s).

231 tests, no test dependencies, and one test module per structure plus frames,
CLI, growth bounds and the README examples. [`CONTRIBUTING.md`](CONTRIBUTING.md)
is three rules: the standard library is the entire dependency list, every claim
is a test or a measurement, and no fabricated performance. Architecture in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), the maths and citations in
[`docs/ALGORITHMS.md`](docs/ALGORITHMS.md).

`.github/workflows/ci.yml` runs the same `unittest discover` across Python
3.11, 3.12 and 3.13 on ubuntu-latest and windows-latest, then `pip install .`
and a smoke run of both entry points (`sift --version`, `python -m sift
--help`) inside each of those six jobs. All six were green on the commit that
added the workflow and green again on the commit after it; before they existed,
every number here came from the Windows runs above.

## Elsewhere in this account

The same rules applied in two other languages — zero dependencies, and every
number in the README measured in the same checkout:

- [`karg`](https://github.com/fj-8/karg) — dependency-free Rust argument parser
  with typed accessors and errors that name the nearest valid flag.
- [`bitlane`](https://github.com/fj-8/bitlane) — header-only C++20 compressed
  bitmap index over `uint32_t` sets, with a versioned, CRC32C-checked wire
  format.

## License

Dual-licensed under [MIT](LICENSE-MIT) and
[Apache-2.0](LICENSE-APACHE), at your option — the same arrangement as Rust
itself, so `sift` code can move between this project and Rust projects without
a relicensing step. SPDX identifier: `MIT OR Apache-2.0`.
