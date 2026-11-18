# Algorithms

What each estimator computes, why this implementation computes it that way, and
where the numbers in `docs/PERFORMANCE.md` come from. Every formula here is the
formula in the source; if they ever disagree, the source is the bug.

## Contents

- [The hash everything shares](#the-hash-everything-shares)
- [HyperLogLog](#hyperloglog)
- [Count-Min](#count-min)
- [t-digest](#t-digest)
- [Welford and Chan](#welford-and-chan)
- [EWMA](#ewma)
- [Reservoir sampling](#reservoir-sampling)
- [Weighted reservoir sampling](#weighted-reservoir-sampling)
- [The frame](#the-frame)
- [References](#references)

## The hash everything shares

`_hash.digest(item)` is `hashlib.blake2b(encode(item), digest_size=16,
key=b"sift.v1")` — 128 bits, keyed with a constant so that the function is a
*sift* hash and not somebody else's BLAKE2b, and never the builtin `hash()`,
whose string seed changes every process.

`encode()` tags the value before hashing so that equal-looking objects of
different types stay distinct:

| Value | Bytes hashed |
| --- | --- |
| `None` | `b"N"` |
| `True` / `False` | `b"b\x01"` / `b"b\x00"` — before the int branch, deliberately |
| `int` | `b"i"` + minimal two's-complement little-endian, unbounded: `2**63` is 9 bytes |
| `bytes` | `b"s"` + the bytes |
| `str` | `b"u"` + UTF-8 |

Everything else raises `TypeError`. Tuples, floats and unhashable objects are
rejected rather than coerced, because a sketch that accepts `1.0` and `1` as
different keys is a sketch that lies about cardinality, and one that accepts
them as the same key lies in the other direction. Say which you want by
hashing what you mean.

`halves()` splits the 16 bytes into two big-endian 64-bit ints `k1, k2`.
Sketches that need several indices derive them arithmetically from those two
(see Count-Min) instead of re-hashing per row: one hash per item, always.

`leading_zeros(value, bits) = bits - value.bit_length()` — no loop, no string
formatting.

## HyperLogLog

**Distinct counts in `2**precision` bytes.**

Precision `p` (4–16) gives `m = 2**p` registers, each one byte. For an item:

```
index = k1 >> (64 - p)                 # top p bits pick the register
rank  = leading_zeros(k1 & mask, 64-p) + 1   # zeros in the remaining bits
registers[index] = max(registers[index], rank)
```

The rank is the length of the longest run of zeros that a *new* distinct item
could plausibly have produced; `1` means "no information". The estimator then
inverts the harmonic mean of `2**rank`:

```
E  = alpha_m * m**2 / sum(2**-registers[i])
```

with the exact small-`m` constants `alpha_16 = 0.673`, `alpha_32 = 0.697`,
`alpha_64 = 0.709`, and `alpha_m = 0.7213 / (1 + 1.079/m)` above that — the
asymptotic value is within 1% of the fitted one from `m = 128` on, so the table
stops at 64.

Two small-sample details that are easy to get wrong:

* **Linear counting takes over** when `E <= 2.5m` *and* at least one register is
  still zero: then `E = m * ln(m / V)` where `V` is the zero count. That is the
  sparse regime where the harmonic estimator is biased high because the
  registers are mostly empty.
* **There is no large-range correction.** The original paper's `k > 2**32/30`
  branch exists because its hash was 32 bits. Here the index uses the top bits
  of a 64-bit digest, so the correction is not applied and the bug it prevents
  cannot occur.

Standard error is the textbook `1.04 / sqrt(m)`: 6.5% at `p=8` (256 registers,
256 B), 3.25% at `p=10`, 1.62% at `p=12` (4 KiB), 0.81% at `p=14` (16 KiB),
0.41% at `p=16` (64 KiB). `standard_error` prints it and the CLI repeats it,
because a cardinality number without its error is a fabrication. It is one
standard deviation, not a maximum: in the measured run, 16 builds at `p=8` had
mean error +0.10% and a worst case of 11.63% against the 6.50% bound.

**Merge** is `max` per register, which is associative, commutative and
idempotent — so any partition of a stream, in any order, gives the same
registers as one pass. `tests/test_hll.py` and the determinism suite both
check it.

## Count-Min

**Per-item frequencies, never undercounting.**

`depth = ceil(ln(1/delta))` rows, `width = ceil(e/eps)` cells, held in one flat
`array("q")` of signed 64-bit counters. Row `i`'s cell is

```
(k1 + i*k2 + i*i) % width
```

one independent quadratic per row, derived from the same two hash halves. This
is the standard cheap substitute for `depth` separately-keyed hashes: it is not
fully independent — for fixed `i` it is a bijection-ish map, and two items that
collide in the `(k1, k2)` space collide in every row — but items that collide
in one row collide in all of them only if their `k1, k2` are equal, which is a
128-bit coincidence. The measured overcount on a million distinct keys was
413 (mean 352) against an `eps·N` allowance of 1,000.

Update is `counter += weight` in every row; query is the **minimum** over rows.
The min is what makes the estimate an upper bound: each row is biased up (by
whatever else landed in the cell), so the least-biased row wins. This means:

* `query` ≥ true count, always. Not "usually" — the arithmetic cannot do
  otherwise.
* An item never seen can return a positive count. `query` answers "at least".
* Error is relative to the **total mass** `N`, not to the item's own count. A
  `+eps*N` allowance is invisible on hot keys and can dominate a count-1 key:
  hence the two accuracy rows in `docs/PERFORMANCE.md`, one Zipf, one flat.

`mass` tracks the total weight added, which is what lets the CLI print the
allowance in absolute terms. `merge` adds cellwise, valid only for identical
geometry, and returns a new sketch.

## t-digest

**Quantiles and CDF for a stream, in `O(compression)` memory.**

The structure is a list of weighted centroids `(mean_i, weight_i)` sorted by
mean. A centroid of weight `w` is treated as `w` samples all sitting at its
mean, so the distribution is a step function and rank interpolates linearly
between centroid means.

The merging variant used here keeps a pending buffer of raw points, and when
the buffer reaches `max(64, 2*compression)` entries, folds it into the centroid
list: sort everything, then walk it accumulating a centroid until adding the
next point would exceed the size limit allowed at that position, then start a
new centroid. The limit is

```
k(q) = 4 * n * q * (1 - q) / delta
```

the quadratic scaling function — the derivative of `k1(q) = delta/2 *
asin(2q-1)`, expressed as a mass budget rather than a rank budget. It is
widest at the median and vanishes at the tails, so the tails get individual
centroids (exact) while the middle is allowed to be coarse. The `max(..., 1.0)`
floor means no centroid ever holds less than one unit of mass, which is what
keeps a `delta=20` digest honest at p99.9.

Measured rank error at 21 quantiles from p05 to p100 over a million Gaussian
values: 2.4e-4 max at `compression=20` (175 centroids), 5e-5 at 100 (742),
3e-5 at 500 (3,185). Those are rank errors — `|q_estimated − q_true|` — not
value errors, and a rank error of 2.4e-4 on a stream of a million values is a
couple of samples.

`quantile(q)` finds the target rank `q*n` among centroid **centres** (the
running weight sum plus half the centroid's own weight) and interpolates
between the two neighbouring means; below the first centre it answers the first
mean, above the last it answers the last. `cdf(value)` walks the same step
function backwards, and at a centroid mean it returns that centroid's centre
rank — which is exactly the largest `q` whose `quantile(q)` resolves to that
mean, so the two functions are inverses at the joints as well as in between.
`tests/test_quantile.py` pins that, including on a digest of one value where
`quantile` is flat and only the centre-rank rule makes `cdf` correct.

Both queries rebuild the cumulative centres on the fly, so they are
`O(centroids)` per call with no cache that a flush could invalidate. That is a
deliberate trade: about 48 µs per query at `delta=100` (measured), and a
500×-different answer is what a stale cache would have cost.

`merge(other)` requires the same `compression` and folds the other digest's
centroids in as buffer points, so the result is re-compressed rather than
bit-identical to a single pass — measured as a 4.3e-4 rank gap between "one
stream" and "four shards merged". `add(value, weight)` accepts integer weights,
which behave exactly like that many repeats.

## Welford and Chan

`RunningStats` holds `count`, `mean`, `M2`, `min`, `max` and updates by

```
count += 1
d     = x - mean
mean += d / count
M2   += d * (x - mean)      # note: the NEW mean
```

Variance is `M2 / (count - 1)` (sample) or `M2 / count` (population). The
alternative — accumulating `sum(x)` and `sum(x*x)` and subtracting the square —
catastrophically cancels when the mean is large relative to the spread. On
`1e9 + 1..5` the naive formula reports a variance of 0 or a negative number;
Welford reports 2.5, and `tests/test_moments.py` asserts exactly that.

`combine(a, b)` is Chan's parallel form:

```
d  = mean_b - mean_a
mean = mean_a + d * n_b / (n_a + n_b)
M2   = M2_a + M2_b + d*d * n_a*n_b / (n_a + n_b)
```

which makes the result of merging shards equal to a single pass over the
concatenation (to within float rounding), independent of shard shape. Measured
against `statistics.fmean`/`variance` over a million values: mean error 3.6e-12,
variance error 1.6e-11 — the numbers in the accuracy table are float noise, not
estimator bias, because there is no bias in this one.

## EWMA

`Ewma(alpha)` updates `value += alpha * (x - value)`, which is the exponentially
weighted mean with weights `alpha*(1-alpha)**(n-1-i)`. With `debias=True` (the
default) a running weight sum is tracked instead, `weight += alpha*(1-weight)`,
and the result is divided by it — so the first sample returns the first sample
rather than `alpha * x`. That is the standard bias correction, and at step 1 the
algebra is exact: the debiased value equals the explicit weighted mean. Measured
agreement against that explicit mean over 2,000 samples: 6.7e-16.

`with_span(span)` uses `alpha = 2/(span+1)`, the spreadsheet convention.
With `debias=False` the recurrence is the uncorrected one, which is what you
want if you are reproducing a chart that used it.

## Reservoir sampling

Algorithm R (Vitter): after the pool is full, each incoming item at position
`i` takes a slot uniformly among `1..i`,

```
slot = rng.randrange(seen)      # 0-based: [0, i)
if slot < k: pool[slot] = item
```

which gives every item inclusion probability exactly `k/n` — not asymptotically.
`tests/test_reservoir.py` checks the empirical rate over 1,500 draws at
`n=200, k=20` against `k/n` within 5 standard deviations, and checks that a
head-to-tail comparison does not favour the first `k` items, which is the
classic off-by-one in this algorithm. `sample()` returns a copy, so mutating it
cannot corrupt the reservoir.

The `rng` is injectable (`Reservoir(k, rng=random.Random(42))`) and defaults to
a fresh unseeded generator; seeded, the same stream gives the same sample in
the same order.

## Weighted reservoir sampling

Efraimidis–Spirakis A-Res: give each item the key `u**(1/w)` for `u ~ U(0,1)`
and keep the `k` largest keys. Taking logs, that is the `k` smallest
`priority = -ln(u)/w`, so the heap stores `(-priority, tie, item)` and the
comparator never touches the items — which matters, because items need not be
orderable at all. The `tie` counter is what makes the heap total.

`u == 0.0` is redrawn rather than allowed, since `-ln(0)` is `inf`. A weight of
`inf` yields priority `0`, so an infinite-weight item always wins, and weights
must be `> 0`: a zero-weight item can never be selected, which is a caller
error, not a sample. `k=1` degenerates to the largest-weight item.

## The frame

```
"SFT1" | kind(1) | version(1) | config_len(1) | config | payload_len(4) | payload | crc32(4)
```

All big-endian, `zlib.crc32` over every preceding byte, `config_len` capped at
255 by construction. `kind` is 1/2/3 for HyperLogLog/CountMinSketch/TDigest;
`decode()` checks magic, version, kind and CRC before returning the payload, so
the failure modes stay distinguishable: a wrong-kind buffer says which two
kinds were involved, a corrupt one says checksum, and a truncated one says
truncated.

The config block is geometry only — HLL: 1 byte precision; Count-Min: `depth`,
`width`, `mass` (4+4+8); t-digest: `compression` plus counts. Because the
payload length is declared and checked against the geometry *before* anything
is sized, a frame cannot make the process allocate on its own say-so.

Framing overhead: 16 B for HLL, 31 B for Count-Min, 23 B for the t-digest —
those are the differences between `to_bytes()` and the bare data, and they are
tested as exact numbers rather than described as "small".

## References

* Flajolet, Fusy, Gandouet, Meunier (2007). *HyperLogLog: the analysis of a
  near-optimal cardinality estimation algorithm.*
* Cormode, Muthukrishnan (2005). *An improved data stream summary: the
  count-min sketch and its variations.*
* Dunning, Ertl, Müller (2019). *Computing Extremely Accurate Quantiles Using
  t-Digests.* The scaling-function derivation is in Dunning & Tibshirani (2021).
* Welford (1962); Chan, Golub, LeVeque (1983). *Algorithms for computing the
  sample variance.*
* Vitter (1985). *Random sampling with a reservoir.*
* Efraimidis, Spirakis (2006). *Weighted random sampling with a reservoir.*
