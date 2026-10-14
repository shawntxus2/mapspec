# Coverage

What was generated, what was not, and why. Nothing here is a guess.

| model | layer | columns | generated | unresolved |
|---|---|---|---|---|
| `dim_date` | dimension | 0 | 0 | 0 |
| `dim_listing` | dimension | 3 | 3 | 0 |
| `dim_party` | dimension | 3 | 3 | 0 |
| `dim_product_cd` | dimension | 3 | 3 | 0 |
| `dim_rate_card` | dimension | 2 | 2 | 0 |
| `dim_security` | dimension | 11 | 10 | 1 |
| `dim_time` | dimension | 0 | 0 | 0 |
| `fct_holdings` | fact | 7 | 7 | 0 |
| `fct_listing_quotes` | fact | 5 | 5 | 0 |
| `fct_party_events` | fact | 5 | 5 | 0 |

**38 of 39 columns generated.**

## Unresolved columns

Each emits a `-- TODO` and a typed null in the model, never a guess.

### `dim_security`
- `haircut_pct` Haircut is keyed on maturity band, credit rating and sector, and the rate table was not included in the extract. Needs the reference table identified and its own effectivity, since haircuts change on their own schedule. _(owner: risk reference data)_


## Construct coverage

Every construct the schema declares and its observed state. VERIFIED means an acceptance spec reaches it and its built output is asserted, and is proven per platform: a tier-1 platform executes it, a tier-2 platform is only parsed, and the report does not round a parse up to verified. UNVERIFIED means written and never run, the state change_detection was in when it could not produce output at all. DECLARED, NOT GENERATED is a stub (honest) or ignored (silent). SUBSTITUTED emits confidently wrong output and fails that platform's build.

The states below are for `duckdb`, the canonical tier-1 platform; per-platform counts and the constructs whose status differs follow.

### VERIFIED (44)

- `def:identifier` primitive: an SQL identifier
- `def:sqlExpression` primitive: an SQL expression
- `def:keyExpressionList` primitive: one key expression or a list, for composite keys
- `def:timestampLiteral` primitive: a millisecond timestamp literal
- `def:ianaZone` primitive: an IANA zone id
- `def:project` project block; its parts are tracked separately
- `def:specialMembers` member rows emitted on every dimension
- `def:platformName` primitive: a platform name
- `def:transport` source transport
- `def:serialization` source serialization
- `def:target` target block
- `def:generated` generated-model container; kinds tracked separately
- `def:scd` scd block on a Type 2 dimension
- `def:durableKey` durable key emitted as dense_rank; the declared strategy is not honored
- `def:source` source declarations
- `def:projectionColumn` a projected source column
- `def:column` a target column
- `def:transform` a column transform; kinds tracked separately
- `def:tDirect` see transform:direct
- `def:tCast` see transform:cast
- `def:tConditional` see transform:conditional
- `def:tLookup` see transform:lookup
- `def:memberRoute` lookup failure route: a member name, null, or error
- `def:tConstant` see transform:constant
- `def:tUnresolved` see transform:unresolved
- `def:keyType` primitive: a key column type
- `def:cardinality` validation input for key sizing; not emitted
- `def:hashPolicy` hash algorithm and framing
- `def:scdBehavior` Kimball behavior per column; read for hash grouping
- `transform:direct` direct column copy
- `transform:cast` typed cast
- `transform:conditional` case expression
- `transform:lookup` point-in-time dimension lookup
- `transform:constant` constant
- `transform:unresolved` emits a TODO and a typed null, which is the generated behavior
- `scd.type:2` versioned dimension
- `derivation:change_detection` hash-based change detection
- `derivation:source_provided` source carries its own effectivity
- `surrogate_key.strategy:sequence` row_number
- `durable_key.strategy:sequence` dense_rank per entity, which is what the emitter emits
- `generated.kind:calendar` calendar dimension
- `generated.kind:time_of_day` time-of-day dimension
- `cast.on_error:null` DuckDB try_cast nulls a bad value; PostgreSQL has no try_cast and no clean guard, so it raises
- `cast.on_error:error` a bad cast raises, which is the declared behavior on both platforms

### UNVERIFIED (10)

- `def:survivorship` survived columns via source_precedence and specific_source only
- `def:survivorshipRule` a survivorship rule
- `def:tExpression` see transform:expression
- `def:tAggregate` see transform:aggregate
- `def:tSurvived` see transform:survived
- `transform:expression` authored SQL expression
- `transform:aggregate` aggregate
- `transform:survived` survived value across sources
- `survivorship.strategy:source_precedence` coalesce in precedence order
- `survivorship.strategy:specific_source` take one named source

### DECLARED, NOT GENERATED (16)

- `def:timestampPolicy` ignored, exercised: metadata, not read; timestamp columns come from cast transforms, so no wrong shape is emitted
- `def:timeline` stub: attribute_timelines timelines; validated, skipped with a message
- `def:contributionLineage` ignored: the emitter does not read it; no lineage side table is emitted
- `def:lateArrival` ignored, exercised: the emitter does not read it; no retroactive-insert handling is emitted
- `def:columnGroup` ignored: the emitter does not read it; no grouped columns are emitted
- `def:temporalKeys` ignored: the declared derived time keys are absent, not emitted wrong
- `def:currentView` ignored, exercised: the _current view is simply absent, not emitted wrong
- `def:miniDimension` ignored: Type 4 mini-dimension banding; no mini dimension is emitted
- `derivation:attribute_timelines` stub: emit_dimension skips with a message
- `surrogate_key.strategy:hash` ignored: the emitter emits a sequence regardless; hash never generated
- `generated.kind:identity_crosswalk` stub: emit_generated skips with a message
- `generated.kind:model_signature_registry` stub: emit_generated skips with a message
- `before_first:null` stub: span before an attribute's first value; part of attribute_timelines
- `before_first:unknown` stub: part of attribute_timelines
- `before_first:carry_back` stub: part of attribute_timelines
- `property:project.target_platform` ignored, exercised: the emitter writes DuckDB and ignores it; transpilation is next

### SUBSTITUTED (8)

- `scd.type:1` emits the Type 2 shape regardless, so a declared overwrite dimension carries full history
- `durable_key.strategy:crosswalk` emits dense_rank, not a persistent crosswalk mapping, so it is not durable across refreshes
- `durable_key.strategy:hash_natural_key` emits dense_rank, a sequence, not a reproducible hash of the natural key
- `survivorship.strategy:most_recent` emits precedence coalesce, so 'most recent source wins' silently becomes 'first in precedence wins'
- `survivorship.strategy:most_complete` emits precedence coalesce, not the most complete value
- `survivorship.strategy:aggregate` emits precedence coalesce, not an aggregate
- `survivorship.strategy:manual` emits precedence coalesce, not the manual resolution
- `cast.on_error:sentinel` the emitter does not implement the sentinel; a bad cast raises

## Per-platform status

**duckdb** (tier 1, executed): 44 verified, 10 unverified, 16 declared+not-generated, 8 substituted

**postgres** (tier 1, executed): 43 verified, 10 unverified, 16 declared+not-generated, 9 substituted
  - `cast.on_error:null`: SUBSTITUTED on postgres, VERIFIED on duckdb

A construct verified on one tier-1 platform and substituted on another stays visible as exactly that above, rather than disappearing from both columns. `cast.on_error:null` is the case: DuckDB honors it with `try_cast`, PostgreSQL has no `try_cast` and no clean guard, so it is substituted there and SUB001 would fail a PostgreSQL build that declared it. The choice was a DuckDB-only fixture (`dim_rate_card`, `project.build_platforms: [duckdb]`) over having the differential skip the column, because a substituted construct must never be emitted at all, so it cannot live in a spec the PostgreSQL build validates. The fixture drives a real failed cast, so the null path is observed on DuckDB, not merely emitted; the differential excludes the model as declared single-platform rather than comparing a table PostgreSQL never built.

## Platform tiers

- tier 1, executed and verified: duckdb, postgres
- tier 2, parse-checked with sqlglot, never executed: snowflake, sqlserver, oracle
- tier 3, no sqlglot dialect so not even parsed: db2_zos (a dialect, or an engine to execute against, would move it up)

