-- Landing for vendor_ref.lst. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view lst as
select * from read_csv('seeds/listings.psv',
  delim='|', header=true,
  columns={'exchange_code': 'varchar(8)', 'ticker': 'varchar(12)', 'eff_date': 'varchar(10)', 'board_lot_txt': 'varchar(10)'});
