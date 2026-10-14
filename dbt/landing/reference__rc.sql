-- Landing for reference.rc. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view rc as
select * from read_csv('seeds/rate_card.psv',
  delim='|', header=true,
  columns={'batch_date': 'varchar(10)', 'rate_id': 'varchar(8)', 'haircut_txt': 'varchar(16)'});
