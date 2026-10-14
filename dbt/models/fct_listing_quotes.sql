{{ config(materialized='table') }}

-- Fact. Every Type 2 lookup resolves against one point in time, declared once on
-- the model as as_of (end_of_day) rather than restated per lookup, so a fact cannot join one
-- dimension as of the position date and another as of now.
select
  cast(strptime(q.quote_date, '%Y-%m-%d') as date) as quote_date,
  q.mic as mic,
  q.symbol as symbol,
  cast(q.px_txt as numeric(18,4)) as price,
  case
    when q.mic is null or q.symbol is null then -5
    when d0.listing_key is not null then d0.listing_key
    when x0.exchange_code is not null then -6
    else -1
  end as listing_key,
  case
    when q.mic is null or q.symbol is null then -5
    when d0.listing_key is not null then d0.listing_durable_key
    when x0.exchange_code is not null then -6
    else -1
  end as listing_durable_key
from {{ source('marketdata', 'q') }} as q
left join {{ ref('dim_listing') }} as d0
  on  d0.exchange_code = q.mic
  and d0.ticker = q.symbol
  and (cast(q.quote_date as timestamp) + interval 1 day - interval 1 millisecond) >= d0.valid_from
  and (cast(q.quote_date as timestamp) + interval 1 day - interval 1 millisecond) <  d0.valid_to
left join (select distinct exchange_code, ticker from {{ ref('dim_listing') }}) as x0
  on  x0.exchange_code = q.mic
  and x0.ticker = q.symbol
