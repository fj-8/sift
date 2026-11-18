"""The ``sift`` command. SPDX-License-Identifier: MIT OR Apache-2.0.

Each subcommand reads its input line by line and keeps only a sketch, so the
memory it uses does not depend on how big the file is. That is the entire reason
this CLI exists rather than being a wrapper around ``sort | awk``.

    sift cardinality access.log --column 7
    sift quantiles    latencies.txt --qs 0.5,0.9,0.99
    sift stats        latencies.txt
    sift sample 20    --seed 7 < access.log
"""

from __future__ import annotations

import argparse
import contextlib
import random
import sys

from . import __version__
from .cm import CountMinSketch
from .hll import HyperLogLog
from .moments import RunningStats
from .quantile import TDigest
from .reservoir import Reservoir


class BadInput(Exception):
    """A line could not be used. Carries the message the user should see."""


def _fields(line: str, separator: str | None) -> list[str]:
    if separator is None:
        return line.split()
    return line.split(separator)


def iter_values(args: argparse.Namespace, *, numeric: bool):
    """Yield one item per input line, honouring --column and --skip-bad."""
    for name in args.files or [""]:
        # "-" is the conventional name for stdin, "" the implicit one; both read
        # the pipe, and neither is ever closed out from under the caller.
        path = "" if name == "-" else name
        # nullcontext(sys.stdin) so a pipe is never closed, while a real file is.
        manager = open(path, encoding="utf-8", errors="replace") if path else contextlib.nullcontext(sys.stdin)
        with manager as stream:
            for number, line in enumerate(stream, 1):
                line = line.rstrip("\n")
                if args.column is None:
                    item: object = line
                else:
                    parts = _fields(line, args.separator)
                    if not parts or args.column > len(parts):
                        if args.skip_bad:
                            continue
                        where = f"{path}:{number}" if path else f"line {number}"
                        raise BadInput(f"{where}: expected column {args.column}, row has {len(parts)} fields")
                    item = parts[args.column - 1]
                if not numeric:
                    yield item
                    continue
                try:
                    yield float(item)
                except (TypeError, ValueError):
                    if args.skip_bad:
                        continue
                    where = f"{path}:{number}" if path else f"line {number}"
                    raise BadInput(f"{where}: {item!r} is not a number") from None


def _add_common(parser: argparse.ArgumentParser, *, numeric: bool) -> None:
    parser.add_argument("files", nargs="*", help="input files (default: stdin)")
    parser.add_argument(
        "--column",
        type=int,
        default=None,
        help="1-based whitespace-separated field to use (default: whole line)",
    )
    parser.add_argument("--separator", default=None, help="field separator instead of whitespace")
    # Always available: --skip-bad covers both a missing --column and a
    # non-numeric row, and the column case exists for text subcommands too.
    parser.add_argument(
        "--skip-bad",
        action="store_true",
        help="ignore rows that --column or the number parser cannot use",
    )


def cmd_cardinality(args: argparse.Namespace) -> int:
    sketch = HyperLogLog(args.precision)
    seen = 0
    for item in iter_values(args, numeric=False):
        sketch.add(item)
        seen += 1
    estimate = sketch.count()
    print(f"rows            {seen}")
    print(f"distinct        {estimate}")
    print(f"registers       {sketch.register_count} ({sketch.memory_usage()} bytes)")
    print(f"expected error  {sketch.standard_error:.2%}")
    if seen and estimate:
        print(f"duplication     {1.0 - estimate / seen:.2%} of rows")
    return 0


def cmd_top(args: argparse.Namespace) -> int:
    sketch = CountMinSketch.with_accuracy(args.eps, args.delta)
    exact: dict[object, int] = {}
    rows = 0
    for item in iter_values(args, numeric=False):
        sketch.add(item)
        rows += 1
        # The heap over the true counts is deliberately exact here: this command
        # is a diagnostic for a file you have, not a production stream path.
        exact[item] = exact.get(item, 0) + 1
    ranked = sorted(exact, key=lambda key: (-exact[key], str(key)))[: args.items]
    print(f"{'count':>10}  {'sketch':>10}  {'error':>7}  item")
    for item in ranked:
        estimate = sketch.query(item)
        error = estimate - exact[item]
        label = item if isinstance(item, str) else str(item)
        print(f"{exact[item]:>10}  {estimate:>10}  {error:>7}  {label[:60]}")
    print(
        f"\n{rows} rows, {len(exact)} distinct, sketch holds {sketch.memory_usage()} bytes "
        f"({sketch.width}x{sketch.depth})"
    )
    return 0


def cmd_quantiles(args: argparse.Namespace) -> int:
    digest = TDigest(args.compression)
    exact: list[float] = []
    for value in iter_values(args, numeric=True):
        digest.add(value)
        if args.exact:
            exact.append(value)
    if args.exact:
        exact.sort()
    qs = []
    for part in args.qs.split(","):
        part = part.strip()
        if not part:
            continue
        q = float(part)
        if not 0.0 <= q <= 1.0:
            raise BadInput(f"--qs takes fractions in [0, 1], got {part}")
        qs.append(q)
    print(f"{'q':>6}  {'digest':>14}" + ("  rank error" if args.exact else ""))
    for q in qs:
        estimate = digest.quantile(q)
        note = ""
        if args.exact and exact:
            index = min(len(exact) - 1, int(q * (len(exact) - 1) + 0.5))
            truth = exact[index]
            rank = digest.cdf(truth)
            note = f"  {abs(rank - q):.4f}"
        print(f"{q:>6.3f}  {estimate:>14.6g}{note}")
    if args.exact:
        print(f"\n{len(exact)} values held in RAM for --exact; drop the flag to stream")
    else:
        print(f"\n{len(digest)} values, {digest.centroid_count} centroids")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    stats = RunningStats()
    for value in iter_values(args, numeric=True):
        stats.add(value)
    if stats.count == 0:
        raise BadInput("no numeric rows")
    print(f"count   {stats.count}")
    print(f"mean    {stats.mean:.6g}")
    print(f"stdev   {stats.stdev:.6g}" if stats.count > 1 else "stdev   (needs 2+ rows)")
    print(f"min     {stats.min:.6g}")
    print(f"max     {stats.max:.6g}")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    pool = Reservoir(args.count, rng=rng)
    for item in iter_values(args, numeric=False):
        pool.add(item)
    for item in pool.sample():
        print(item)
    print(f"# kept {len(pool)} of {pool.seen}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sift",
        description="Fixed-memory statistics over streams too big to hold.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Every subcommand reads stdin when given no file, so these compose:\n"
            "  cut -f7 access.log | sift cardinality\n"
            "  cat *.csv | sift quantiles --column 3 --qs 0.95"
        ),
    )
    parser.add_argument("--version", action="version", version=f"sift {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_card = sub.add_parser("cardinality", help="roughly how many distinct values")
    _add_common(p_card, numeric=False)
    p_card.add_argument(
        "--precision", type=int, default=12, help="log2 of register count, 4..16 (default 12)"
    )
    p_card.set_defaults(func=cmd_cardinality)

    p_top = sub.add_parser("top", help="most frequent values, with the sketch estimate shown")
    _add_common(p_top, numeric=False)
    p_top.add_argument("--items", type=int, default=10, help="how many to print (default 10)")
    p_top.add_argument("--eps", type=float, default=1e-3, help="error as a fraction of rows")
    p_top.add_argument("--delta", type=float, default=0.05, help="failure probability")
    p_top.set_defaults(func=cmd_top)

    p_q = sub.add_parser("quantiles", help="p50/p90/p99 without sorting the file")
    _add_common(p_q, numeric=True)
    p_q.add_argument("--compression", type=float, default=100.0, help="bigger = closer (default 100)")
    p_q.add_argument("--qs", default="0.5,0.9,0.99", help="comma-separated fractions")
    p_q.add_argument(
        "--exact",
        action="store_true",
        help="also keep every value to report rank error (defeats the point; for checking)",
    )
    p_q.set_defaults(func=cmd_quantiles)

    p_s = sub.add_parser("stats", help="count, mean, stdev, min, max")
    _add_common(p_s, numeric=True)
    p_s.set_defaults(func=cmd_stats)

    p_sam = sub.add_parser(
        "sample",
        help="a fair k-subset of the lines",
        description=(
            "Sample k lines. k comes first, then the file names; options may go "
            "on either side of them."
        ),
    )
    p_sam.add_argument("count", type=int, help="k")
    _add_common(p_sam, numeric=False)
    p_sam.add_argument("--seed", type=int, default=None, help="make the draw reproducible")
    p_sam.set_defaults(func=cmd_sample)
    return parser


_SAMPLE_VALUE_OPTS = frozenset({"--column", "--separator", "--seed"})


def _reorder_sample(argv: list[str]) -> list[str]:
    """`sample` takes k and then the files, and Python 3.11's argparse will not
    resume a variadic positional once an option has intervened, so `sample 5
    --seed 7 f.csv` dies with "the following arguments are required: files".
    Move the positionals in front of the options ourselves. An option's value
    travels with the option, or `--seed 7` becomes a file named "7"."""
    rest, positionals, options = argv[1:], [], []
    index = 0
    while index < len(rest):
        token = rest[index]
        if token.startswith("-") and token != "-":
            options.append(token)
            if token in _SAMPLE_VALUE_OPTS and index + 1 < len(rest):
                options.append(rest[index + 1])
                index += 1
        else:
            positionals.append(token)
        index += 1
    if not options or not positionals:
        return argv
    return [argv[0], *positionals, *options]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) > 1 and argv[0] == "sample":
        argv = _reorder_sample(argv)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BadInput as exc:
        print(f"sift: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # `| head` closing the pipe is not an error
        return 0
    except (ValueError, TypeError) as exc:
        print(f"sift: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # A missing or unreadable input file is the user's to fix, not a crash.
        print(f"sift: {exc.strerror or exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
