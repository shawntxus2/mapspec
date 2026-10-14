-- Landing for reference.src. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view src as
select * from read_csv('seeds/parties.psv',
  delim='|', header=true,
  columns={'source_system': 'varchar(20)', 'party_id': 'varchar(12)', 'eff_date': 'varchar(10)', 'party_name': 'varchar(80)'});
