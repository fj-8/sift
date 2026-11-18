# Performance

Every number on this page is transcribed from one run of `bench/bench.py` on
this machine, and the exact baselines were measured in the same run. Nothing
here is quoted from a paper or from another project.

```console
$ python bench/bench.py > bench/bench-full.txt   # 149.8 s, the run reproduced below
$ python bench/bench.py --quick                  # ~15 s, smaller sizes, same paths
```

Every table on this page is the committed `bench/bench-full.txt`, transcribed by
hand. If a number here and a number in that file disagree, the file is right and
this page has a bug.

| | |
| --- | --- |
| Interpreter | CPython 3.11.9, run 2026-11-18 22:23 |
| Platform | Windows AMD64, single machine, no tuning |
| Seed | `20251118` — every stream below is reproducible |
| Mode | full: 1,000,000 accuracy items, 200,000 throughput ops, 500,000 CSV rows, 5 repetitions |

## Method

* **Median of five repetitions** of the identical loop, with the min–max spread
  printed beside it. A single timing of Python is noise; the spread is what
  tells you whether a row means anything.
* **Interpreter startup is measured, not assumed.** The CLI suite records an
  empty `python -m sift --version` run (48 ms in this run) and subtracts it
  from nothing — the table reports the wall time including startup, and the
  per-row figure after subtraction.
* **The baseline is the honest container, run on the same data.** `set(keys)`,
  `dict` counting, `sorted(numbers)`, the naive two-sum variance. When the
  sketch loses, the row says `slower`, not `competitive`.
* **Accuracy compares against the exact answer**, computed by sorting or
  counting the same stream in memory. For a million values that is affordable;
  for your stream it may not be, which is the trade these structures make.
* **Memory is `sys.getsizeof` walked recursively**, not RSS. It counts the
  container and the objects it owns.
* **Quick mode is for sanity, never for publication.** It shrinks every size by
  10–20×, so its absolute numbers are meaningless.

## Accuracy

Error against the exact answer. `measured` is the error this run observed,
`spread` the second statistic of that measurement, `bound` the promise.

| structure | stream | measured | spread | bound | note |
| --- | --- | --- | --- | --- | --- |
| HyperLogLog p=8 | 50,000 distinct | +0.10% | 11.63% | 6.50% | 16 independent builds, m=256 |
| HyperLogLog p=10 | 50,000 distinct | +0.38% | 4.86% | 3.25% | 16 independent builds, m=1024 |
| HyperLogLog p=12 | 50,000 distinct | +0.12% | 2.87% | 1.62% | 16 independent builds, m=4096 |
| HyperLogLog p=14 | 50,000 distinct | +0.99% | 2.30% | 0.81% | 16 independent builds, m=16384 |
| CountMinSketch eps=1e-3 delta=0.05 | 267 distinct | 0 max overcount | 0.00% of N | 0.10% promise | 2719x3 cells, never undercounts |
| CountMinSketch eps=1e-3 delta=0.05 | 1,000,000 distinct, count 1 each | 413 max overcount | 352 mean | 1,000 = eps*N | 2719x3 cells, never undercounts |
| TDigest delta=20 | 1,000,000 values | 0.00024 max rank error | 0.00010 median | 0 by construction | 175 centroids, 2,800 B |
| TDigest delta=100 | 1,000,000 values | 0.00005 max rank error | 0.00003 median | 0 by construction | 742 centroids, 11,872 B |
| TDigest delta=500 | 1,000,000 values | 0.00003 max rank error | 0.00001 median | 0 by construction | 3185 centroids, 50,960 B |
| RunningStats mean | 1,000,000 | 3.638e-12 | - | 0 (exact algorithm) | vs statistics.fmean |
| RunningStats variance | 1,000,000 | 1.637e-11 | - | 0 (exact algorithm) | vs statistics.variance |
| Reservoir inclusion | n=400, k=40 | 0.0325 max deviation | 0.0085 spread | 0.100 target | 1200 draws, one shared rng |
| WeightedReservoir k=1 | 55 total weight | 0.0094 max deviation | - | 0.0530 spread | 2000 draws, exact proportions as n grows |
| Ewma debias | 2,000 samples | 6.661e-16 | - | 0 (algebraic) | vs the explicit weighted mean |

Reading the two HyperLogLog columns together is the point. `measured` is the
mean error over 16 independent builds; `spread` is the worst single build. At
`p=8` that worst build came in at 11.63% against a 1σ bound of 6.50% — nearly
twice the bound, which is what the tail of a Gaussian looks like with 16
samples. Anyone who tells you HyperLogLog is "within 2%" should be asked how
many builds they ran.

The Count-Min pair is deliberately two rows. Over a Zipf stream of 267 hot keys
the maximum overcount was 0, because the minimum across three rows rarely picks
a polluted row when one key dominates. Over a flat stream of a million count-1
keys, the same geometry overcounted by up to 413 with a mean of 352, against
the `eps·N = 1,000` the sketch was sized for. Neither row undercounted.

## Throughput

| operation | sketch | exact baseline | sketch per op | verdict |
| --- | --- | --- | --- | --- |
| `HyperLogLog(12).add` ×200,000 | 590.44 ms (552.6–619.1) | 20.70 ms `set(keys)` | 2,952 ns/op | 28.5× slower |
| `CountMinSketch(2048,4).add` ×200,000 | 944.27 ms (932.2–981.7) | 73.40 ms `dict` counting | 4,721 ns/op | 12.9× slower |
| `TDigest(100).add` ×200,000 | 1,117.85 ms (952.3–1,151.8) | 50.14 ms `sorted(values)` | 5,589 ns/op | 22.3× slower |
| `Reservoir(50).add` ×200,000 | 132.43 ms (121.1–140.0) | 2.33 ms `list(keys)` | 662 ns/op | 56.9× slower |
| `RunningStats.add` ×200,000 | 116.69 ms (112.9–134.4) | 14.69 ms naive sums | 583 ns/op | 7.9× slower |
| `HyperLogLog.add` ×2,000 vs membership | 5.33 ms (5.1–5.7) | 0.13 ms `key in set` | 2,666 ns/op | 42.0× slower |
| `CountMinSketch.query` ×2,000 vs `dict.get` | 9.49 ms (9.1–10.1) | 0.14 ms | 4,747 ns/op | 65.7× slower |
| `TDigest.quantile` ×1,000 vs indexed sorted list | 48.02 ms (44.6–52.6) | 0.06 ms (sort excluded) | 48,025 ns/op | 759.9× slower |
| `line.split` + `RunningStats.add` vs `line.split` alone | 202.74 ms (196.4–209.9) | 45.51 ms | 1,014 ns/op | 4.5× slower |

Three consequences for how to use the library:

1. The **marginal cost of an exact statistic on a parsed line is 4.5× the
   parse**. If you are already reading CSV in Python, `stats` is not your
   bottleneck.
2. A **t-digest query is a linear scan** of the centroid list (742 centroids at
   δ=100). Ask it once per report, not once per request. The sorted-list row
   excludes the sort, so it is the best possible exact comparison, and the
   digest loses by 760×.
3. The Count-Min build is only 12.9× a `dict` count because the dictionary is
   doing Python-level hashing too; on the per-item rows (`query` vs `dict.get`)
   the gap widens to 65.7×. Fixed memory is what you are buying, not speed.

## Memory

1,000,000 items in, the same answer out.

| structure | live bytes | what it counted | scales with | exact baseline it replaces |
| --- | --- | --- | --- | --- |
| `HyperLogLog(12)` | 4,201 B | 1,000,000 distinct | constant | `set`: 92,443,538 B (22,005×) |
| `CountMinSketch(2048,4)` | 69,800 B | 1,000,000 mass | constant | `dict`: 30,758,348 B (440×) |
| `TDigest(100)` | 6,640 B | 371 centroids | `O(compression)` | `list`: 32,448,728 B (4,886×) |
| `Reservoir(50)` | 3,481 B | 50 items | constant | the full list |
| `RunningStats` | 72 B | 1,000,000 counted | constant | two `float` accumulators, not a container |

This is the table that justifies the project.

The centroid count is a function of the stream as well as of `compression`:
this digest holds 371 centroids, the accuracy table above reports 742 for its
own Gaussian stream, and `TDigest(100)` fed an *affine rescaling of the same
million values* holds 449. So the byte figure is printed next to the count
rather than derived from a formula, and `O(compression)` should be read as
"scales with `compression`, not with `n`" — which is what the growth tests in
`tests/test_limits.py` actually assert.

## Serialisation

| structure | frame | encode | decode | framing |
| --- | --- | --- | --- | --- |
| `HyperLogLog(14)` | 16,400 B | 0.018 ms | 0.019 ms | 16 B over the bare registers |
| `CountMinSketch 2719x3` | 65,287 B | 0.091 ms | 0.090 ms | 31 B |
| `TDigest(200)`, 1,191 centroids | 19,095 B | 0.941 ms | 0.563 ms | 23 B, plus 16 B per centroid |

Round-trip, not one-way: the suite asserts `X.from_bytes(x.to_bytes()) == x` on
the same objects it times.

## Determinism

| check | result | scale |
| --- | --- | --- |
| HLL + Count-Min + t-digest frames hashed in two interpreters | `59a0aef9312b…` = `59a0aef9312b…` | 2 subprocesses |
| HyperLogLog insertion order | 50,864 both ways | 50,000 keys |
| Count-Min split into shards and merged | 5000/5000 queries exact | 50,000 keys |
| t-digest split into shards and merged | 0.00038 max CDF gap | 50,000 values |

The last row is the one that is not a guarantee. Merging re-compresses, so four
shards give a slightly different digest than one pass. It is within the rank
error the structure already promises, and it is reported rather than hidden.

## Sharding

| operation | sketch | exact baseline | verdict |
| --- | --- | --- | --- |
| Build 4 `HyperLogLog` shards + merge | 607.47 ms (546.6–709.7) | 18.07 ms `set()` | 33.6× slower |
| Build 4 Count-Min shards + merge | 1,016.82 ms (950.7–1,198.4) | 83.86 ms `dict` | 12.1× slower |
| Build 4 t-digest shards + merge | 893.14 ms (864.3–971.6) | 2.12 ms `sorted()` | 422.3× slower |
| Two shards + `RunningStats.combine` vs one long pass | 115.54 ms (112.1–121.1) | 116.65 ms | 1.0× — the same |
| Merge correctness, t-digest | 0.00043 rank gap, 682 centroids one pass vs 599 merged | — | 4 shards |

`RunningStats.combine` is the useful row: splitting a stream in two and merging
costs nothing versus one pass, and gives the exact same moments. Sketch merging
costs the same as building the shards, because hashing dominates — merging is
not a speed trick here, it is a *parallelism* trick, and the only reason to
want it is that the shards live on different machines.

## The CLI

500,000-row CSV, three subprocess runs per row, median reported; interpreter
startup for this machine is 48 ms and is included in the wall time.

| command | median | spread | per row |
| --- | --- | --- | --- |
| `sift stats --column 3 --separator ,` | 923 ms | 852–926 ms | 1.75 µs/row |
| `sift quantiles --column 3` | 4,052 ms | 3,690–4,999 ms | 8.01 µs/row |
| `sift cardinality --column 2` | 2,009 ms | 1,988–2,096 ms | 3.92 µs/row |
| `sift top --column 2` | 3,350 ms | 3,220–3,636 ms | 6.60 µs/row |
| `sift sample 20` | 699 ms | 665–809 ms | 1.30 µs/row |
| read the file and sum column 3 in-process | 258 ms | — | 0.52 µs/row |

The last row is the floor: reading 500,000 lines and adding one column, no
sketch and no subprocess. `stats` costs 3.4× that floor; `sample` 2.5×;
`quantiles` 15×, because every row goes through `TDigest.add` at ~5.6 µs.
`sift top` does two passes (a `dict` and a sketch) which is why it is not
cheaper than `cardinality`.

## What this does not tell you

* Not a comparison against other libraries. There is no `datasketch` row here
  because it was not installed for this run; the only baselines measured are
  exact ones.
* Not a scaling curve past 1M items. The memory rows are constant by
  construction and tested to stay that way; the time rows are linear and
  measured at one point on the line.
* Not a multithreaded or async picture. There is none, and none planned.
* One machine, one interpreter, one run per table. If you publish a different
  number, publish the run that produced it — `bench/bench.py --json` gives you
  the rows in machine-readable form for exactly that purpose.
