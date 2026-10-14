{{ config(materialized='incremental', incremental_strategy='delete+insert', unique_key=['security_durable_key', 'valid_from']) }}

-- Type 2 dimension, derivation: source_provided
-- Half open [from, to): version N's valid_to equals version N+1's valid_from exactly, so there is
-- no subtract-one-millisecond close, which is the most common Type 2 bug.
-- Surrogate keys are ordered here for a full refresh; the incremental path draws from the
-- warehouse sequence so retroactive inserts never re-key the versions around them.
with typed as (
  select
    'vendor_ref' as source_system,
    sec.security_id as security_id,
    cast(trim(sec.ticker) as varchar(12)) as ticker,
    cast(trim(sec.issuer_name) as varchar(200)) as issuer_name,
    cast(trim(sec.exchange_mic_txt) as varchar(4)) as exchange_mic,
    cast(trim(sec.sector_cd) as varchar(8)) as sector_code,
    cast(trim(sec.credit_rating) as varchar(8)) as credit_rating,
    cast(sec.par_value_txt as numeric(18,4)) as par_value,
    cast(strptime(sec.maturity_dt_txt, '%Y-%m-%d') as date) as maturity_date,
    case
      when sec.maturity_dt_txt is null then 'UNKNOWN'
      when date_diff('year', date '2026-06-15', cast(sec.maturity_dt_txt as date)) < 1 then 'UNDER_1Y'
      when date_diff('year', date '2026-06-15', cast(sec.maturity_dt_txt as date)) < 3 then '1Y_3Y'
      when date_diff('year', date '2026-06-15', cast(sec.maturity_dt_txt as date)) < 5 then '3Y_5Y'
      when date_diff('year', date '2026-06-15', cast(sec.maturity_dt_txt as date)) < 10 then '5Y_10Y'
      else 'OVER_10Y'
    end as maturity_band,
    -- TODO haircut_pct (numeric(9,6)): Haircut is keyed on maturity band, credit rating and sector, and the rate table was not included in the extract. Needs the reference table identified and its own effectivity, since haircuts change on their own schedule. [owner: risk reference data]
    cast(null as numeric(9,6)) as haircut_pct,
    cast(cast(sec.eff_date_txt as date) as timestamp) as valid_from
  from {{ source('vendor_ref', 'sec') }} as sec
),
hashed as (
  select *,
    lower(sha256((case when ticker is null then 'N:' when cast(ticker as varchar) = '' then 'E:' else 'L' || length(cast(ticker as varchar)) || ':' || cast(ticker as varchar) end) || '|' || (case when issuer_name is null then 'N:' when cast(issuer_name as varchar) = '' then 'E:' else 'L' || length(cast(issuer_name as varchar)) || ':' || cast(issuer_name as varchar) end) || '|' || (case when exchange_mic is null then 'N:' when cast(exchange_mic as varchar) = '' then 'E:' else 'L' || length(cast(exchange_mic as varchar)) || ':' || cast(exchange_mic as varchar) end) || '|' || (case when sector_code is null then 'N:' when cast(sector_code as varchar) = '' then 'E:' else 'L' || length(cast(sector_code as varchar)) || ':' || cast(sector_code as varchar) end) || '|' || (case when credit_rating is null then 'N:' when cast(credit_rating as varchar) = '' then 'E:' else 'L' || length(cast(credit_rating as varchar)) || ':' || cast(credit_rating as varchar) end) || '|' || (case when par_value is null then 'N:' when cast(par_value as varchar) = '' then 'E:' else 'L' || length(cast(par_value as varchar)) || ':' || cast(par_value as varchar) end) || '|' || (case when maturity_date is null then 'N:' when cast(maturity_date as varchar) = '' then 'E:' else 'L' || length(cast(maturity_date as varchar)) || ':' || cast(maturity_date as varchar) end) || '|' || (case when maturity_band is null then 'N:' when cast(maturity_band as varchar) = '' then 'E:' else 'L' || length(cast(maturity_band as varchar)) || ':' || cast(maturity_band as varchar) end))) as record_hash,
    lower(sha256((case when source_system is null then 'N:' when cast(source_system as varchar) = '' then 'E:' else 'L' || length(cast(source_system as varchar)) || ':' || cast(source_system as varchar) end) || '|' || (case when security_id is null then 'N:' when cast(security_id as varchar) = '' then 'E:' else 'L' || length(cast(security_id as varchar)) || ':' || cast(security_id as varchar) end))) as attribute_hash
  from typed
),
versioned as (
  select *,
    coalesce(
      lead(valid_from) over (partition by source_system, security_id order by valid_from),
      timestamp '9999-12-31 00:00:00.000'
    ) as valid_to,
    row_number() over (partition by source_system, security_id order by valid_from) as version_no
  from hashed
)
select
  row_number() over (order by source_system, security_id, valid_from) as security_key,
  dense_rank() over (order by source_system, security_id) as security_durable_key,
  ticker as ticker,
  issuer_name as issuer_name,
  exchange_mic as exchange_mic,
  sector_code as sector_code,
  credit_rating as credit_rating,
  par_value as par_value,
  maturity_date as maturity_date,
  maturity_band as maturity_band,
  haircut_pct as haircut_pct,
  source_system as source_system,
  security_id as security_id,
  valid_from as valid_from,
  valid_to as valid_to,
  valid_to = timestamp '9999-12-31 00:00:00.000' as is_current,
  version_no as version_no,
  record_hash as record_hash,
  attribute_hash as attribute_hash,
  current_timestamp as dw_load_ts,
  'vendor_ref' as dw_source_system,
  cast(null as varchar) as member_name
from versioned
union all
select
  -1 as security_key,
  -1 as security_durable_key,
  cast('Unknown' as varchar(12)) as ticker,
  cast('Unknown' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast('Unknown' as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'unknown' as member_name
union all
select
  -2 as security_key,
  -2 as security_durable_key,
  cast(null as varchar(12)) as ticker,
  cast('Not Applicable' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast(null as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'not_applicable' as member_name
union all
select
  -3 as security_key,
  -3 as security_durable_key,
  cast(null as varchar(12)) as ticker,
  cast('Not Yet Occurred' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast(null as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'not_yet_occurred' as member_name
union all
select
  -4 as security_key,
  -4 as security_durable_key,
  cast('Invalid' as varchar(12)) as ticker,
  cast('Invalid' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast('Invalid' as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'invalid' as member_name
union all
select
  -5 as security_key,
  -5 as security_durable_key,
  cast('Not Provided' as varchar(12)) as ticker,
  cast('Not Provided' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast(null as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'not_provided' as member_name
union all
select
  -6 as security_key,
  -6 as security_durable_key,
  cast(null as varchar(12)) as ticker,
  cast('No Version At Date' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast(null as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'no_version_at_date' as member_name
union all
select
  -7 as security_key,
  -7 as security_durable_key,
  cast(null as varchar(12)) as ticker,
  cast('Multiple Matches' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast(null as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'multiple_matches' as member_name
union all
select
  -8 as security_key,
  -8 as security_durable_key,
  cast('Rejected' as varchar(12)) as ticker,
  cast('Rejected' as varchar(200)) as issuer_name,
  cast(null as varchar(4)) as exchange_mic,
  cast(null as varchar(8)) as sector_code,
  cast('Rejected' as varchar(8)) as credit_rating,
  cast(null as numeric(18,4)) as par_value,
  cast(null as date) as maturity_date,
  cast(null as varchar(12)) as maturity_band,
  cast(null as numeric(9,6)) as haircut_pct,
  cast(null as varchar(20)) as source_system,
  cast(null as varchar(12)) as security_id,
  timestamp '1900-01-01 00:00:00.000' as valid_from,
  timestamp '9999-12-31 00:00:00.000' as valid_to,
  true as is_current,
  0 as version_no,
  cast(null as varchar) as record_hash,
  cast(null as varchar) as attribute_hash,
  cast(null as timestamp) as dw_load_ts,
  cast(null as varchar) as dw_source_system,
  'rejected' as member_name
