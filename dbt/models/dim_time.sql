{{ config(materialized='table') }}

-- generated: time of day at second grain, 86,400 rows.
-- Deliberately time zone naive: a clock reading, not an instant, which is what
-- lets a fact role-play it twice, once for UTC and once for local.
with secs as (select unnest(generate_series(0, 86399)) as s)
select
  cast(printf('%02d%02d%02d', s // 3600, (s % 3600) // 60, s % 60) as integer) as time_key,
  cast(s // 3600 as integer)                      as hour_24,
  cast((s % 3600) // 60 as integer)               as minute_of_hour,
  cast(s % 60 as integer)                         as second_of_minute,
  printf('%02d:00', s // 3600)                    as hour_band,
  printf('%02d:%02d', s // 3600, ((s % 3600) // 60) // 15 * 15) as quarter_hour_band,
  case when s < 43200 then 'AM' else 'PM' end     as am_pm,
  cast(null as varchar)                           as member_name
from secs
union all
select
  -1 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'Unknown' as hour_band,
  'Unknown' as quarter_hour_band,
  'Unknown' as am_pm,
  'unknown' as member_name
union all
select
  -2 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'Not Applicable' as hour_band,
  'Not Applicable' as quarter_hour_band,
  'Not Applicable' as am_pm,
  'not_applicable' as member_name
union all
select
  -3 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'Not Yet Occurred' as hour_band,
  'Not Yet Occurred' as quarter_hour_band,
  'Not Yet Occurred' as am_pm,
  'not_yet_occurred' as member_name
union all
select
  -4 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'Invalid' as hour_band,
  'Invalid' as quarter_hour_band,
  'Invalid' as am_pm,
  'invalid' as member_name
union all
select
  -5 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'Not Provided' as hour_band,
  'Not Provided' as quarter_hour_band,
  'Not Provided' as am_pm,
  'not_provided' as member_name
union all
select
  -6 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'No Version At Date' as hour_band,
  'No Version At Date' as quarter_hour_band,
  'No Version At Date' as am_pm,
  'no_version_at_date' as member_name
union all
select
  -7 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'Multiple Matches' as hour_band,
  'Multiple Matches' as quarter_hour_band,
  'Multiple Matches' as am_pm,
  'multiple_matches' as member_name
union all
select
  -8 as time_key,
  null as hour_24,
  null as minute_of_hour,
  null as second_of_minute,
  'Rejected' as hour_band,
  'Rejected' as quarter_hour_band,
  'Rejected' as am_pm,
  'rejected' as member_name
