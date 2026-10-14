#!/usr/bin/env python3
"""Tier-2 platforms are parse-checked only. The Spark cannot execute Snowflake, SQL Server,
Oracle or DB2, so a successful sqlglot parse in the target dialect is a syntax check, never
a proof the SQL means what the spec said. DB2 has no sqlglot dialect and cannot be parsed at
all, which is stated rather than papered over.

Also asserts, for every transpiled platform, that the change_hash came from the registry and
not from sqlglot: sqlglot leaves sha256 untranslated, so a plausible substitution would make
every hash differ from the previous run and re-version the whole estate silently.
"""
import pathlib
import subprocess
import sys
import tempfile

import sqlglot
import yaml

import run

ROOT = pathlib.Path(__file__).parent.parent
TRANSPILE = yaml.safe_load((ROOT / "registry" / "platforms.yml").read_text())["transpilation"]
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def emit(platform, out):
    subprocess.run([sys.executable, str(ROOT / "emit.py"),
                    "--models", str(ROOT / "acceptance" / "models"),
                    "--out", str(out), "--platform", platform],
                   check=True, capture_output=True, text=True)


def main():
    tmp = pathlib.Path(tempfile.mkdtemp())
    emitted = {}

    # Hash provenance, every transpiled platform. The registry wrapper's literal prefix is
    # present; sqlglot's rendering (bare SHA256) would not be.
    print("Hash provenance (registry, never sqlglot)")
    for p in [k for k in TRANSPILE if k != "duckdb"]:
        out = tmp / p
        emit(p, out)
        emitted[p] = out
        sec = (out / "models" / "dim_security.sql").read_text()
        prefix = TRANSPILE[p]["hash"]["sha256"].split("{inner}")[0]
        check(f"{p}: change_hash from the registry",
              prefix in sec and "SHA256(" not in sec.upper().replace(prefix.upper(), ""),
              f"wrapper {prefix!r}")

    # Tier-2 parse check. Syntax only.
    print("\nTier-2 parse check (syntax only, not execution)")
    for p, e in TRANSPILE.items():
        if e["tier"] != 2:
            continue
        dialect = e["dialect"]
        out = emitted[p]
        bad = []
        for f in sorted((out / "models").glob("*.sql")):
            try:
                sqlglot.parse_one(run.resolve(f.read_text()), dialect=dialect)
            except Exception as ex:
                bad.append(f"{f.stem} ({str(ex).splitlines()[0]})")
        check(f"{p}: all models parse in {dialect}", not bad, "; ".join(bad))

    # Tier 3: no sqlglot dialect, so not even parse-checked. Named, not silently skipped.
    for p, e in TRANSPILE.items():
        if e["tier"] == 3:
            print(f"\nTier 3 (not checked at all): {p} has no sqlglot dialect, so it is emitted "
                  f"but neither executed nor parsed. A dialect or an engine would move it up.")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed (transpilation)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
