-- No two versions of one party_durable_key may overlap. A dimension invariant, tested here
-- once rather than per fact row. Real members only; member rows span the full range.
select a.party_durable_key as party_durable_key, a.party_key as a_key, b.party_key as b_key
from {{ ref('dim_party') }} as a
join {{ ref('dim_party') }} as b
  on  a.party_durable_key = b.party_durable_key
  and a.party_key <> b.party_key
  and a.valid_from < b.valid_to
  and b.valid_from < a.valid_to
where a.party_key >= 0 and b.party_key >= 0
