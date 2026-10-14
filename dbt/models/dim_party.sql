{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['party_durable_key', 'valid_from']) }}

-- Type 2 dimension, derivation: source_provided
-- Half open [from, to): version N's valid_to equals version N+1's valid_from exactly, so there is
-- no subtract-one-millisecond close, which is the most common Type 2 bug.
-- Surrogate keys are ordered here for a full refresh; the incremental path draws from the
-- warehouse sequence so retroactive inserts never re-key the versions around them.
with typed as (
  select
    src.source_system as source_system,
    src.party_id as party_id,
    src.party_name as party_name,
    cast(cast(src.eff_date as date) as timestamp) as valid_from
  from {{ source('reference', 'src') }} as src
),
hashed as (
  select *,
    lower(sha256((case when party_name is null then 'N:' when cast(party_name as varchar) = '' then 'E:' else 'L' || length(cast(party_name as varchar)) || ':' || cast(party_name as varchar) end))) as record_hash,
    lower(sha256((case when source_system is null then 'N:' when cast(source_system as varchar) = '' then 'E:' else 'L' || length(cast(source_system as varchar)) || ':' || cast(source_system as varchar) end) || '|' || (case when party_id is null then 'N:' when cast(party_id as varchar) = '' then 'E:' else 'L' || length(cast(party_id as varchar)) || ':' || cast(party_id as varchar) end))) as attribute_hash
  from typed
),
versioned as (
  select *,
    coalesce(
      lead(valid_from) over (partition by source_system, party_id order by valid_from),
      timestamp '9999-12-31 00:00:00.000'
    ) as valid_to,
    row_number() over (partition by source_system, party_id order by valid_from) as version_no
  from hashed
)
select
  row_number() over (order by source_system, party_id, valid_from) as party_key,
  dense_rank() over (order by source_system, party_id) as party_durable_key,
  party_name as party_name,
  source_system as source_system,
  party_id as party_id,
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
  -1 as party_key,
  -1 as party_durable_key,
  cast('Unknown' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'unknown' as member_name
union all
select
  -2 as party_key,
  -2 as party_durable_key,
  cast('Not Applicable' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_applicable' as member_name
union all
select
  -3 as party_key,
  -3 as party_durable_key,
  cast('Not Yet Occurred' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_yet_occurred' as member_name
union all
select
  -4 as party_key,
  -4 as party_durable_key,
  cast('Invalid' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'invalid' as member_name
union all
select
  -5 as party_key,
  -5 as party_durable_key,
  cast('Not Provided' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_provided' as member_name
union all
select
  -6 as party_key,
  -6 as party_durable_key,
  cast('No Version At Date' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'no_version_at_date' as member_name
union all
select
  -7 as party_key,
  -7 as party_durable_key,
  cast('Multiple Matches' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'multiple_matches' as member_name
union all
select
  -8 as party_key,
  -8 as party_durable_key,
  cast('Rejected' as varchar(80)) as party_name,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as party_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'rejected' as member_name
