-- No version's valid_to may differ from the next version's valid_from for one rate_durable_key.
-- The companion to the overlap test, same real-member scope.
select rate_durable_key, valid_to as closes_at, next_opens_at from (
  select rate_durable_key, valid_to,
         lead(valid_from) over (partition by rate_durable_key order by valid_from) as next_opens_at
  from {{ ref('dim_rate_card') }}
  where rate_key >= 0
) t
where next_opens_at is not null and next_opens_at <> valid_to
