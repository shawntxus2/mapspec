{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['rate_durable_key', 'valid_from']) }}

-- Type 2 dimension, derivation: change_detection
-- Half open [from, to): version N's valid_to equals version N+1's valid_from exactly, so there is
-- no subtract-one-millisecond close, which is the most common Type 2 bug.
-- Surrogate keys are ordered here for a full refresh; the incremental path draws from the
-- warehouse sequence so retroactive inserts never re-key the versions around them.
with typed as (
  select
    rc.rate_id as rate_id,
    try_cast(rc.haircut_txt as numeric(9,4)) as haircut_pct,
    cast(cast(rc.batch_date as date) as timestamp) as valid_from
  from {{ source('reference', 'rc') }} as rc
),
hashed as (
  select *,
    lower(sha256((case when haircut_pct is null then 'N:' when cast(haircut_pct as varchar) = '' then 'E:' else 'L' || length(cast(haircut_pct as varchar)) || ':' || cast(haircut_pct as varchar) end))) as record_hash,
    lower(sha256((case when rate_id is null then 'N:' when cast(rate_id as varchar) = '' then 'E:' else 'L' || length(cast(rate_id as varchar)) || ':' || cast(rate_id as varchar) end))) as attribute_hash
  from typed
),
deduped as (
  -- a reload that carries no content change must not open a version
  select * from (
    select *, lag(record_hash) over (
                partition by rate_id order by valid_from) as prev_hash
    from hashed
  ) t
  where prev_hash is null or prev_hash <> record_hash
),
versioned as (
  select *,
    coalesce(
      lead(valid_from) over (partition by rate_id order by valid_from),
      timestamp '9999-12-31 00:00:00.000'
    ) as valid_to,
    row_number() over (partition by rate_id order by valid_from) as version_no
  from deduped
)
select
  row_number() over (order by rate_id, valid_from) as rate_key,
  dense_rank() over (order by rate_id) as rate_durable_key,
  haircut_pct as haircut_pct,
  rate_id as rate_id,
  valid_from as valid_from,
  valid_to as valid_to,
  valid_to = timestamp '9999-12-31 00:00:00.000' as is_current,
  version_no as version_no,
  record_hash as record_hash,
  attribute_hash as attribute_hash,
  cast(null as varchar) as member_name
from versioned
union all
select
  -1 as rate_key,
  -1 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'unknown' as member_name
union all
select
  -2 as rate_key,
  -2 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_applicable' as member_name
union all
select
  -3 as rate_key,
  -3 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_yet_occurred' as member_name
union all
select
  -4 as rate_key,
  -4 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'invalid' as member_name
union all
select
  -5 as rate_key,
  -5 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_provided' as member_name
union all
select
  -6 as rate_key,
  -6 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'no_version_at_date' as member_name
union all
select
  -7 as rate_key,
  -7 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'multiple_matches' as member_name
union all
select
  -8 as rate_key,
  -8 as rate_durable_key,
  cast(null as numeric(9,4)) as haircut_pct,
  cast(null as varchar(8)) as rate_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'rejected' as member_name
