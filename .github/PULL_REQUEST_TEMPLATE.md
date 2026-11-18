<!--
Short is fine. A failing test first and a two-line fix after it is the ideal PR here.
-->

## What changes

## Why it is not expressible today

## Verification

- [ ] `PYTHONPATH=src python -m unittest discover -s tests -t .` passes, including the new test
- [ ] `python bench/bench.py --quick` runs clean
- [ ] `pyproject.toml` still has `dependencies = []`, and no test imports anything outside the standard library
- [ ] `python -c "import sift; print(sorted(sift.__all__))"` matches the exports you changed
- [ ] Docs updated: README table, `docs/ALGORITHMS.md`, `CHANGELOG.md`

## If this touches an estimate

State the guarantee you are changing and the row in `bench/bench.py`'s accuracy
suite that measures it. "It seems more accurate" is not a claim.

## If this touches a frame or the hash

- [ ] Round-trip equality still holds: `X.from_bytes(x.to_bytes()) == x`
- [ ] A single edited byte is still rejected
- [ ] The version byte is unchanged, or you have said why it cannot be
- [ ] `tests/test_serial.py` cross-kind and cross-process tests pass on both platforms

## If this touches performance

Paste `python bench/bench.py` before and after, same machine, same terminal,
full mode (not `--quick`). Include the exact-baseline column: the point of the
table is the comparison, not the sketch. No numbers, no merge.
