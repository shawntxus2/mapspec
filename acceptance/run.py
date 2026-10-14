#!/usr/bin/env python3
"""Acceptance: execute the generated models and assert what they claim.

dbt itself would not install in this environment, so this harness resolves ref() and
source() and runs the generated SQL directly on DuckDB. That proves the SQL, which is the
part mapspec writes. Orchestration, materialization strategies and the test runner are
dbt's, and are not exercised here.

Nothing below reads the specs for its assertions. It reads the generated SQL and the seeds,
and checks properties that must hold for any correct Type 2 dimension and point-in-time
fact. The data_checks run on every tier-1 platform; the structural checks read the emitted
DuckDB tree and run only here.
"""
import pathlib
import re
import sys

import duckdb
import yaml

HERE = pathlib.Path(__file__).parent
BUILD = HERE / "build"
SENTINEL = "9999-12-31 00:00:00.000"  # the open interval's high sentinel, a Type 2 constant
CFG = re.compile(r"\{\{\s*config\([^}]*\)\s*\}\}")
REF = re.compile(r"\{\{\s*ref\('([^']+)'\)\s*\}\}")
SRC = re.compile(r"\{\{\s*source\('([^']+)',\s*'([^']+)'\)\s*\}\}")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def resolve(sql):
    """ref('x') -> x, source('sys','a') -> a, config() -> gone. The harness is the loader."""
    return CFG.sub("", SRC.sub(lambda m: m.group(2), REF.sub(lambda m: m.group(1), sql)))


def build_models(models_dir, create):
    """Build every model in dependency order, calling create(name, resolved_sql)."""
    models = {f.stem: f.read_text() for f in sorted(models_dir.glob("*.sql"))}
    done, guard = set(), 0
    while len(done) < len(models) and guard < 20:
        guard += 1
        for name, sql in models.items():
            if name in done or not set(REF.findall(sql)) <= done:
                continue
            create(name, resolve(sql))
            done.add(name)
    return done, models


def data_checks(q, one):
    """The semantic assertions. These hold on any correct build, so they run on every tier-1
    platform; passing on DuckDB and PostgreSQL both is what proves the transpiler preserved
    meaning, not just syntax."""
    # The special member rows carry negative keys. Type 2 integrity is a property of the real
    # versions, so these filter to security_key >= 0. That is a property, not a value it was told.
    print("Type 2 integrity, derived from the SCD contract")
    check("row count", one("select count(*) from dim_security where security_key >= 0") == 4,
          f"{one('select count(*) from dim_security where security_key >= 0')} versions from 4 source rows")
    check("no overlapping versions", one("""
        select count(*) from dim_security a join dim_security b
          on a.security_id = b.security_id and a.security_key <> b.security_key
         and a.valid_from < b.valid_to and b.valid_from < a.valid_to
         where a.security_key >= 0 and b.security_key >= 0""") == 0)
    check("no gaps between versions", one("""
        select count(*) from (
          select valid_to, lead(valid_from) over (partition by security_id order by valid_from) nxt
          from dim_security where security_key >= 0) t
        where nxt is not null and nxt <> valid_to""") == 0)
    check("valid_to strictly after valid_from",
          one("select count(*) from dim_security where valid_to <= valid_from") == 0)
    check("exactly one open version per key", one("""
        select count(*) from (
          select security_id, sum(case when is_current then 1 else 0 end) n
          from dim_security where security_key >= 0 group by 1) t where n <> 1""") == 0)
    check("surrogate keys unique",
          one("select count(*) from (select security_key from dim_security group by 1 having count(*)>1) t") == 0)
    check("durable key stable across versions", one("""
        select count(*) from (
          select security_id, count(distinct security_durable_key) n
          from dim_security where security_key >= 0 group by 1) t where n <> 1""") == 0)
    check("content hash differs between versions", one("""
        select count(*) from (
          select record_hash, count(*) n from dim_security
          where security_id = 'SEC001' group by 1) t where n > 1""") == 0)

    print("\nPoint in time resolution, as_of_promotion: end_of_day")
    sector = lambda d: one(f"""
        select d.sector_code from fct_holdings f join dim_security d on d.security_key = f.security_key
        where f.holding_date = date '{d}' and f.security_id = 'SEC001'""")
    check("before the change resolves to the earlier version", sector("2026-03-31") == "INDU",
          f"got {sector('2026-03-31')}")
    check("after the change resolves to the later version", sector("2026-04-30") == "INFT",
          f"got {sector('2026-04-30')}")
    check("on the change date, end of day takes the new version", sector("2026-04-15") == "INFT",
          f"got {sector('2026-04-15')}")
    check("a later change resolves again", one("""
        select d.credit_rating from fct_holdings f join dim_security d on d.security_key = f.security_key
        where f.holding_date = date '2026-09-30'""") == "AA")
    check("no version at date resolves to the right member", one("""
        select d.member_name from fct_holdings f join dim_security d
          on d.security_key = f.security_key
        where f.holding_date = date '2025-12-01'""") == "no_version_at_date")
    check("every holding resolved or explicitly a member",
          one("select count(*) from fct_holdings where security_key is null") == 0)

    # Every arm of the generated case must be reached by a row, or it is output nothing runs.
    mem = lambda acct: one(f"""
        select d.member_name from fct_holdings f join dim_security d on d.security_key = f.ref_security_key
        where f.account_id = '{acct}'""")
    check("miss arm resolves to the unknown member", mem("ACC8") == "unknown",
          "ref_security_id SEC999 was never loaded")
    check("null-key arm resolves to not_provided", mem("ACC7") == "not_provided",
          "ref_security_id arrived empty")

    print("\nDerived from declared constraints")
    check("accepted_values holds for sector_code", one("""
        select count(*) from dim_security where sector_code is not null and sector_code not in
        ('ENERGY','FIN','HLTH','INDU','INFT','MATR','REAL','UTIL','CONS','COMM')""") == 0)
    check("not_null holds on the fact grain",
          one("select count(*) from fct_holdings where holding_date is null or account_id is null") == 0)
    check("relationships hold for real references", one("""
        select count(*) from fct_holdings f where f.security_key >= 0
        and not exists (select 1 from dim_security d where d.security_key = f.security_key)""") == 0)
    check("unresolved column emitted as a typed null, not a guess",
          one("select count(*) from dim_security where haircut_pct is not null") == 0)

    print("\nSpecial members")
    members = q("select security_key, member_name from dim_security where security_key < 0")
    member_keys = {k for k, _ in members}
    real_entities = one("select count(distinct security_durable_key) from dim_security where security_key >= 0")
    check("every member present exactly once", len(members) == len(member_keys) and len(members) >= 4,
          f"{len(members)} member rows, {len(member_keys)} distinct codes")
    check("member rows carry the marker and a null natural key", one("""
        select count(*) from dim_security
        where security_key < 0 and (member_name is null or security_id is not null)""") == 0)
    check("member rows span the full validity range", one(f"""
        select count(*) from dim_security where security_key < 0 and not (
          valid_from <= timestamp '2025-12-01 00:00:00.000'
          and valid_to = timestamp '{SENTINEL}' and is_current)""") == 0,
          "valid_from at or below the earliest fact, valid_to at the sentinel, current")
    check("member change hashes are null", one("""
        select count(*) from dim_security where security_key < 0 and record_hash is not null""") == 0)
    check("every fact key is a real reference or a member", one("""
        select count(*) from fct_holdings f
        where not exists (select 1 from dim_security d where d.security_key = f.security_key)""") == 0)
    check("durable count documents the convention",
          one("select count(distinct security_durable_key) from dim_security")
          == real_entities + len(member_keys),
          f"{real_entities} real entities, filter is >= 0; unfiltered exceeds by {len(member_keys)} members")
    check("a declared domain is not violated by a member", one("""
        select count(*) from dim_security where security_key < 0 and sector_code is not null
        and sector_code not in ('ENERGY','FIN','HLTH','INDU','INFT','MATR','REAL','UTIL','CONS','COMM')""") == 0)

    print("\nGenerated dimensions")
    date_members = one("select count(*) from dim_date where date_key < 0")
    time_members = one("select count(*) from dim_time where time_key < 0")
    check("calendar real row count", one("select count(*) from dim_date where date_key >= 0") == 7670,
          "2015-01-01 to 2035-12-31 inclusive, members excluded")
    check("time of day is second grain", one("select count(*) from dim_time where time_key >= 0") == 86400)
    check("date_key is yyyymmdd", one("select date_key from dim_date where full_date = date '2026-04-15'") == 20260415)
    check("generated dimensions carry the members too",
          date_members == len(member_keys) and time_members == len(member_keys),
          f"dim_date {date_members}, dim_time {time_members}, registry {len(member_keys)}")
    check("a date key routes not_yet_occurred to a real row",
          one("select count(*) from dim_date where member_name = 'not_yet_occurred'") == 1)

    print("\nComposite and non-fan-out lookups")
    check("the fact lookup does not fan out the fact",
          one("select count(*) from fct_holdings") == 8, "8 holdings in, 8 rows out")
    check("the two-column lookup resolves every quote",
          one("select count(*) from fct_listing_quotes") == 3
          and one("select count(*) from fct_listing_quotes where listing_key >= 0") == 3,
          "3 quotes in, 3 rows out, all resolved")
    check("AAPL resolves to the version in force at the quote date", one("""
        select d.board_lot from fct_listing_quotes f join dim_listing d on d.listing_key = f.listing_key
        where f.symbol = 'AAPL' and f.quote_date = date '2026-07-01'""") == 10,
          "the 2026-06-01 version, not the 2026-01-01 one")

    print("\nMulti-source lookup joins on source_system")
    party = lambda sys: one(f"""
        select d.party_name from fct_party_events f join dim_party d on d.party_key = f.party_key
        where f.party_id = 'P1' and f.src_sys = '{sys}'""")
    check("the alpha event resolves to the alpha party", party("alpha") == "Alpha Partners",
          f"got {party('alpha')}")
    check("the beta event resolves to the beta party", party("beta") == "Beta Brothers",
          f"got {party('beta')}")
    check("the colliding business key does not fan out the fact",
          one("select count(*) from fct_party_events") == 2, "2 events in, 2 rows out")

    # The default derivation: a source with no effective dates, versioned by hashing the Type 2
    # columns per batch. P1 changes a Type 2 attribute and versions; P2 changes only a Type 1.
    print("\nChange detection")
    real = "product_key >= 0"
    check("a Type 2 change opens a version",
          one(f"select count(*) from dim_product_cd where product_id='P1' and {real}") == 2,
          "HARDWARE then SOFTWARE")
    check("a Type 1 only change opens no version",
          one(f"select count(*) from dim_product_cd where product_id='P2' and {real}") == 1,
          "name changed, category did not")
    check("no overlapping versions (change detection)", one(f"""
        select count(*) from dim_product_cd a join dim_product_cd b
          on a.product_id = b.product_id and a.product_key <> b.product_key
         and a.valid_from < b.valid_to and b.valid_from < a.valid_to
         where a.{real} and b.{real}""") == 0)
    check("no gaps between versions (change detection)", one(f"""
        select count(*) from (
          select valid_to, lead(valid_from) over (partition by product_id order by valid_from) nxt
          from dim_product_cd where {real}) t
        where nxt is not null and nxt <> valid_to""") == 0)
    check("exactly one open version per key (change detection)", one(f"""
        select count(*) from (
          select product_id, sum(case when is_current then 1 else 0 end) n
          from dim_product_cd where {real} group by 1) t where n <> 1""") == 0)
    check("durable key stable across versions (change detection)", one(f"""
        select count(*) from (
          select product_id, count(distinct product_durable_key) n
          from dim_product_cd where {real} group by 1) t where n <> 1""") == 0)


def structural_checks(one):
    """Properties of the emitted DuckDB artifact, not of the data: tests generated, filters,
    join shape. Platform-independent in intent, but they read the DuckDB build tree, so they
    run only here, not against a transpiled tree."""
    print("\nGenerated tests and routes")
    fct_yml = yaml.safe_load((BUILD / "models" / "fct_holdings.yml").read_text())
    sk_tests = next(c["tests"] for m in fct_yml["models"]
                    for c in m["columns"] if c["name"] == "security_key")
    check("error route on a non-nullable fact key is tested unconditionally",
          "not_null" in sk_tests,
          "plain not_null, no where config, is what makes error stop the build")
    check("overlap invariant generated on the dimension",
          (BUILD / "tests" / "dim_security_no_overlap.sql").exists())
    check("gap invariant generated on the dimension",
          (BUILD / "tests" / "dim_security_no_gaps.sql").exists())
    artifacts = (list((BUILD / "models").glob("*.sql")) + list((BUILD / "models").glob("*.yml"))
                 + list((BUILD / "tests").glob("*.sql")))
    offenders = [f.name for f in artifacts if "> 0" in f.read_text()]
    check("no generated artifact uses > 0 as a member filter", not offenders,
          f"offenders: {offenders}" if offenders else "every real-member filter is >= 0")
    quotes_sql = (BUILD / "models" / "fct_listing_quotes.sql").read_text()
    check("the composite join has one equality per key column",
          "d0.exchange_code = q.mic" in quotes_sql and "d0.ticker = q.symbol" in quotes_sql,
          "exchange_code = mic and ticker = symbol, resolved from the dimension not the suffix")
    events_sql = (BUILD / "models" / "fct_party_events.sql").read_text()
    check("the lookup join includes the source_system predicate",
          "d0.source_system = ev.src_sys" in events_sql)


def honored_on_duckdb(one):
    """on_error: null is honored on DuckDB (try_cast) and substituted on PostgreSQL, so its
    fixture is DuckDB only. The null path is observed, not merely emitted: the placeholder row
    carries null because the cast failed and was caught, and the real row carries its rate. This
    is the coverage PostgreSQL cannot have, kept on the platform that honors the construct."""
    print("\non_error: null, verified on the platform that honors it (DuckDB)")
    check("a failed cast becomes null, not a build failure",
          one("select haircut_pct from dim_rate_card where rate_id = 'HC-BBB' and rate_key >= 0") is None,
          "try_cast caught the non-numeric placeholder")
    check("a valid value still casts",
          float(one("select haircut_pct from dim_rate_card where rate_id = 'HC-AAA' and rate_key >= 0")) == 0.05)


def land_duckdb(con, build=BUILD):
    """dbt transforms; it does not ingest, so landing is deliberately separate. The DuckDB
    harness reads the generated read_csv landing views directly."""
    landing = sorted((build / "landing").glob("*.sql"))
    for f in landing:
        sql = f.read_text()
        for stmt in [s for s in sql.split(";")
                     if any(l.strip() and not l.strip().startswith("--") for l in s.split("\n"))]:
            con.execute(stmt.replace("seeds/", str(HERE / "seeds") + "/"))
    return len(landing)


def main():
    con = duckdb.connect()
    print(f"landed {land_duckdb(con)} source(s)")

    done, models = build_models(BUILD / "models",
                                lambda n, s: con.execute(f"create or replace table {n} as\n{s}"))
    if len(done) < len(models):
        print("unresolved dependencies:", set(models) - done)
        return 1
    print(f"built {len(done)} model(s): {', '.join(sorted(done))}\n")

    q = lambda s: con.execute(s).fetchall()
    one = lambda s: q(s)[0][0]
    data_checks(q, one)
    structural_checks(one)
    honored_on_duckdb(one)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
