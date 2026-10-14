-- Landing for vendor_ref.sec. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view sec as
select * from read_csv('seeds/securities$ref.psv',
  delim='|', header=true,
  columns={'security_id': 'varchar(12)', 'eff_date_txt': 'varchar(10)', 'ticker': 'varchar(12)', 'issuer_name': 'varchar(200)', 'sector_cd': 'varchar(8)', 'credit_rating': 'varchar(8)', 'par_value_txt': 'varchar(30)', 'maturity_dt_txt': 'varchar(10)', 'exchange_mic_txt': 'varchar(4)'});
