-- No two versions of one rate_durable_key may overlap. A dimension invariant, tested here
-- once rather than per fact row. Real members only; member rows span the full range.
select a.rate_durable_key as rate_durable_key, a.rate_key as a_key, b.rate_key as b_key
from {{ ref('dim_rate_card') }} as a
join {{ ref('dim_rate_card') }} as b
  on  a.rate_durable_key = b.rate_durable_key
  and a.rate_key <> b.rate_key
  and a.valid_from < b.valid_to
  and b.valid_from < a.valid_to
where a.rate_key >= 0 and b.rate_key >= 0
