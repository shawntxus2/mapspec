{{ config(materialized='table') }}

-- Fact. Every Type 2 lookup resolves against one point in time, declared once on
-- the model as as_of (end_of_day) rather than restated per lookup, so a fact cannot join one
-- dimension as of the position date and another as of now.
select
  cast(strptime(hld.holding_date, '%Y-%m-%d') as date) as holding_date,
  hld.account_id as account_id,
  hld.security_id as security_id,
  cast(hld.quantity_txt as numeric(18,4)) as quantity,
  cast(hld.market_value_txt as numeric(18,2)) as market_value,
  case
    when hld.security_id is null then -5
    when d0.security_key is not null then d0.security_key
    when x0.source_system is not null then -6
    else null
  end as security_key,
  case
    when hld.security_id is null then -5
    when d0.security_key is not null then d0.security_durable_key
    when x0.source_system is not null then -6
    else null
  end as security_durable_key,
  case
    when hld.ref_security_id is null then -5
    when d1.security_key is not null then d1.security_key
    when x1.source_system is not null then -6
    else -1
  end as ref_security_key,
  case
    when hld.ref_security_id is null then -5
    when d1.security_key is not null then d1.security_durable_key
    when x1.source_system is not null then -6
    else -1
  end as ref_security_durable_key
from {{ source('custody', 'hld') }} as hld
left join {{ ref('dim_security') }} as d0
  on  d0.source_system = 'vendor_ref'
  and d0.security_id = hld.security_id
  and (cast(hld.holding_date as timestamp) + interval 1 day - interval 1 millisecond) >= d0.valid_from
  and (cast(hld.holding_date as timestamp) + interval 1 day - interval 1 millisecond) <  d0.valid_to
left join (select distinct source_system, security_id from {{ ref('dim_security') }}) as x0
  on  x0.source_system = 'vendor_ref'
  and x0.security_id = hld.security_id
left join {{ ref('dim_security') }} as d1
  on  d1.source_system = 'vendor_ref'
  and d1.security_id = hld.ref_security_id
  and (cast(hld.holding_date as timestamp) + interval 1 day - interval 1 millisecond) >= d1.valid_from
  and (cast(hld.holding_date as timestamp) + interval 1 day - interval 1 millisecond) <  d1.valid_to
left join (select distinct source_system, security_id from {{ ref('dim_security') }}) as x1
  on  x1.source_system = 'vendor_ref'
  and x1.security_id = hld.ref_security_id
