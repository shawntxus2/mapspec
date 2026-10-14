{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['listing_durable_key', 'valid_from']) }}

-- Type 2 dimension, derivation: source_provided
-- Half open [from, to): version N's valid_to equals version N+1's valid_from exactly, so there is
-- no subtract-one-millisecond close, which is the most common Type 2 bug.
-- Surrogate keys are ordered here for a full refresh; the incremental path draws from the
-- warehouse sequence so retroactive inserts never re-key the versions around them.
with typed as (
  select
    lst.exchange_code as exchange_code,
    lst.ticker as ticker,
    cast(lst.board_lot_txt as int) as board_lot,
    cast(cast(lst.eff_date as date) as timestamp) as valid_from
  from {{ source('vendor_ref', 'lst') }} as lst
),
hashed as (
  select *,
    lower(sha256((case when board_lot is null then 'N:' when cast(board_lot as varchar) = '' then 'E:' else 'L' || length(cast(board_lot as varchar)) || ':' || cast(board_lot as varchar) end))) as record_hash,
    lower(sha256((case when exchange_code is null then 'N:' when cast(exchange_code as varchar) = '' then 'E:' else 'L' || length(cast(exchange_code as varchar)) || ':' || cast(exchange_code as varchar) end) || '|' || (case when ticker is null then 'N:' when cast(ticker as varchar) = '' then 'E:' else 'L' || length(cast(ticker as varchar)) || ':' || cast(ticker as varchar) end))) as attribute_hash
  from typed
),
versioned as (
  select *,
    coalesce(
      lead(valid_from) over (partition by exchange_code, ticker order by valid_from),
      timestamp '9999-12-31 00:00:00.000'
    ) as valid_to,
    row_number() over (partition by exchange_code, ticker order by valid_from) as version_no
  from hashed
)
select
  row_number() over (order by exchange_code, ticker, valid_from) as listing_key,
  dense_rank() over (order by exchange_code, ticker) as listing_durable_key,
  board_lot as board_lot,
  exchange_code as exchange_code,
  ticker as ticker,
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
  -1 as listing_key,
  -1 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'unknown' as member_name
union all
select
  -2 as listing_key,
  -2 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_applicable' as member_name
union all
select
  -3 as listing_key,
  -3 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_yet_occurred' as member_name
union all
select
  -4 as listing_key,
  -4 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'invalid' as member_name
union all
select
  -5 as listing_key,
  -5 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'not_provided' as member_name
union all
select
  -6 as listing_key,
  -6 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'no_version_at_date' as member_name
union all
select
  -7 as listing_key,
  -7 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'multiple_matches' as member_name
union all
select
  -8 as listing_key,
  -8 as listing_durable_key,
  cast(null as int) as board_lot,
  cast(null as varchar(8)) as exchange_code,
  cast(null as varchar(12)) as ticker,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  'rejected' as member_name
