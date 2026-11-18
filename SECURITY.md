# Security policy

## What this library is for

`HyperLogLog`, `CountMinSketch`, `TDigest`, `Reservoir` and `RunningStats` all
take untrusted items from a stream and answer questions about them in fixed
memory. The threat model is therefore "the keys are chosen by someone else, the
geometry is chosen by you".

## What counts as a vulnerability here

- A frame from `from_bytes()` that allocates memory proportional to what the
  buffer *claims* rather than what it contains. The three serialisable
  structures (`HyperLogLog`, `CountMinSketch`, `TDigest`) each compare the
  declared geometry against the bytes actually present before any counter or
  centroid array is sized — a crafted `CountMinSketch` frame claiming
  `width = 2**31` is rejected with `FrameError`, not allocated. Tests in
  `tests/test_limits.py` pin it; if you can break that, it is a bug.
- Unbounded growth from `add()`: the sketch sizes are fixed at construction and
  the t-digest buffer flushes at `max(64, 2 * compression)`. A stream that makes
  one of these structures grow without bound is a bug.
- A panic (traceback) escaping into a caller that passed a well-typed value the
  docs say is accepted. The library raises `TypeError`/`ValueError` for what it
  rejects and nothing else.
- In the CLI: reading a file the user did not name, spawning a shell, or
  buffering a whole input in memory. It reads line by line and execs nothing.

Not in scope:

- **Sketches are not tamper-proof.** `CountMinSketch.query()` is an upper bound,
  and an attacker who controls both the keys and knows your `width`/`depth` can
  aim collisions at a cell to inflate a specific estimate. That is arithmetic,
  not a flaw in this implementation: never use a sketch as an authorisation
  check. The fixed `blake2b` key exists so that a sketch written today still
  means the same thing tomorrow, not to keep anyone from predicting cell
  positions.
- What the calling application does with the values it streams in.
- Timing side channels on hashed keys; `hyperloglog++`-style privacy is not a
  goal of this project.

## Reporting

Open a **private** security advisory on the repository rather than a public
issue:
[Report a vulnerability](https://github.com/fj-8/sift/security/advisories/new).

If you would rather not use GitHub, mail the maintainer address shown on the
profile. Expect an acknowledgement within a week; this is a side project, so the
fix may take longer than a company SLA, but the disclosure timeline is yours to
set and will be respected.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | ✅ |
| < 0.1 | ❌ — nothing earlier was published |

## Patch and disclosure

A fix lands on `main` first, is tagged as a patch release, and the advisory is
coordinated with you before the release notes describe the class of bug. If the
issue needs no private handling, a normal issue and pull request are welcome
instead.
