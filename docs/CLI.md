# The `sift` CLI

Five subcommands. Each one reads files or stdin **line by line**, keeps one
sketch in memory, and prints a short aligned report. Everything below was run
against this file, which has 50,000 rows and 250 distinct users:

```console
$ head -3 access.log.csv
id,user,latency_ms
1,u0231,108.7
2,u0020,103.2
```

## Common options

| Option | Applies to | Meaning |
| --- | --- | --- |
| `--column N` | all | use field *N* (1-based) of each row instead of the whole line |
| `--separator S` | all | split on *S* rather than on whitespace |
| `--skip-bad` | all | ignore rows the column or the number parser cannot use |
| `files …` | all | read stdin when empty, or when the name is `-` |

`sift` is strict by default, and that is the behaviour you want when a
measurement is going into a report:

```console
$ sift stats --column 3 --separator , access.log.csv
sift: access.log.csv:1: 'latency_ms' is not a number
$ echo $?
2
```

The header row is row 1, the message names it, and the exit code is 2. Skip it
on purpose with `--skip-bad` and the same command works:

```console
$ sift stats --column 3 --separator , --skip-bad access.log.csv
count   50000
mean    120.035
stdev   30.0388
min     1
max     252.9
```

`stats` uses `RunningStats`, so those numbers are exact for the rows it read —
no approximation anywhere in this subcommand.

## `quantiles`

```console
$ sift quantiles --column 3 --separator , --skip-bad access.log.csv
     q          digest
 0.500         120.019
 0.900         158.826
 0.990         190.421
```

`--qs 0.75,0.95,0.99,0.999` changes the rows printed; `--compression` trades
memory for accuracy (default 100, and the digest holds roughly that many
centroids either way). `--exact` additionally keeps every value so it can
report the rank error it actually achieved:

```console
$ sift quantiles --column 3 --skip-bad --exact access.log.csv
     q          digest  rank error
 0.500         120.019  0.0003
 0.900         158.826  0.0002
 0.990         190.421  0.0001

50000 values held in RAM for --exact; drop the flag to stream
```

That footer is the honest warning: `--exact` defeats the purpose and exists to
let you check the claim on your own data.

## `cardinality`

```console
$ sift cardinality --column 2 --separator , --skip-bad access.log.csv
rows            50001
distinct        247
registers       4096 (4096 bytes)
expected error  1.62%
duplication     99.51% of rows
```

247 against 250 real users, in 4 KiB. `--precision` is log2 of the register
count, 4 to 16 — 16 bytes to 64 KiB — and the error falls as `1.04/sqrt(m)`,
which the report prints for the precision you chose. `duplication` is
`1 - distinct/rows`, the number that tells you whether the column is an id or
a category.

Note that `rows` counts 50001: `--skip-bad` only drops rows the *number*
parser rejects, and for a string column the header is a legitimate value. Use
`--column` on a numeric field, or filter the header in the pipeline, when you
want row counts to mean rows.

## `top`

```console
$ sift top --column 2 --separator , --skip-bad --items 5 access.log.csv
     count      sketch    error  item
       260         260        0  u0080
       245         245        0  u0218
       231         231        0  u0233
       230         230        0  u0123
       228         228        0  u0022

50001 rows, 251 distinct, sketch holds 65256 bytes (2719x3)
```

This is the one subcommand that runs **two passes** — a `dict` pass to rank
the candidates and a sketch pass to score them — so `count` is exact and
`sketch` is what a single-pass Count-Min would have said. `error` is
`sketch - count`, always ≥ 0, because Count-Min never underestimates. The point
of the table is that on a stream that fits in RAM you can see how far the
sketch would have been; on a stream that does not fit, you only get the
`sketch` column.

`--eps` and `--delta` size the sketch the way `with_accuracy` does: `eps` is
the error as a fraction of rows, `delta` the probability of exceeding it.

## `sample`

```console
$ sift sample --seed 7 3 events.csv
# kept 3 of 21
13,u5,87.3
16,u4,143.5
20,u6,120.8
```

Reservoir sampling: uniform over the lines, `k` held, no seek. The `# kept …`
line goes to stderr so stdout stays pipeable.

`k` is the first thing after the subcommand; the file names follow it, and
options may sit on either side:

```console
$ sift sample 3 --seed 7 events.csv
$ sift sample --seed 7 3 events.csv          # same draw
```

`sift` reorders `sample`'s arguments before handing them to `argparse`, because
Python 3.11 will not resume a variadic positional once an option has intervened
(`tests/test_cli.py::TestSample::test_an_option_between_k_and_the_file_works_too`
pins that both spellings give the identical draw).

## Composition

Because everything is line-oriented and stdin is the default, the tool sits in
a pipeline rather than replacing one:

```console
$ cut -f7 access.log | sift cardinality
$ gzcat events.jsonl.gz | jq -r .user_id | sift top --items 20
$ find . -name '*.csv' -exec cat {} + | sift quantiles --column 3 --qs 0.99
```

`--version` prints `sift 0.1.0`. A closed stdout (`sift top … | head -3`) exits
0 rather than printing a traceback, which is what a pager expects.
