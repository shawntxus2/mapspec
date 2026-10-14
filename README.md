# mapspec

A declarative spec format and generator that turns a source-to-target mapping into dbt
models, tests, and a coverage report, and proves the generated SQL by running it on two
database engines and failing when they disagree.

Why a spec rather than hand-written SQL: the mapping is reviewable by people who do not read
SQL, and the invariants that make a Type 2 dimension correct get generated every time instead
of remembered by whoever wrote the last one.

## What matters first

**It refuses to generate eight constructs** the schema otherwise accepts. Not because they
are hard, but because it cannot generate them *correctly*, and SQL that looks right and is
wrong is worse than no SQL. For each, the build fails rather than emit the clause that
follows:

- `scd.type: 1` (an overwrite dimension) would have emitted a full **Type 2 history**,
  versioning a dimension declared to overwrite in place.
- `durable_key: crosswalk` would have emitted a **`dense_rank` sequence**, which re-keys on
  every insert instead of the stable crosswalk that was asked for.
- `durable_key: hash_natural_key` would have emitted that **same `dense_rank` sequence**,
  not a reproducible hash of the natural key.
- `survivorship: most_recent` would have emitted **precedence `coalesce`**, so "most recent
  source wins" silently becomes "first in precedence order wins."
- `survivorship: most_complete` would have emitted **precedence `coalesce`**, not the most
  complete value.
- `survivorship: aggregate` would have emitted **precedence `coalesce`**, not an aggregate.
- `survivorship: manual` would have emitted **precedence `coalesce`**, not the manual
  resolution.
- `cast on_error: sentinel` would have emitted a cast that **raises**, since no sentinel
  substitution is implemented.

A ninth, `cast on_error: null`, is generated on DuckDB (via `try_cast`) but refused on
PostgreSQL, which has no `try_cast` and no clean guard, so a bad value would raise instead of
nulling.

**The same spec is built on two engines and the build fails when the results differ.**
DuckDB and PostgreSQL build every acceptance model; the tables are compared row by row, and a
difference is either a defect to fix or a platform difference to declare with a reason, never
waved through. On the first run with seed data placed on a year boundary, the differential
caught three things:

- **A translation defect:** `date_diff('year', a, b)` was being transpiled to
  `EXTRACT(year FROM AGE(b, a))` (elapsed years, not the year-boundary count), and that wrong
  value fed a Type 2 record hash. Fixed with a targeted rewrite.
- **A design mistake:** a special-member label too long for its column was truncated to fit,
  a value that looks real and is not. It is now nulled instead.
- **A genuine platform difference:** `dim_security.dw_load_ts` is `current_timestamp` at
  build time, so it differs by wall clock and session time zone. It is load metadata, so it
  is declared and excluded from the comparison.

## Coverage

Every construct the schema declares has a recorded status. The four counts, on DuckDB, the
canonical engine:

- **verified: 44** — reached by an acceptance spec and observed to work.
- **unverified: 10** — written, but never reached by a test, so not yet observed to work.
- **declared, not generated: 16** — the schema allows it and the emitter stubs or ignores it.
- **substituted: 8** — it fails the build rather than emit something plausible and wrong (the
  eight above). On PostgreSQL this is **9**: `cast on_error: null` is substituted there too.

## Quick start

```bash
git clone https://github.com/shawntxus2/mapspec.git
cd mapspec
make setup     # one venv: dbt + duckdb + sqlglot + psycopg2, then dbt deps
make check     # validate -> emit -> staleness -> graph -> accept (DuckDB) -> accept (PostgreSQL) -> tier-2 parse
```

`make check` is green when every stage passes. The PostgreSQL stage brings up a throwaway
Docker container; without Docker it skips rather than fails, so the suite is unverified on
that engine, not blocked. `make dbt` runs a real dbt build on DuckDB.

## How it works

- You write a **spec**: a YAML mapping of sources to one target table (dimension or fact),
  with its grain, keys, SCD behavior, and a transform per column.
- **`validate.py`** checks it against the JSON schema and about thirty semantic rules.
- **`emit.py`** generates the dbt models, tests, landing DDL, and `coverage.md`. `--platform`
  transpiles expression-level SQL through sqlglot; DuckDB is the canonical dialect, emitted
  verbatim. The change hash and the calendar/time generators come from the platform registry,
  never from sqlglot, so a transpile cannot silently re-version the estate.
- **`graph.py`** writes a lineage graph and a self-contained review page (`ui/review.html`).

The generated dbt project under `dbt/` is committed so the SQL can be read here without
cloning; `make check` re-emits it and fails if the committed output is stale, which keeps the
committed SQL and the "this SQL is disposable" rule from drifting apart.

## What it does not do

- **`attribute_timelines`** (merging change points across independent histories) is declared,
  validated, and deliberately left for last; it is the hardest piece.
- The **identity-crosswalk** and **model-signature-registry** generators are stubs that say
  so, as are the spreadsheet importer and the warehouse pre-flight check.
- **Tier-2 platforms** (Snowflake, SQL Server, Oracle) are only parse-checked, never executed;
  DB2 has no sqlglot dialect and is not even parsed. A parse is not a proof.
- **Nobody but the author has run it.** The confidence here comes from the two-engine
  differential, not from adoption.
