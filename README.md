# mapspec

Turn a data model into dbt. Review it before you generate it.

`mapspec` reads a declarative source-to-target mapping and emits dbt sources, staging
models, dimensions, facts and tests. Anything it cannot express as typed SQL is reported
as unconverted rather than quietly approximated.

**It reads the model, not the warehouse.** Tools that reverse engineer an existing
warehouse can only learn what already arrived there. Reading the design means the tests
are a restatement of constraints the modeler already declared, and precision loss can be
caught before anything moves.

---

## Where it is

The format, the validator and the DDL reader. No SQL generator yet.

```
schema/mapspec.schema.json   the format
registry/platforms.yml       what each platform and transport can actually carry
models/                      nine worked models exercising every feature
ddl/                         sample DDL in two dialects
ddl_reader.py                DDL in, skeleton spec out
validate.py                  schema plus the semantic checks schema cannot express
graph.py                     lineage JSON, and the self-contained review page
ui/index.html                the review page template
emit.py                      dbt models, tests and the coverage report
acceptance/                  seeds, fixture specs and the assertion suite
dbt/                         the runnable dbt project. models/ is generated.
Makefile                     the whole loop
CLAUDE.md                    conventions, and why each one exists
```

## Running it

Linux or macOS, Python 3.10 or later. arm64 is fine; every dependency has aarch64 wheels.

```
make setup     create the venv, install dbt-core, dbt-duckdb, duckdb, sqlglot, dbt deps
make check     validate -> emit -> graph -> acceptance. Stops on the first red.
make dbt       emit the acceptance specs and run a real dbt build on DuckDB
```

dbt pins its dependencies hard, so `make setup` builds a dedicated virtualenv. Do not
install it into an environment that also carries PyTorch or anything else with a large
transitive tree.

The reference specs under `models/` point at illustrative source URIs, so they are
validated, emitted and graphed but not executed. The `acceptance/models/` specs point at
real seed files, and those are what `make dbt` builds.

```
$ python3 validate.py
mapspec validate - 9 models
========================================================================
WARN  dim_customer           cols=15  generatable=73.3%  unresolved=4
PASS  dim_customer_xref      cols=0   generatable=100.0%
PASS  dim_date               cols=0   generatable=100.0%
WARN  dim_instrument_ref     cols=5   generatable=80.0%  unresolved=1
PASS  dim_security           cols=9   generatable=88.9%  unresolved=1
PASS  dim_time               cols=0   generatable=100.0%
PASS  fct_positions          cols=9   generatable=88.9%  unresolved=1
PASS  meta_model_signature   cols=0   generatable=100.0%
PASS  stg_orders             cols=13  generatable=92.3%  unresolved=1
========================================================================
0 errors, 2 warnings
```

---

## A mapping sheet is not a mapping spec

Most source-to-target documents are a list of column pairs and an English rule. You
cannot generate SQL from that, because it never says how the source rows join or what one
row of the target represents.

So a spec has two halves. A model half declaring grain, sources and joins. A column half
declaring where each target column comes from. The second is what people think of as the
mapping. The first is what makes it executable.

## Everything is typed except one thing

`transform.kind` is a closed set of nine.

| kind | what it does |
|---|---|
| `direct` | copy a column |
| `cast` | type conversion with format, rounding and error policy |
| `expression` | authored SQL, transpiled across dialects |
| `conditional` | case logic |
| `lookup` | point-in-time dimension key resolution |
| `aggregate` | a function over a declared grain |
| `constant` | a literal |
| `survived` | value chosen across sources by survivorship rules |
| `unresolved` | the English rule, and no guess |

`unresolved` is a first class citizen. It emits a `-- TODO`, counts against coverage and
renders red in review. The converter never invents a transformation it was not given.

`cast` is separate from `expression` on purpose. It is most of a staging layer, it
generates without anyone authoring SQL, it transpiles cleanly, and it is checkable for
precision loss. Date format masks are the single most dialect-specific thing in any
mapping.

## Tests are derived, not authored

| declared in the spec | generated dbt test |
|---|---|
| `nullable: false` | `not_null` |
| `role: primary_key` | `unique` |
| `references:` | `relationships` |
| `allowed_values:` | `accepted_values` |
| `grain:` | unique combination of columns |

Type-2 dimensions additionally generate their own integrity tests from the SCD contract:
no overlapping versions, no gaps, `valid_to` strictly greater than `valid_from`, exactly
one open version per natural key.

---

## Type-2 is where mapping tools usually stop

The SCD contract belongs to the dimension, declared once. A fact's lookup names the
dimension and inherits the model's single `as_of`. It never restates the validity columns,
the boundary rule or the sentinel, so twenty facts cannot drift apart.

**Effectivity is `timestamp(3)` holding UTC, everywhere, with half open `[from, to)`
intervals.** Millisecond precision because dimensions change intraday and more than once.
Half open because version N's `valid_to` then equals version N+1's `valid_from` exactly,
with no subtract-one-millisecond fudge, which is the most common Type-2 bug.

The open sentinel is a real high value rather than null, because `coalesce` in a join
predicate is a function on the join column and can defeat partition pruning on every fact
in the estate.

### Three ways a dimension gets its history

- **`change_detection`** compares incoming to current and opens a version when a tracked
  column moves.
- **`source_provided`** preserves effectivity the source already carries. When only a
  start date arrives, `source_effective_to: { derive: lead }` generates the window
  function everyone otherwise hand writes slightly differently.
- **`attribute_timelines`** handles an entity carrying several independent histories:
  nested arrays, or denormalized repeating date and code column groups off a mainframe
  extract. The generator unions the distinct change instants across every timeline, as-of
  joins each one back to that set, then closes the versions over the merged points.
  Exploding them independently produces a cartesian product that looks plausible and is
  wrong, which is why the relationship has to be declared rather than inferred.

`before_first` is required on every timeline and has no default. An entity known from 2019
whose rating history starts in 2022 has three years where the answer is a decision.

### Late arriving data

A change effective inside an already closed interval shortens the preceding version's
`valid_to` and inserts a new version between them. The surrounding versions keep their
keys, because v1's `valid_from` never moved, so no fact is orphaned.

Facts already loaded in the reopened window still point at the superseded version.
`affected_facts` decides whether that is reported or restated. That is a governance
decision, so it is declared rather than defaulted.

---

## Durable keys

Three different keys, and conflating any two breaks something.

- **Surrogate key.** One per version row.
- **Durable key.** One per entity, for life.
- **Natural key.** The source system's identifier, and the thing that actually changes.

A retail customer moves state and changes her name on marriage. Type-2 handles the
attributes. The durable key handles what Type-2 cannot: the source reissuing her
identifier, or identity resolution later finding two records are one person.

So a durable key is **assigned in a crosswalk, never hashed from the natural key**. The
validator refuses `strategy: hash_natural_key` on a dimension declared
`natural_key_stability: unstable`, because that combination is the bug the durable key
exists to prevent.

Facts carry both keys. Distinct customer counts use the durable key; counting distinct on
the surrogate counts versions, so a customer who moved and married counts three times.

**Matching is out of scope.** Deterministic rules, probabilistic scoring and steward
review belong to another system. This tool consumes the crosswalk and generates everything
downstream of it.

## Survivorship is per attribute

CRM has the best name, billing has the authoritative state, the web profile has the newest
email and the worst name. Picking a winning record throws away the good columns on the
losing ones. Six strategies: `source_precedence`, `most_recent`, `specific_source`,
`most_complete`, `aggregate`, `manual`.

Each system versions on its own schedule, so a conformed multi-source dimension is just
`attribute_timelines` with the timelines coming from different systems. Same change point
merge, no second engine.

---

## Precision is checked across platforms, not just within one

Every column's declared path is traced:

```
source platform type -> transport -> serialization -> landing -> target platform type
```

A hop that cannot represent the declared precision is an error. A hop that can but is
configured not to is a warning.

What this catches that a column name comparison cannot:

- **JSON numbers are doubles.** About fifteen reliable significant digits. A
  `numeric(28,8)` crossing a JSON hop as a number loses precision before it reaches the
  warehouse and nothing errors. The fix is `as_text: true` on the projection.
- **Oracle `DATE` is not ANSI `DATE`.** It is a date plus a time to the second. Mapping it
  to another platform's `DATE` silently discards the time. Types are therefore normalized
  from platform-qualified names, never compared as bare keywords.
- **SQL Server `DATETIME` rounds to roughly 3.33 milliseconds.** Millisecond effectivity
  routed through it is no longer millisecond effectivity.
- **DB2 caps `DECIMAL` at 31 digits, not 38.**
- **The format mask is the other half.** A capable platform plus a mask with no fractional
  seconds still gives you whole seconds.

Entries marked `verify: true` in the registry are the ones to confirm against current
vendor documentation before they fail someone's build.

## Time zones

UTC is the stored instant, because half open interval math needs a monotonic line and
local wall clock is not one. During the fall back hour the same local time occurs twice.

But UTC alone is not sufficient, so three things are stored:

- the **instant**, for ordering, joins and durations
- the **zone or offset**, to reconstruct what was meant
- the **business date**, stored rather than derived

An offset reconstructs the past exactly. Future-dated effectivity needs the IANA zone id,
because the UTC instant has to be recomputed if a government changes the rules.
`tzdata_version` is recorded so a historical conversion is reproducible years later.

Ambiguous and nonexistent local times default to `error`. There is no correct guess.

## Date and time dimensions

Both are generated, not mapped. `dim_date` is deliberately geography free, because
holidays differ by country and exchange and a single `is_holiday` column would be wrong
for most of a global estate. `dim_time` is second grain and deliberately zone naive, which
is what lets a fact role-play it twice.

A fact declares `temporal_keys` once and gets six columns:

```yaml
emit: [utc_date_key, utc_time_key, local_date_key, local_time_key,
       utc_offset_minutes, business_date_key]
```

The zone is resolved at load. Comparing 9am to 11am local across Sydney, Hong Kong,
London, New York and San Francisco then becomes a filter on `local_time_key`. No
conversion in the query, and no fixed offset arithmetic, because those five zones do not
shift together. `utc_offset_minutes` is carried because on the fall back hour two
instants share one local key and nothing else separates them.

Durations come from the UTC instant. A duration derived from local keys is wrong by an
hour whenever it crosses a transition.

---

## read-ddl

Point it at the DDL for a dimension or a fact and it writes a skeleton spec.

```
$ python3 ddl_reader.py ddl/dim_product.oracle.sql --dialect oracle --out models

ddl/dim_product.oracle.sql  ->  models/dim_product.skeleton.yml
  11 mappable columns, all unresolved
  lifted    valid_from -> scd.effective_from
  lifted    valid_to -> scd.effective_to
  lifted    is_current -> scd.current_flag
  lifted    version_no -> scd.version_number
  lifted    record_hash -> scd.change_hash
  lifted    attribute_hash -> scd.attribute_hash
  lifted    dw_load_ts -> scd.audit.loaded_at
  lifted    dw_source_system -> scd.audit.source_system
  inferred  layer 'dimension' from the table name prefix
  inferred  product_key: number(10) has scale 0, normalized to bigint
  WARNING   launch_date: Oracle DATE normalized to timestamp(0), not date. Oracle DATE
            carries a time to the second; mapping it to a target DATE would discard it
            silently.
  WARNING   no natural key stated in the DDL. The unique constraints describe the version
            grain, not the business identifier, so natural_key is left for you to declare.
```

**Every transform comes out `unresolved`.** The reader states what the DDL states and
guesses no mappings, so coverage starts at zero and climbs as you fill transforms in.
That number is the point.

```
$ python3 validate.py --models models
WARN  dim_product.skeleton   cols=11  generatable=0.0%   unresolved=11
      WARN [KEY020] surrogate_key product_key declares no cardinality
      WARN [HASH005] change_hash is declared with no hash_policy

... six transforms later ...

WARN  dim_product.filled     cols=11  generatable=54.5%  unresolved=5
```

A skeleton validates clean with warnings that read as a to-do list, which is the behavior
you want from a tool that refuses to guess.

### Three things it does that a naive reader does not

**Type 2 system columns are lifted, not mapped.** Nineteen columns of DDL produced eleven
mappable columns. The other eight are `valid_from`, `valid_to`, `is_current`, `version_no`,
the two hashes and the `dw_` audit pair, and they belong in the SCD contract. Emitting them
as mappable rows is how a mapping sheet ends up with twelve lines nobody ever fills in.

**Types are normalized platform-qualified.** Oracle `DATE` lands as `timestamp(0)`, not
`date`, because Oracle `DATE` carries a time to the second. A reader that trusts the
keyword silently discards it. `NUMBER(10)` with no scale becomes an integer rather than a
decimal, and the reader says so.

**It reports what it guessed.** Layer from the table prefix, natural key from a unique
constraint, scale-zero numerics. Each inference is printed so it can be corrected, and
anything it could not infer is a warning rather than a silent omission.

On the sample dimension that last one earns its keep. The unique constraint is on
`(product_durable_key, valid_from)`, which states the version grain and not the business
identifier, so the reader refuses to call `product_id` the natural key just because it
looks like one.

### What it carries across

`CHECK (col IN (...))` becomes `allowed_values`, which becomes an `accepted_values` test.
`PRIMARY KEY` becomes the grain on a fact and the surrogate key on a dimension. `FOREIGN
KEY` becomes `references`, which becomes a `relationships` test. `NOT NULL` becomes
`nullable: false`, which becomes a `not_null` test.

None of that is inference. It is the constraints the modeler already declared, carried
through to tests rather than rediscovered from data.

---

## Review before generate

```
$ python3 graph.py
ui/graph.json
  18 nodes, 12 links
  51 columns, 8 unresolved, 84.3% generatable
  ui/review.html  (30 KB, self contained)
```

The graph emitter is deliberately separate from the generator. The page depends on this
JSON and on nothing else, so review works before any SQL exists and keeps working if the
emitter is rewritten.

**The diagram is entity level.** Nodes are sources, then staging, then crosswalks,
dimensions and facts. Ribbon width is the number of column mappings carried, and a ribbon
that carries any unresolved column is drawn in the open colour so the gaps are visible
before you click anything. Click a ribbon and the column grid below fills with the columns
behind it: name, type, transform kind, and the actual source expression. For an unresolved
column it shows the English rule and who owns the answer.

A column-level diagram would be unreadable. Forty columns from eight sources is forty
ribbons crossing a canvas, and you cannot follow one. Entity level with drill-in is
readable and still shows what review needs: which sources nothing consumes, which targets
have suspiciously thin inputs, and where the open items are concentrated.

It is a Sankey-shaped lineage diagram rather than a true Sankey. Flow is not conserved:
one source column can feed several targets and a target column can have no source at all.
Worth saying before someone else does.

### Colour

Three families plus one status, and the exact transform kind is written beside every
swatch, so identity never rests on colour alone.

| | |
|---|---|
| **Direct move** | `direct` |
| **Computed** | `cast`, `expression`, `conditional`, `aggregate`, `constant` |
| **Resolved against another model** | `lookup`, `survived`, `timeline`, `durable_key` |
| **Unresolved** | the status colour, reserved, never used for a category |

The palette was validated for colour-vision separation and surface contrast in both light
and dark, all pairs, rather than chosen by eye. The first combination tried failed: the
categorical orange and the status red came out at a normal-vision delta of 10.8, well under
the floor, which is exactly the kind of thing that looks fine to the person who picked it.

---

## emit

```
$ python3 emit.py
  skip dim_customer             1 file(s)
  skip dim_customer_xref        1 file(s)
  ok   dim_date                 2 file(s)
  skip dim_instrument_ref       1 file(s)
  ok   dim_security             2 file(s), 1 TODO
  ok   dim_time                 2 file(s)
  ok   fct_positions            2 file(s), 1 TODO
  skip meta_model_signature     1 file(s)
  ok   stg_orders               2 file(s), 1 TODO
  coverage: 28/51 columns generated -> build/coverage.md
```

An unresolved column emits a `-- TODO` carrying the English rule and the owner, then a
typed null. A derivation the emitter does not implement yet writes a stub that says so.
The coverage report accounts for every column it did not write, and `attribute_timelines`
is named there as declared, validated and not generated, rather than quietly missing.

**Landing is emitted separately and labelled.** dbt transforms; it does not ingest. The
external table or stage DDL for a file source goes in `landing/`, outside the models, so
nothing pretends dbt did the loading.

### Acceptance

dbt would not install in the environment this was built in, so the suite resolves `ref()`
and `source()` itself and runs the generated SQL directly on DuckDB. That proves the SQL,
which is the part mapspec writes. Orchestration, materializations and the test runner are
dbt's and are not exercised.

```
$ python3 acceptance/run.py
built 4 model(s): dim_date, dim_security, dim_time, fct_holdings

Type 2 integrity, derived from the SCD contract
  PASS  no overlapping versions
  PASS  no gaps between versions
  PASS  valid_to strictly after valid_from
  PASS  exactly one open version per key
  PASS  durable key stable across versions
  PASS  content hash differs between versions

Point in time resolution, as_of_promotion: end_of_day
  PASS  before the change resolves to the earlier version      INDU
  PASS  after the change resolves to the later version         INFT
  PASS  on the change date, end of day takes the new version   INFT
  PASS  no version covering the date falls to the unknown member

Derived from declared constraints
  PASS  accepted_values holds for sector_code
  PASS  relationships hold to dim_security
  PASS  unresolved column emitted as a typed null, not a guess

21 passed, 0 failed
```

The seed holds a security whose sector changes on 2026-04-15 and whose rating changes
again on 2026-09-01, with holdings on either side and one on the change date itself. The
boundary case is the point: with `[from, to)` and `end_of_day`, a holding dated on the day
of the change picks up the new version, and a holding predating the first version falls to
the unknown member rather than to a null nobody notices.

### Three bugs the acceptance run found

**The content hash used a sentinel string for null.** A literal equal to the sentinel
would hash identically to a null, and the default sentinel contained a NUL byte, which
cannot live in a SQL literal. Nullness is now encoded structurally: `N:` for null, `E:`
for empty, `L<len>:<value>` otherwise. No literal can impersonate a null.

**`source_timestamp_promotion` was declared and ignored.** The effective date arrived as
text and went into `valid_from` uncast, so every interval comparison was string
comparison. Now promoted as declared.

**The time dimension divided with `/`.** DuckDB's `/` is true division, so `printf('%02d')`
got a float and the model would not build. Integer division throughout.

None of those would have surfaced from reading the generated SQL.

---

## Keys are integers, sized to cardinality

Surrogate keys are sequences, not hashes. The usual argument for a hash is that you can
compute the foreign key without a lookup, and that dies on contact with Type 2: resolving
a point-in-time key means finding the version in force at the as-of instant, which is a
join either way. Once the row is in hand, taking its integer is free.

`strategy: hash` survives for one case, assigning keys in parallel with no central
sequence, and the validator asks you to say so.

```yaml
surrogate_key:
  name: customer_key
  strategy: sequence
  cardinality: { entities: 50000000, avg_versions: 6 }
  type: int
  reserved_members: -8
```

| cardinality | type | examples |
|---|---|---|
| under 32,767 | `smallint` | country, currency, state, exchange, sector |
| under 2.1 billion | `int` | nearly every real dimension |
| beyond | `bigint` | high churn Type 2 on a very large entity set |

A country dimension holds a few hundred rows. A `bigint` there is four wasted bytes on
every fact row that joins to it, and it tells the next reader the cardinality is unknown,
which is the opposite of what the model knows.

On a columnar platform most of the raw storage arithmetic is absorbed by compression. The
width is fully paid in join and hash table memory, on row stores, in intermediate results
and in transit. A varchar key against an integer key is not a rounding error in any of
those places.

The validator refuses a type that cannot hold `entities * avg_versions` with 30 percent
headroom, warns when a type is wider than the cardinality needs, and errors when a fact's
foreign key type does not match the dimension's surrogate type. That last one is invisible
in a mapping sheet and expensive in a query plan.

## Hashing is for content, not keys

Two hashes, over different column sets, both computed **after survivorship**. Hashing raw
source values means a change in a losing source opens a version carrying identical
surviving values.

- **`change_hash`** over Type 2 columns only. Decides whether to open a version.
- **`attribute_hash`** over everything maintained in place. Decides whether to update.
- **Type 0 columns are in neither.** They are asserted, not compared.

Stored as lowercase hex rather than binary. The hash lives only on dimension rows, never
on a fact, so readability during a 2am reconciliation beats width.

The normalization contract is where this silently breaks, so it is declared rather than
assumed:

```yaml
hash_policy:
  algorithm: sha256
  storage: hex
  hex_case: lower          # Snowflake returns lower, SQL Server upper with an 0x prefix
  strip_prefix: true
  framing: length_prefix   # without it, ['AB','C'] and ['A','BC'] hash identically
  null_sentinel: "\u0000NULL"
  empty_sentinel: "\u0000EMPTY"
  numeric: cast_to_declared_scale    # 1.1, 1.10 and 1.100 are one number, three strings
  timestamp_format: "YYYY-MM-DD HH24:MI:SS.FF3"
  trim_text: true
  column_order: declared
```

The timestamp format is explicit because implicit rendering uses session settings, so the
same row hashes differently from two sessions. That one is an Oracle and Snowflake classic.

## Every Kimball type, and what each costs

| Type | Behavior | Columns introduced | Where |
|---|---|---|---|
| **0** | Retain original | optional paired `original_<col>` | dimension |
| **1** | Overwrite | none | |
| **2** | Add a new row | surrogate, `valid_from`, `valid_to`, `is_current`, `version_no` | dimension |
| **3** | Prior value column | `prior_<col>`, optional change date, per attribute per depth | dimension |
| **4** | Mini-dimension | mini-dimension FK | fact, plus the mini-dimension |
| **5** | Type 4 + Type 1 outrigger | `current_<minidim>_key` | base dimension |
| **6** | Type 1 + 2 + 3 | `current_<col>` per attribute, on every version row | dimension |
| **7** | Dual keys | durable key beside the surrogate | fact |

**Type 6 defaults to a view, not materialized columns.** Because the fact already carries
the durable key, as-is costs one ordinary join to the generated `_current` view rather than
a self join or a `current_` column rewritten across every version row. The materialized
form is an opt-in that has to state why, for the three cases where it still wins: a hot
filter that measures, a BI tool that cannot express the join, and a flat file extract to a
system that cannot join anything.

Materialized current columns can also go **stale**. The mass update that maintains them can
fail partway, leaving some versions of one entity on the new value and some on the old,
with nothing erroring. A view cannot be stale.

**Type 4 requires banding.** A mini-dimension carrying raw numerics has cardinality equal
to the product of every distinct value and defeats its own purpose, so a continuous column
with no declared band is an error.

**The Type 6 trap.** A materialized `current_<col>` in the `change_hash` set re-versions
the entire entity on every change. It looks like normal activity for about a month. The
validator refuses it.

## The model signature registry

Two generated tables recording the column contract of every model as it was actually
built, Type 2 so it can be reconstructed as of any date.

- **`model_signature`** covers the full ordered contract. The audit record.
- **`change_hash_signature`** covers only the Type 2 columns feeding `change_hash`. This is
  the one that decides whether a stored hash is still comparable to a new one.

That split keeps a harmless change harmless. Adding a Type 1 or a Type 6 current column
bumps the model signature and leaves the change hash signature alone, so the next load does
not version an entire dimension for nothing.

On a mismatch the generator emits a reconciliation step instead of silently re-versioning
every row, and the table permanently answers "why did every row in dim_customer version on
June first."

The validator can optionally read it and warn before a run rather than after. That read
stays optional: a spec validates offline from the model, and a warehouse read is an
enrichment, never a requirement.

---

## Build order

0. **The format.** Schema, registry, worked models. Done.
1. **DDL reader.** sqlglot parses dimension and fact DDL into a skeleton spec with every
   target column, type and key role, all transforms `unresolved`. Done.
2. **Validator.** Schema plus about thirty semantic checks. Done in outline, grows with
   each phase.
3. **Graph emitter.** Lineage JSON, kept separate so the UI never depends on the generator. Done.
4. **Review UI.** Static page, entity-level Sankey with column drill-in and a coverage
   panel. Review only, no editing. Done.
5. **dbt emitter.** sources, staging, dimensions, facts, tests, coverage report. Done,
   except `attribute_timelines`, which is declared, validated and reported as not
   generated. ← you are here
6. **Spreadsheet importer.** A mapping profile maps spreadsheet columns to spec fields;
   anything unparseable lands as `unresolved` with the row number in `raised_by`.

Remaining: the `attribute_timelines` emitter (change point merging across independent
histories), the spreadsheet importer, and running the output through dbt itself rather
than through the SQL harness.

## A note on the Sankey

The review diagram is entity level, with ribbon width as the number of column mappings and
column detail on click. A column-level Sankey is unreadable past about thirty flows, and a
forty column fact from eight sources is forty ribbons crossing a canvas.

It is also a Sankey-shaped lineage diagram rather than a true Sankey. Real Sankeys conserve
flow. Column mappings do not: one source column can feed five targets, and a target column
can have no source at all.

---

Built by Shawn Ludmer.
