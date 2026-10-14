-- No version's valid_to may differ from the next version's valid_from for one listing_durable_key.
-- The companion to the overlap test, same real-member scope.
select listing_durable_key, valid_to as closes_at, next_opens_at from (
  select listing_durable_key, valid_to,
         lead(valid_from) over (partition by listing_durable_key order by valid_from) as next_opens_at
  from {{ ref('dim_listing') }}
  where listing_key >= 0
) t
where next_opens_at is not null and next_opens_at <> valid_to
