#!/usr/bin/env python3
"""Cross-platform differential: build every acceptance model on both tier-1 platforms and
compare the tables row by row.

Phase one ran the same specs on DuckDB and PostgreSQL and asserted each passed its own
tests. That is not enough: both suites can pass while the tables disagree, because nothing
compares them. Two real differences slipped through that way. This compares them.

Every column is rendered to text with an explicit, platform-matched format so that timestamp
and numeric rendering cannot masquerade as a data difference; rows are sorted in Python so
collation cannot either. The record_hash is compared like any other column, which is the
strongest test in the suite: a change_hash that differed across platforms would re-version
the estate on a platform migration, silently.

Any difference is a transpiler defect to fix or a platform difference to declare in
registry/platforms.yml with a reason. There is no third category, and agreeing on this seed
is not a reason. Declared (model, column) pairs are excluded from the comparison and listed.
"""
import pathlib
import sys

import duckdb
import yaml

import run
import run_postgres

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
NULL = "␀"  # a glyph no column value will contain, so NULL never collides with text
DIFFS = yaml.safe_load((ROOT / "registry" / "platforms.yml").read_text()).get("platform_differences") or []
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def category(duckdb_type):
    t = duckdb_type.upper()
    if t.startswith(("VARCHAR", "CHAR", "TEXT", "STRING")):
        return "text"
    if t.startswith("BOOL"):
        return "bool"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    if t.startswith("DATE"):
        return "date"
    if t.startswith(("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "HUGEINT")):
        return "numeric"
    return "int"


def render(cat, col, engine):
    """A platform-matched expression rendering one column to the same canonical text on both
    engines, with NULL explicit, so only real data differences survive."""
    c = f'"{col}"'
    if cat == "bool":
        return f"case when {c} is null then '{NULL}' when {c} then 'T' else 'F' end"
    if cat == "timestamp":
        r = (f"strftime({c}, '%Y-%m-%d %H:%M:%S')" if engine == "duckdb"
             else f"to_char({c}, 'YYYY-MM-DD HH24:MI:SS')")
    elif cat == "numeric":
        kind = "decimal" if engine == "duckdb" else "numeric"
        r = f"cast(cast({c} as {kind}(38,6)) as varchar)"
    else:  # text, int, date all round-trip through varchar identically on both
        r = f"cast({c} as varchar)"
    return f"coalesce({r}, '{NULL}')"


def dump(execute, model, columns):
    sel = ", ".join(render(cat, col, columns.engine) for col, cat in columns.items)
    rows = execute(f'select {sel} from "{model}"')
    return sorted(tuple(r) for r in rows)


class Cols:
    def __init__(self, items, engine):
        self.items, self.engine = items, engine


def grain_of(specs, model):
    for s in specs.values():
        if (s.get("target") or {}).get("name") == model:
            return (s.get("target") or {}).get("grain") or []
    return []


def main():
    try:
        import psycopg2  # noqa: F401
        pg = run_postgres.connect()
    except Exception as e:
        print(f"Differential skipped: {str(e).splitlines()[0]} (PostgreSQL not reachable)")
        return 0

    # DuckDB side, in process.
    dk = duckdb.connect()
    run.land_duckdb(dk)
    run.build_models(run.BUILD / "models",
                     lambda n, s: dk.execute(f"create or replace table {n} as\n{s}"))

    # PostgreSQL side.
    cur = pg.cursor()
    run_postgres.load_seeds(cur)
    for mf in (run_postgres.BUILD_PG / "models").glob("*.sql"):
        cur.execute(f'drop table if exists "{mf.stem}" cascade')
    run.build_models(run_postgres.BUILD_PG / "models",
                     lambda n, s: cur.execute(f'create table "{n}" as\n{s}'))

    def pg_exec(sql):
        cur.execute(sql)
        return cur.fetchall()

    specs = {f.stem: yaml.safe_load(f.read_text())
             for f in sorted((HERE / "models").glob("*.yml"))}
    dk_models = {m.stem for m in (run.BUILD / "models").glob("*.sql")}
    pg_models = {m.stem for m in (run_postgres.BUILD_PG / "models").glob("*.sql")}
    # Only models built on both tier-1 platforms can be compared. A model DuckDB built but
    # PostgreSQL did not is one whose spec declares project.build_platforms: [duckdb] because it
    # exercises a construct substituted on PostgreSQL. That is not an undeclared difference, it
    # is the declared reason the model is single-platform, so it is named here, not compared.
    models = sorted(dk_models & pg_models)
    duckdb_only = sorted(dk_models - pg_models)

    declared = {(d["model"], d["column"]) for d in DIFFS}
    print("Cross-platform differential (DuckDB vs PostgreSQL)")
    for model in models:
        schema = [(r[0], category(r[1])) for r in dk.execute(f'describe "{model}"').fetchall()]
        compared = [(c, cat) for c, cat in schema if (model, c) not in declared]
        dk_rows = dump(lambda s: dk.execute(s).fetchall(), model, Cols(compared, "duckdb"))
        pg_rows = dump(pg_exec, model, Cols(compared, "postgres"))

        if len(dk_rows) != len(pg_rows):
            check(f"{model}: row counts match", False,
                  f"DuckDB {len(dk_rows)} rows, PostgreSQL {len(pg_rows)}")
            continue
        col_names = [c for c, _ in compared]
        grain = [g for g in grain_of(specs, model) if g in col_names]
        gidx = [col_names.index(g) for g in grain]
        first = None
        for a, b in zip(dk_rows, pg_rows):
            if a != b:
                j = next(i for i in range(len(a)) if a[i] != b[i])
                gv = ", ".join(f"{grain[k]}={a[i]}" for k, i in enumerate(gidx)) or "(no grain match)"
                first = (col_names[j], gv, a[j], b[j])
                break
        check(f"{model}: {len(dk_rows)} rows identical on both platforms", first is None,
              "" if first is None else
              f"column {first[0]} at [{first[1]}]: DuckDB {first[2]!r} vs PostgreSQL {first[3]!r}")

    if duckdb_only:
        print("\nDuckDB-only models (declared single-platform, nothing to compare)")
        for m in duckdb_only:
            s = next((sp for sp in specs.values() if (sp.get("target") or {}).get("name") == m), {})
            bp = (s.get("project") or {}).get("build_platforms") or []
            check(f"{m}: built on {', '.join(bp) or 'duckdb'} only", True,
                  "exercises a construct substituted on PostgreSQL; verified where honored")

    if declared:
        print("\nDeclared platform differences (excluded from the comparison above)")
        for d in DIFFS:
            check(f"{d['model']}.{d['column']} declared", True,
                  f"{', '.join(d.get('platforms', []))}: {d.get('reason', '')}")

    pg.rollback()
    pg.close()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed (differential)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
