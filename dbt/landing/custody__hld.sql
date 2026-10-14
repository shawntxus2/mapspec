-- Landing for custody.hld. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view hld as
select * from read_csv('seeds/holdings.psv',
  delim='|', header=true,
  columns={'holding_date': 'varchar(10)', 'account_id': 'varchar(20)', 'security_id': 'varchar(12)', 'quantity_txt': 'varchar(30)', 'market_value_txt': 'varchar(30)', 'ref_security_id': 'varchar(12)'});
