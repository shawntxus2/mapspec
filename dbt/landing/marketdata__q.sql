-- Landing for marketdata.q. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view q as
select * from read_csv('seeds/quotes.psv',
  delim='|', header=true,
  columns={'quote_date': 'varchar(10)', 'mic': 'varchar(8)', 'symbol': 'varchar(12)', 'px_txt': 'varchar(30)'});
