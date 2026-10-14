{{ config(materialized='table') }}

-- Fact. Every Type 2 lookup resolves against one point in time, declared once on
-- the model as as_of (end_of_day) rather than restated per lookup, so a fact cannot join one
-- dimension as of the position date and another as of now.
select
  cast(strptime(ev.event_date, '%Y-%m-%d') as date) as event_date,
  ev.src_sys as src_sys,
  ev.party_id as party_id,
  cast(ev.amount_txt as numeric(18,2)) as amount,
  case
    when ev.party_id is null then -5
    when d0.party_key is not null then d0.party_key
    when x0.source_system is not null then -6
    else -1
  end as party_key,
  case
    when ev.party_id is null then -5
    when d0.party_key is not null then d0.party_durable_key
    when x0.source_system is not null then -6
    else -1
  end as party_durable_key
from {{ source('reference', 'ev') }} as ev
left join {{ ref('dim_party') }} as d0
  on  d0.source_system = ev.src_sys
  and d0.party_id = ev.party_id
  and (cast(ev.event_date as timestamp) + interval 1 day - interval 1 millisecond) >= d0.valid_from
  and (cast(ev.event_date as timestamp) + interval 1 day - interval 1 millisecond) <  d0.valid_to
left join (select distinct source_system, party_id from {{ ref('dim_party') }}) as x0
  on  x0.source_system = ev.src_sys
  and x0.party_id = ev.party_id
