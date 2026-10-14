-- No two versions of one product_durable_key may overlap. A dimension invariant, tested here
-- once rather than per fact row. Real members only; member rows span the full range.
select a.product_durable_key as product_durable_key, a.product_key as a_key, b.product_key as b_key
from {{ ref('dim_product_cd') }} as a
join {{ ref('dim_product_cd') }} as b
  on  a.product_durable_key = b.product_durable_key
  and a.product_key <> b.product_key
  and a.valid_from < b.valid_to
  and b.valid_from < a.valid_to
where a.product_key >= 0 and b.product_key >= 0
