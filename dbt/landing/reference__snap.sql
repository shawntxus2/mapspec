-- Landing for reference.snap. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view snap as
select * from read_csv('seeds/products_cd.psv',
  delim='|', header=true,
  columns={'batch_date': 'varchar(10)', 'product_id': 'varchar(12)', 'product_name': 'varchar(40)', 'category': 'varchar(12)'});
