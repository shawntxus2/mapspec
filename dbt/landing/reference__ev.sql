-- Landing for reference.ev. Not a dbt model.
-- dbt reads relations the warehouse can already query, so this step belongs to
-- whatever lands the file. Shown for DuckDB, which reads these natively.
create or replace view ev as
select * from read_csv('seeds/party_events.psv',
  delim='|', header=true,
  columns={'event_date': 'varchar(10)', 'src_sys': 'varchar(20)', 'party_id': 'varchar(12)', 'amount_txt': 'varchar(30)'});
