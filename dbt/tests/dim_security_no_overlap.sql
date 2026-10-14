-- No two versions of one security_durable_key may overlap. A dimension invariant, tested here
-- once rather than per fact row. Real members only; member rows span the full range.
select a.security_durable_key as security_durable_key, a.security_key as a_key, b.security_key as b_key
from {{ ref('dim_security') }} as a
join {{ ref('dim_security') }} as b
  on  a.security_durable_key = b.security_durable_key
  and a.security_key <> b.security_key
  and a.valid_from < b.valid_to
  and b.valid_from < a.valid_to
where a.security_key >= 0 and b.security_key >= 0
