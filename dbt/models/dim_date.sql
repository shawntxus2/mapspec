{{ config(materialized='table') }}

-- generated: calendar. Geography free on purpose; holidays differ by country
-- and exchange and belong in dim_calendar_date.
with days as (
  select cast(d as date) as full_date
  from generate_series(date '2015-01-01', date '2035-12-31', interval 1 day) t(d)
)
select
  cast(strftime(full_date, '%Y%m%d') as integer)        as date_key,
  full_date,
  extract(year from full_date)                          as calendar_year,
  extract(quarter from full_date)                       as calendar_quarter,
  extract(month from full_date)                         as calendar_month,
  strftime(full_date, '%B')                             as month_name,
  extract(day from full_date)                           as day_of_month,
  extract(dow from full_date)                           as day_of_week,
  strftime(full_date, '%A')                             as day_name,
  extract(week from full_date)                          as iso_week,
  extract(dow from full_date) between 1 and 5           as is_weekday,
  cast(null as varchar)                                 as member_name
from days
union all
select
  -1 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'Unknown' as month_name,
  null as day_of_month,
  null as day_of_week,
  'Unknown' as day_name,
  null as iso_week,
  null as is_weekday,
  'unknown' as member_name
union all
select
  -2 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'Not Applicable' as month_name,
  null as day_of_month,
  null as day_of_week,
  'Not Applicable' as day_name,
  null as iso_week,
  null as is_weekday,
  'not_applicable' as member_name
union all
select
  -3 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'Not Yet Occurred' as month_name,
  null as day_of_month,
  null as day_of_week,
  'Not Yet Occurred' as day_name,
  null as iso_week,
  null as is_weekday,
  'not_yet_occurred' as member_name
union all
select
  -4 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'Invalid' as month_name,
  null as day_of_month,
  null as day_of_week,
  'Invalid' as day_name,
  null as iso_week,
  null as is_weekday,
  'invalid' as member_name
union all
select
  -5 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'Not Provided' as month_name,
  null as day_of_month,
  null as day_of_week,
  'Not Provided' as day_name,
  null as iso_week,
  null as is_weekday,
  'not_provided' as member_name
union all
select
  -6 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'No Version At Date' as month_name,
  null as day_of_month,
  null as day_of_week,
  'No Version At Date' as day_name,
  null as iso_week,
  null as is_weekday,
  'no_version_at_date' as member_name
union all
select
  -7 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'Multiple Matches' as month_name,
  null as day_of_month,
  null as day_of_week,
  'Multiple Matches' as day_name,
  null as iso_week,
  null as is_weekday,
  'multiple_matches' as member_name
union all
select
  -8 as date_key,
  null as full_date,
  null as calendar_year,
  null as calendar_quarter,
  null as calendar_month,
  'Rejected' as month_name,
  null as day_of_month,
  null as day_of_week,
  'Rejected' as day_name,
  null as iso_week,
  null as is_weekday,
  'rejected' as member_name
