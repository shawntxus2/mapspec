-- No two versions of one listing_durable_key may overlap. A dimension invariant, tested here
-- once rather than per fact row. Real members only; member rows span the full range.
select a.listing_durable_key as listing_durable_key, a.listing_key as a_key, b.listing_key as b_key
from {{ ref('dim_listing') }} as a
join {{ ref('dim_listing') }} as b
  on  a.listing_durable_key = b.listing_durable_key
  and a.listing_key <> b.listing_key
  and a.valid_from < b.valid_to
  and b.valid_from < a.valid_to
where a.listing_key >= 0 and b.listing_key >= 0
