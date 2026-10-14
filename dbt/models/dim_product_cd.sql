{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['product_durable_key', 'valid_from']) }}

-- Type 2 dimension, derivation: change_detection
-- Half open [from, to): version N's valid_to equals version N+1's valid_from exactly, so there is
-- no subtract-one-millisecond close, which is the most common Type 2 bug.
-- Surrogate keys are ordered here for a full refresh; the incremental path draws from the
-- warehouse sequence so retroactive inserts never re-key the versions around them.
with typed as (
  select
    snap.product_id as product_id,
    snap.product_name as product_name,
    snap.category as category,
    cast(cast(snap.batch_date as date) as timestamp) as valid_from
  from {{ source('reference', 'snap') }} as snap
),
hashed as (
  select *,
    lower(sha256((case when category is null then 'N:' when cast(category as varchar) = '' then 'E:' else 'L' || length(cast(category as varchar)) || ':' || cast(category as varchar) end))) as record_hash,
    lower(sha256((case when product_id is null then 'N:' when cast(product_id as varchar) = '' then 'E:' else 'L' || length(cast(product_id as varchar)) || ':' || cast(product_id as varchar) end) || '|' || (case when product_name is null then 'N:' when cast(product_name as varchar) = '' then 'E:' else 'L' || length(cast(product_name as varchar)) || ':' || cast(product_name as varchar) end))) as attribute_hash
  from typed
),
deduped as (
  -- a reload that carries no content change must not open a version
  select * from (
    select *, lag(record_hash) over (
                partition by product_id order by valid_from) as prev_hash
    from hashed
  ) t
  where prev_hash is null or prev_hash <> record_hash
),
versioned as (
  select *,
    coalesce(
      lead(valid_from) over (partition by product_id order by valid_from),
      timestamp '9999-12-31 00:00:00.000'
    ) as valid_to,
    row_number() over (partition by product_id order by valid_from) as version_no
  from deduped
)
select
  row_number() over (order by product_id, valid_from) as product_key,
  dense_rank() over (order by product_id) as product_durable_key,
  product_name as product_name,
  category as category,
  product_id as product_id,
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
  -1 as product_key,
  -1 as product_durable_key,
  cast('Unknown' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'unknown' as member_name
union all
select
  -2 as product_key,
  -2 as product_durable_key,
  cast('Not Applicable' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_applicable' as member_name
union all
select
  -3 as product_key,
  -3 as product_durable_key,
  cast('Not Yet Occurred' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_yet_occurred' as member_name
union all
select
  -4 as product_key,
  -4 as product_durable_key,
  cast('Invalid' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'invalid' as member_name
union all
select
  -5 as product_key,
  -5 as product_durable_key,
  cast('Not Provided' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_provided' as member_name
union all
select
  -6 as product_key,
  -6 as product_durable_key,
  cast('No Version At Date' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'no_version_at_date' as member_name
union all
select
  -7 as product_key,
  -7 as product_durable_key,
  cast('Multiple Matches' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'multiple_matches' as member_name
union all
select
  -8 as product_key,
  -8 as product_durable_key,
  cast('Rejected' as varchar(40)) as product_name,
  cast(null as varchar(12)) as category,
  cast(null as varchar(12)) as product_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'rejected' as member_name
