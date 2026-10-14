# Working on mapspec

Read this before changing anything. Most of what follows exists because the obvious
shortcut is wrong in a way that only shows up months later in a reconciliation.

## What this is

A declarative source-to-target mapping format, a validator, a DDL reader, a lineage
review page and a dbt emitter. The format is the product. Everything else consumes it.

**The design principle, which overrides convenience every time: the converter never
guesses.** Anything it cannot express as typed SQL is reported as unconverted. A guess
that is usually right is still a guess.

## The loop

```
make check     validate -> emit -> graph -> acceptance, stops on the first red
make dbt       emit the acceptance specs and run a real dbt build
```

Edit a spec in `models/`, never the SQL in `build/` or `dbt/models/`. Those are
regenerated and a hand edit is lost on the next emit.

## Layout

```
schema/mapspec.schema.json   the format
registry/platforms.yml       platform capabilities, and the transpilation tiers, dialects,
                             hash functions and generator templates. Data, not code.
registry/constructs.yml      every construct's emitter status. Data, not code.
constructs.py                construct coverage: verified, unverified, declared, substituted
models/                      reference specs, illustrative source URIs, not executed
acceptance/models/           specs whose sources are real files, and which are executed
acceptance/seeds/            the fixtures
acceptance/run.py            execute the generated SQL on DuckDB and assert what it claims
acceptance/run_postgres.py   the same specs and assertions against PostgreSQL (tier 1)
acceptance/differential.py   build each model on both tier-1 platforms and compare row by row
acceptance/parse_tier2.py    parse-check the transpiled SQL for the tier-2 platforms
validate.py                  schema plus about thirty semantic checks
ddl_reader.py                DDL in, skeleton spec out
emit.py                      dbt models, tests, landing DDL, coverage.md; --platform transpiles
graph.py                     lineage JSON and the review page
ui/index.html                review page template, graph embedded at build time
dbt/                         the runnable dbt project. models/ is generated.
```

## Rules that are not negotiable

These were each settled by working through a failure mode. Changing one means
re-deriving the argument, not just editing the code.

**Transforms are a closed set of nine.** `direct`, `cast`, `expression`, `conditional`,
`lookup`, `aggregate`, `constant`, `survived`, `unresolved`. Eight generate
deterministically. `unresolved` is a first class citizen: it emits a `-- TODO` and a typed
null, counts against coverage and renders red in review. Never add a tenth kind to avoid
writing `unresolved`.

**`cast` is separate from `expression` on purpose.** It is most of a staging layer, it
generates without authored SQL, it transpiles cleanly, and it is checkable for precision
loss. Do not collapse it into `expression`.

**Effectivity is `timestamp(3)` holding UTC, half open `[from, to)`, everywhere.** With
millisecond precision and half-open intervals, version N's `valid_to` equals version N+1's
`valid_from` exactly. No subtract-one-millisecond close. The open sentinel is a real high
value, not null, because `coalesce` in a join predicate is a function on the join column
and defeats partition pruning on every fact in the estate.

**Keys are sequences sized to cardinality, not hashes.** A point-in-time lookup joins the
dimension anyway, so a computable key buys nothing. `strategy: hash` survives only for
parallel assignment with no central sequence, and the validator asks you to say why.

**Hashing is for content, never for keys.** `change_hash` covers Type 2 columns only and
is computed **after** survivorship. `attribute_hash` covers everything maintained in
place. Type 0 columns are in neither; they are asserted, not compared. A Type 6
`current_*` column in the change hash re-versions the whole entity on every change and
looks like normal activity for about a month.

**Nullness in a hash is encoded structurally, not with a sentinel string.** `N:` null,
`E:` empty, `L<len>:<value>` otherwise. A sentinel can be impersonated by real data even
behind a length prefix.

**Types are normalized platform-qualified.** Oracle `DATE` is a date plus a time to the
second, not an ANSI `DATE`. Never compare bare type keywords.

**Type 6 defaults to a view.** The fact carries the durable key, so as-is costs one
ordinary join to the generated `_current` view. Materialized `current_*` columns can go
stale when the mass update fails partway, and a view cannot.

**Matching is out of scope.** The crosswalk is consumed, not produced. Deciding that two
records are the same person belongs to another system.

**A fact foreign key is never null.** A null cannot join, and an inner join drops the row
without saying so. Every failed lookup resolves to a declared special member instead.

**Special member codes are declared once at project level, never on the dimension.** If one
dimension uses `-2` for not applicable and another uses `-2` for invalid, every analysis
spanning the two is wrong and nothing errors. The registry is the single source; a
dimension draws from it and cannot extend it.

**Every dimension emits every declared member, used or not.** A left join to a row that is
not there yields null, which is the condition the members exist to prevent. The rows are a
`union all` onto the versioning result, never fed through it: no window functions, no
change detection, a null change hash, validity spanning the full range.

**A member row carries its label only where the column's contract can hold it, otherwise
null.** The natural key columns are null on a member (it is not a real entity), and the
marker column carries the name. A text column takes the label so a group by reads as a
category, but only when the column's contract allows it: a declared `allowed_values` the
label is not among, or a declared width the label overruns, both force null. Writing a value
the contract cannot hold is worse than absence, because `member_name` already carries the
meaning and a fragment that fits can collide with real data. Never truncate a label to fit:
a truncated label is a plausible wrong value, and on a platform that ignores declared width
(DuckDB) against one that enforces it (PostgreSQL) an over-length label is also exactly where
the two silently diverge, which the differential now guards with a column too narrow for any
member label.

**The reserved band is `[reserved_floor, -1]`, inclusive, declared in one field.** There is
no second floor on the key block. Sequences start at 1 and the sign keeps the two ranges
apart, except under `strategy: hash`, where a value cast into a signed type lands negative
about half the time and has to be forced positive explicitly.

**A route of `error` is only as strong as the test behind it.** No platform throws from a
select, so `error` emits a typed null and the column's `not_null` test stops the build.
That makes the test the mechanism: `error` on a nullable column is the same declaration as
`null`, so the validator requires `nullable: false` alongside it.

**An invariant about a dimension is tested on the dimension, not per fact row.** Overlapping
Type 2 versions were briefly handled with a per-lookup `on_multiple` route, which paid an
aggregation on every fact load to discover something that is true or false once. The
overlap and gap tests are generated on the dimension instead. A per-row route to absorb a
structural defect is the same mistake as a tenth transform kind.

**`>= 0` is the filter for a real member, because the invariant is the sign, not the
magnitude. No generated SQL or test holds a specific code.** The validator enforces
`reserved_floor <= code <= -1`, so a member is always negative and a real key is never
negative; `>= 0` is the exact complement for every key domain in the format, a sequence
from 1, a yyyymmdd date key, an hhmmss time key from 0, and a hash forced positive by
`absolute` which can also be 0. `> 0` is never more correct, only sometimes equally
correct, so it does not appear anywhere. The only place a code appears in generated SQL is
the `case` expression that resolves a lookup failure. Everything else, including every
`not_null`, `accepted_values` and `relationships_where` condition, uses `>= 0`. A sentinel
in a module constant is the same mistake one layer in, and was removed once already.

**Each lookup failure mode resolves to its own member.** A null source key, a key that
never loaded, a key with no version covering the as-of instant and a value that failed its
cast are four problems with four owners. Collapsing any two loses the routing, and
recovering it later means reprocessing.

**A lookup resolves the dimension's key columns from the dimension, never from the fact's
names.** The join columns, the surrogate and the durable come from the referenced
dimension's `target.natural_key` and spec, or from an explicit `dimension_natural_key`, and
a dimension absent from the emit run with no override is an error, not a guess. Deriving
them from the fact's column-name suffix made the same spec generate different SQL depending
on how the emitter was invoked, which is a guess dressed as a convenience. The natural key
is a list, one equality per column positionally matched against the dimension's, because a
dimension can be keyed on two columns.

**A multi-source dimension's natural key is the pair `(source_system, business key)`, and
both halves are materialized and joined.** Two records sharing a business key from
different systems are different records, so a lookup that joined on the business key alone
would match the wrong system's version, silently and without error. So `source_system` is a
real column on any dimension that names it in the natural key, fed from the source system,
and the lookup supplies it through `tLookup.source_system`, which the join uses. The
validator errors when a dimension names `source_system` in its key without materializing it,
and when a lookup into such a dimension does not declare `source_system`. Because the
qualifier is now a column, `target.natural_key` and the role-tagged columns agree and the
join reads `target.natural_key` directly; the role-tagged special case is gone.

**Every construct the schema declares has a recorded status, and substituted output is a
build error.** `registry/constructs.yml` records, for every $def, closed-set enum value and
expected property, whether the emitter generates it, stubs it with a message, ignores it,
or substitutes something else. The constructs are enumerated from the schema, so one with no
registry entry fails validation (REG001) and the coverage report cannot go stale. The build
prints four counts, verified, unverified, declared and not generated, and substituted,
computed by scanning the acceptance specs with schema defaults resolved rather than trusting
the registry, because the registry says what the emitter does and the specs say what is
tested and the gap between them is the finding. **`generated` is a self-report, not an
observation**: change_detection carried it while being unable to produce output, and only a
fixture reaching it found that out. So a generated construct an acceptance spec reaches and
asserts is VERIFIED, observed to work; one nothing reaches is UNVERIFIED, written and never
run, and may be broken the same way, so the build names the unverified list to point at the
next fixture to write. A stub is honest, it says it did nothing. Ignored is silent, its
output is missing and someone notices. **Substituted is the one that bites: it emits
well-formed output that passes every test and is not what the spec declared** - a Type 1
dimension carrying full Type 2 history, a durable key declared crosswalk or hash that is
really a dense_rank sequence (not durable across refreshes, which is the whole point of
either), "most recent source wins" that is really "first in precedence wins." The converter
never guesses, so a spec that declares a substituted construct (SUB001) fails the build. Fix
it by removing the declaration the emitter cannot honor, never by weakening the rule.

**`durable_key.strategy` is required, and `sequence` is the only one the emitter honors.** A
required field is how a spec is stopped from silently receiving a dense_rank it never asked
for, which is the substituted trap one level down. `sequence` names exactly what the emitter
emits. `crosswalk` and `hash_natural_key` are substituted until the identity_crosswalk
emitter and a real hash exist, so an unstable-key dimension, which genuinely needs crosswalk,
cannot be honestly built today and declares `sequence` as a labeled placeholder. A comment is
not enforcement, so the validator warns (KEY005) on an unstable natural key with a sequence
durable key: a sequence over current data re-keys when an entity is inserted, so it is not
durable across refreshes, which is the same defect as `hash_natural_key` on a moving key.

**A model whose grain is not unique does not hold what it says it holds.** Every model that
declares a `target.grain`, not only a fact, generates a `unique_combination_of_columns` on
it. A fact lookup that fans out duplicates the grain, and that is where the test started,
but the highest-consequence grain is the crosswalk's: a duplicate there corrupts durable key
assignment and so every fact carrying the durable key, unrecoverable without a reload. On a
Type 2 dimension the grain already equals the SCD `(durable key, effective_from)`
combination, so one test is emitted, not two; a dimension whose grain differs from that
combination gets both and is a finding, not a thing to paper over.

**`source_system` stays in a natural key even on a single-source dimension.** It looks like
dead weight, a constant column nobody reads, and it is not: adding it later changes the
natural key, which re-versions the whole dimension and invalidates the crosswalk and every
fact that carries the durable key. That is a reload, not a migration. The constant column is
cheap insurance against an expensive day, so it is declared up front and not cleaned up.

## Things that have already bitten

- **YAML 1.1 reads a bare `on` key as boolean true.** The join key is `condition` for this
  reason. Do not rename it back.
- **An unquoted `numeric(18,4)` inside a YAML flow mapping splits on the comma.** Quote
  every parameterized type in flow style.
- **DuckDB's `/` is true division.** Integer division is `//`. A `printf('%02d')` on the
  result of `/` fails at build time.
- **dbt's dependencies conflict with a PyTorch environment.** Its venv stays separate, and
  its versions are pinned in `make setup`. An unpinned install means the next clean machine
  can resolve a different set and the verification stops meaning anything.
- **A hand-written exclusion in a test hides a generator bug.** `acceptance/run.py` once
  carried `where f.security_key <> -1` while the emitter generated a strict test with no
  exclusion. The two disagreed and only the one that already knew the answer passed. Real
  dbt found it. Nothing in `run.py` reads the specs: it asserts properties that hold for
  any correct model. `>= 0` is such a property; a specific code is something it had to be
  told.
- **A property asserted only in `run.py` is a property the generator does not have.** The
  Type 2 overlap and gap invariants were asserted by hand in the acceptance suite and not
  generated for months, so every real warehouse built by the emitter went without them.
  When the suite proves something the emitter should be proving, that is the same bug one
  layer out.
- **`formatter: template` on a dbt-duckdb external location makes `$` special.** It was
  chosen because `read_csv(..., columns={...})` has literal braces that
  `str.format_map` consumed and doubling them then tripped dbt's Jinja.
  `string.Template` fixes the braces and introduces `$`, so `$` is escaped as `$$` on the
  way out.
- **A key domain that includes zero breaks a magnitude filter.** An hhmmss `time_key`
  makes midnight `0`, a real row that `> 0` silently drops. The member filter tests the
  sign, not the magnitude, so it is `>= 0`, the exact complement of the negative codes.
  `> 0` was used first and was equally correct only where the smallest real key was at
  least 1; it was wrong the moment a key could be zero, and is gone.
- **A validator rule no spec exercises is untested code, and a case arm no data reaches is
  untested output.** The new special-member rules were negative-tested directly, which was
  right; the generated lookup `case` needs the same. `security_key` routes a miss to
  `error`, whose arm emits `else null` and which no passing row can reach, so a second
  lookup with `on_miss: unknown` and seed rows with a never-loaded and an empty natural key
  exist only to drive the miss-to-member and null-to-member arms. Ask of every branch the
  emitter writes whether anything runs it.
- **A nk-only join to a Type 2 dimension fans the fact out by its version count.** The
  second join that tells a miss from a no-version has no temporal predicate, so against the
  dimension itself it matches every version of the key and multiplies the fact rows, once
  per lookup. It went unnoticed because no assertion counted fact rows: eight holdings had
  become fifty-two. The join is against `select distinct <natural key>` so it matches one
  row, and the acceptance suite now asserts each fact's row count is unchanged by its joins.
- **A source alias names a landing view, so two models sharing one clobber each other.**
  `dim_party` and `dim_product_cd` both used `src`; the second view won and the first model
  read the wrong source. Only the emitter sees all models at once, so the validator errors
  (SRC010) when two models in the emit set declare the same alias with different definitions.
  It caught a second, older collision on first run: `dim_customer` and `dim_instrument_ref`
  both used `mf` for different mainframe tables, and `emit_sources` had been silently keeping
  the first and dropping the second from `sources.yml`.

## Where the real gaps are

**Special members are built, parent task and follow-up both.** The registry lives under
`project.special_members`, the reserved band is the inclusive `[reserved_floor, -1]` in one
field, a lookup failure resolves through a two-stage `case`, every dimension including the
generated calendar and time dimensions emits the members, and the Type 2 overlap and gap
invariants are generated as singular tests. `scd.unknown_member`, `reserved_members` and
`on_multiple` are gone. The specs are `CLAUDE_TASKS_special_members.md` and its follow-up.
The real-member filter is `>= 0` everywhere, the exact complement of the negative codes, so
a zero-valued real key such as a midnight `time_key` is kept rather than dropped.

**Transpilation, phase one, is built for two tiers; the rest of the dialects are phase two.**
`--platform` drives emission, with `registry/platforms.yml` declaring each platform's tier,
sqlglot dialect, hash function and generator templates. The rules, because the Spark cannot
execute Snowflake, Oracle, SQL Server or DB2:
- **Tier 1 is executed, tier 2 is only parsed, and parse is not proof.** DuckDB and
  PostgreSQL run the full acceptance suite, the same specs and assertions, which is the only
  thing that proves the transpiler preserved meaning rather than syntax. Add a tier-1
  platform before a tier-2 one. Snowflake, SQL Server and Oracle are sqlglot parse-checked
  only; DB2 has no sqlglot dialect and is not even parsed. `coverage.md` reports this per
  platform and does not round a parse up to verified.
- **The hash and the generators come from the registry, never from sqlglot.** sqlglot leaves
  `sha256` untranslated, so a plausible substitution would change every change_hash and
  re-version the estate silently; each platform declares its hash function, encoding and
  case, and an undeclared one is an error. The calendar and time generators are per-platform
  templates for the same reason. Only expression-level SQL, casts, authored expressions and
  the half-open join predicate, routes through sqlglot; DuckDB is the canonical source
  dialect and is emitted verbatim, so its output never changed.
- **What will not transpile downgrades to unresolved, never to a guess.** A cast or authored
  expression sqlglot cannot translate becomes a `-- TODO` and a typed null, counted against
  coverage and red in review, the same as a declared `unresolved`. A substituted construct
  stays a build error and is not transpiled at all.

**Transpilation, phase two: two green suites that disagree are the thing to catch.** Phase
one found two semantic differences between DuckDB and PostgreSQL. Neither was caught by a
test, because each suite only checked its own platform against the spec; nothing compared
the two platforms to each other. The rules that close that gap:
- **The cross-platform differential is the mechanism.** `acceptance/differential.py` builds
  every acceptance model on both tier-1 platforms, dumps each table in canonical form
  (ordered by the declared grain, every column rendered as text with an explicit format so
  timestamp and numeric rendering cannot masquerade as a data difference), and compares row
  by row. The first differing row fails with model, column, grain and both values. A
  difference is a transpiler defect to fix or a platform difference declared in
  `platform_differences` with a reason. There is no third category, and "they agree on this
  seed" is not a reason, so seeds must reach the boundaries where constructs disagree (a row
  one day either side of a year boundary, so `date_diff` and `EXTRACT(year FROM AGE(...))`
  actually produce different bands).
- **A difference that feeds a hash is a defect, not a declarable difference.** sqlglot
  renders `date_diff('year', a, b)` as `EXTRACT(year FROM AGE(b, a))`, which is elapsed years
  rather than the year-boundary count, and that value lands in a Type 2 hashed column:
  declaring it would be exactly the silent re-versioning the registry-hash rule exists to
  prevent. It is fixed with a targeted AST rewrite, and only the `year` unit is vouched for;
  any other unit downgrades to unresolved rather than translating to something unproven. The
  lesson generalizes: audit date and interval functions in authored expressions first, they
  are where the platforms quietly diverge.
- **Construct status is per platform, not once.** `registry/constructs.yml` status may be a
  map (`{default: generated, postgres: substituted}`). `cast` with `on_error: null` is
  generated on DuckDB via `try_cast` and substituted on PostgreSQL, which has no `try_cast`
  and no clean guard, so SUB001 fires only for the platform being validated and `coverage.md`
  reports a status per platform. If a registry template can generate the guarded form
  cleanly, write it; until then the construct is a build error on that platform, not a guess.
  A construct honored on one tier-1 platform and substituted on another must still be verified
  where it is honored, or it silently disappears from both columns. Because a substituted
  construct is never emitted at all, it cannot live in a spec the substituting platform
  validates, so its coverage comes from a single-platform fixture: `project.build_platforms`
  lists only the platforms that honor it, and that fixture is validated, emitted, built and
  compared on those platforms alone. The differential names it as declared single-platform
  rather than comparing a table the other platform never built. `cast.on_error: null` is the
  live case, verified on DuckDB via `dim_rate_card` (which drives a real failed cast, so the
  null path is observed, not merely emitted) and substituted on PostgreSQL.
- **DB2 is its own tier, not a weak tier 2.** sqlglot has no DB2 dialect, so DB2 is emitted
  but neither executed nor parsed, which is strictly weaker than the tier-2 platforms that at
  least parse. It is tier 3 so there is no silent gradient inside tier 2. A sqlglot dialect
  moves it to tier 2, an engine to execute against moves it to tier 1.

**`attribute_timelines` is declared, validated and not generated.** Change point merging
across independent histories is the most complex piece and is deliberately last.

**The spreadsheet importer does not exist.** A `mapping_profile.yml` maps spreadsheet
columns to spec fields; anything unparseable lands as `unresolved` with the row number in
`raised_by`.

**The validator cannot read the warehouse.** The `meta_model_signature` pre-flight check
is designed and not wired up. Keep that read optional: a spec must validate offline from
the model, and a warehouse read is an enrichment.

## Style

American spelling. No em-dashes in anything a reader sees, including generated SQL
comments and the README. Comments in generated SQL explain *why* a shape was chosen, not
what the SQL does.
