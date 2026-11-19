# Changelog

All notable changes to `sift` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- A root `LICENSE` file stating both grants, the `MIT OR Apache-2.0` SPDX
  identifier, and the contribution clause; `[tool.setuptools] license-files` now
  ships it in the distribution next to the two full texts it points at. The wheel
  that carried only `LICENSE-MIT` and `LICENSE-APACHE` told a downstream user the
  two licenses existed but not that they are alternatives. No terms changed.
- `.github/ISSUE_TEMPLATE/bug-report.yml` and `.github/dependabot.yml`. The form
  asks for the estimator, the calls and input, the commands with the `src`-layout
  `PYTHONPATH` spelled out for Windows as well as POSIX, and `python -VV`; the
  Dependabot config watches `github-actions` only, since `dependencies = []`
  leaves the workflow pins as the only versions in the repository that can go
  stale.

### Fixed
- `-` is stdin on every subcommand. `sift stats -` used to try to open a file
  named `-` and exit 2 with `No such file or directory`.
- `sift sample 20 --seed 7 f.csv` parses. `main()` moves `sample`'s positionals
  in front of its options, because Python 3.11's argparse will not resume a
  variadic positional once an option has intervened; `sift sample --seed 7 20
  f.csv` draws the same lines from the same seed either way, which
  `tests/test_cli.py` now asserts.
- The suite is 231 tests.

## [0.1.0] - 2026-11-18

Initial public release. Pure standard library, Python 3.11+.

### Added
- `HyperLogLog`: precision 4–16 (16 B to 64 KiB of registers), `add`,
  `add_all`, `count`, `register_count`, `standard_error`, `empty_registers`,
  `registers`, `merge`, `memory_usage`, `to_bytes`/`from_bytes`. Cardinality
  uses the Flajolet estimator plus linear counting for the sparse range, and
  the 64-bit hash means no large-range correction is applied or needed.
- `CountMinSketch`: `width`/`depth` construction or
  `with_accuracy(eps, delta)`, `add(item, count)`, `add_all(items, count)`,
  `query`, `merge`, `mass`, `cells`, `to_bytes`/`from_bytes`. Counts are
  `array("q")`, row positions are derived arithmetically from two 64-bit hash
  halves instead of a per-row salt table.
- `TDigest`: merging variant with the `4·n·q·(1−q)/δ` size limit, `add(value,
  weight)`, `add_all`, `compress`, `merge`, `quantile`, `cdf`,
  `centroid_count`, `buffered`, `to_bytes`/`from_bytes`. The pending buffer
  flushes at `max(64, 2 * compression)`.
- `RunningStats`: Welford mean/variance with `min`, `max`, `stdev`, `pstdev`,
  `population_variance`, the `combine(a, b)` parallel merge, and
  `state`/`from_state` for checkpointing. `Ewma`: `with_span`, `debias`
  option, exact weighted-mean semantics.
- `Reservoir` (Vitter's Algorithm R), `WeightedReservoir` (Efraimidis–Spirakis
  A-Res keys with `−log(u)/weight`) and the one-shot `reservoir_sample`.
- `sift._serial`: one frame for every persisted structure —
  `magic | kind | version | config_len | config | payload_len | payload | crc32`,
  big-endian, CRC32 over all preceding bytes. `FrameError` is a `ValueError`.
- `sift._hash`: `blake2b(digest_size=16, key=b"sift.v1")` over a tagged
  encoding, so hashing does not depend on `PYTHONHASHSEED` or on the process.
- `sift` CLI: `cardinality`, `top`, `quantiles`, `stats`, `sample`, reading
  files or stdin line by line, with `--column`,
  `--separator`, `--skip-bad`,
  `--precision`, `--items`, `--eps`, `--delta`, `--compression`, `--qs`,
  `--exact`, `--seed`.
- `bench/bench.py`: seven suites (accuracy, throughput, memory, serialisation,
  determinism, merge, cli) with the exact `set`/`dict`/`sorted` baselines
  measured in the same run, `--quick` and `--json`.
- 230 tests under `tests/`, including a suite that pins the frame layout,
  byte-edit rejection, cross-process determinism, and the invariant that a
  hostile frame cannot allocate memory from its own claimed geometry.

### Notes
- `Reservoir` and `RunningStats` are deliberately not serialisable: a sample
  is an answer, not a state, and `RunningStats.state` is the checkpoint API
  for the one case where you need to persist the scalars.
- Benchmarks in the README and `docs/PERFORMANCE.md` are transcribed from a
  real run of `bench/bench.py` on one machine, not quoted from papers.
