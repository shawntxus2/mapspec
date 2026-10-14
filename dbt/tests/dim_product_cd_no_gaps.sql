-- No version's valid_to may differ from the next version's valid_from for one product_durable_key.
-- The companion to the overlap test, same real-member scope.
select product_durable_key, valid_to as closes_at, next_opens_at from (
  select product_durable_key, valid_to,
         lead(valid_from) over (partition by product_durable_key order by valid_from) as next_opens_at
  from {{ ref('dim_product_cd') }}
  where product_key >= 0
) t
where next_opens_at is not null and next_opens_at <> valid_to
