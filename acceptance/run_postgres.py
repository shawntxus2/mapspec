#!/usr/bin/env python3
"""Acceptance against PostgreSQL, the second tier-1 platform.

Same specs, same assertions as run.py, a different engine. DuckDB proves the SQL mapspec
writes; PostgreSQL proves the transpiler preserved its meaning rather than its syntax, which
is the only thing adding a second executable platform can prove. Both green, or the
transpiler is wrong.

The models are emitted with --platform postgres into acceptance/build_pg. Seeds are loaded
with COPY rather than DuckDB's native read_csv. The data_checks are imported from run.py so
the two platforms cannot drift apart; the structural checks read the DuckDB tree and stay
there.

Connection comes from the standard PG* environment variables, defaulting to the local
container make brings up. If psycopg2 or the server is absent the run is skipped, not
failed, so a machine without Postgres is not blocked, only unverified.
"""
import os
import pathlib
import sys

import yaml

import run  # shared check/data_checks/build_models/resolve

HERE = pathlib.Path(__file__).parent
BUILD_PG = HERE / "build_pg"
SEEDS = HERE / "seeds"


def connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "55432")),
        dbname=os.environ.get("PGDATABASE", "mapspec"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "mapspec"),
        connect_timeout=5,
    )


def load_seeds(cur):
    """Create a text staging table per declared delimited source and COPY the seed in. This
    reads the specs, but only for setup: the loader has to know column names and the file. The
    assertions below still read nothing but the data. Empty fields load as NULL to match
    DuckDB's read_csv, so the on_null_key arm resolves the same on both platforms."""
    loaded = 0
    for f in sorted((HERE / "models").glob("*.yml")):
        spec = yaml.safe_load(f.read_text())
        # A spec that does not build on PostgreSQL (project.build_platforms) has no model here,
        # so its seed would be a staging table nothing reads. Skip it.
        bp = (spec.get("project") or {}).get("build_platforms")
        if bp and "postgres" not in bp:
            continue
        for s in spec.get("sources") or []:
            if s.get("kind") != "delimited" or not s.get("uri"):
                continue
            alias = s["alias"]
            cols = [p["name"] for p in s.get("projection", [])]
            cur.execute(f'drop table if exists "{alias}" cascade')
            cur.execute(f'create table "{alias}" (' + ", ".join(f'"{c}" text' for c in cols) + ")")
            path = SEEDS / pathlib.Path(s["uri"]).name
            with open(path) as fh:
                cur.copy_expert(
                    f"copy \"{alias}\" from stdin with (format csv, delimiter '{s['delimiter']}', "
                    f"header true, null '')", fh)
            loaded += 1
    return loaded


def main():
    try:
        import psycopg2  # noqa: F401
        con = connect()
    except Exception as e:
        print(f"PostgreSQL acceptance skipped: {str(e).splitlines()[0]}")
        print("  (set PGHOST/PGPORT or run `make check-postgres` which starts the container)")
        return 0

    cur = con.cursor()
    loaded = load_seeds(cur)
    print(f"landed {loaded} source(s) into PostgreSQL")

    # Drop model tables so the run is repeatable, then build in dependency order.
    for mf in (BUILD_PG / "models").glob("*.sql"):
        cur.execute(f'drop table if exists "{mf.stem}" cascade')
    done, models = run.build_models(BUILD_PG / "models",
                                    lambda n, s: cur.execute(f'create table "{n}" as\n{s}'))
    if len(done) < len(models):
        print("unresolved dependencies:", set(models) - done)
        return 1
    print(f"built {len(done)} model(s) on PostgreSQL: {', '.join(sorted(done))}\n")

    def q(s):
        cur.execute(s)
        return cur.fetchall()
    one = lambda s: q(s)[0][0]

    run.data_checks(q, one)
    con.rollback()
    con.close()

    print(f"\n{len(run.PASS)} passed, {len(run.FAIL)} failed (PostgreSQL)")
    return 1 if run.FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
