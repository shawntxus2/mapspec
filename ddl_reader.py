#!/usr/bin/env python3
"""mapspec read-ddl - turn dimension and fact DDL into a skeleton spec.

Every transform comes out `unresolved`. The reader never guesses a transformation; it
fills in what the DDL actually states (columns, types, keys, constraints) and leaves the
mapping to a human. Coverage therefore starts near zero and climbs as transforms are
filled in, which is the number worth watching.

Two things it does that a naive reader does not:

  Types are normalized platform-qualified. Oracle DATE is a date plus a time to the
  second, not an ANSI DATE, so it lands as timestamp(0) rather than silently losing the
  time component on the way to the target platform.

  Type 2 system columns are lifted, not mapped. valid_from, valid_to, is_current,
  version_no, the hashes and the dw_ audit columns belong in the scd contract. Emitting
  them as mappable columns is how a mapping sheet ends up with twelve rows nobody fills in.
"""
import argparse
import pathlib
import re
import sys

import sqlglot
from sqlglot import exp

# --- names the generator owns. Lifted into the scd contract, never emitted as columns.
VALIDITY = {
    "valid_from": "effective_from",
    "valid_to": "effective_to",
    "effective_from": "effective_from",
    "effective_to": "effective_to",
    "is_current": "current_flag",
    "current_flag": "current_flag",
    "version_no": "version_number",
    "version_number": "version_number",
    "record_hash": "change_hash",
    "change_hash": "change_hash",
    "attribute_hash": "attribute_hash",
}
AUDIT = {
    "dw_load_ts": "loaded_at",
    "dw_source_system": "source_system",
    "dw_tzdata_version": "tzdata_version",
}

LAYER_BY_PREFIX = [
    ("dim_", "dimension"), ("d_", "dimension"),
    ("fct_", "fact"), ("fact_", "fact"), ("f_", "fact"),
    ("stg_", "staging"), ("br_", "bridge"), ("bridge_", "bridge"),
]


class Note:
    def __init__(self):
        self.inferred = []
        self.lifted = []
        self.warnings = []


def layer_of(table, override):
    if override:
        return override
    for pre, layer in LAYER_BY_PREFIX:
        if table.lower().startswith(pre):
            return layer
    return None


def render_type(dt, dialect, target, note, column):
    """Platform-qualified normalization. The source keyword is not the source meaning."""
    if dt is None:
        return "varchar", None
    src = dt.sql(dialect=dialect).lower()

    # Oracle DATE carries a time to the second. ANSI DATE does not.
    if dialect == "oracle" and dt.this == exp.DataType.Type.DATE:
        note.warnings.append(
            f"{column}: Oracle DATE normalized to timestamp(0), not date. "
            f"Oracle DATE carries a time to the second; mapping it to a target DATE "
            f"would discard it silently."
        )
        return "timestamp(0)", src

    # NUMBER(p) and NUMBER(p,0) are integers wearing a decimal keyword.
    if dt.this in (exp.DataType.Type.DECIMAL, exp.DataType.Type.BIGDECIMAL):
        params = [p.name for p in dt.expressions if p.name.isdigit()]
        if len(params) <= 1 or (len(params) == 2 and params[1] == "0"):
            p = int(params[0]) if params else 38
            out = "smallint" if p <= 4 else "int" if p <= 9 else "bigint"
            note.inferred.append(f"{column}: {src} has scale 0, normalized to {out}")
            return out, src

    return canon(dt, src), src


# The spec carries a logical type. Platform rendering is the emitter's job, so a
# varchar stays a varchar rather than becoming whatever the target happens to call it.
_TEXTY = {exp.DataType.Type.VARCHAR, exp.DataType.Type.TEXT, exp.DataType.Type.NVARCHAR}
_CHARY = {exp.DataType.Type.CHAR, exp.DataType.Type.NCHAR}


def canon(dt, src):
    t = dt.this
    params = [p.name for p in dt.expressions if p.name.isdigit()]
    if t in _TEXTY:
        return f"varchar({params[0]})" if params else "varchar"
    if t in _CHARY:
        return f"char({params[0]})" if params else "char(1)"
    if t in (exp.DataType.Type.DECIMAL, exp.DataType.Type.BIGDECIMAL, exp.DataType.Type.DOUBLE):
        if len(params) == 2:
            return f"numeric({params[0]},{params[1]})"
        return f"numeric({params[0]})" if params else "numeric(38,9)"
    if t in (exp.DataType.Type.INT, exp.DataType.Type.MEDIUMINT):
        return "int"
    if t == exp.DataType.Type.BIGINT:
        return "bigint"
    if t == exp.DataType.Type.SMALLINT:
        return "smallint"
    if t == exp.DataType.Type.DATE:
        return "date"
    if t in (exp.DataType.Type.TIMESTAMP, exp.DataType.Type.TIMESTAMPNTZ):
        return f"timestamp({params[0]})" if params else "timestamp(6)"
    if t in (exp.DataType.Type.TIMESTAMPTZ, exp.DataType.Type.TIMESTAMPLTZ):
        return f"timestamptz({params[0]})" if params else "timestamptz(6)"
    if t == exp.DataType.Type.BOOLEAN:
        return "boolean"
    return src


def check_allowed_values(create):
    """CHECK (col IN (...)) becomes allowed_values, which becomes an accepted_values test."""
    out = {}
    for chk in create.find_all(exp.Check):
        e = chk.this
        if isinstance(e, exp.In) and isinstance(e.this, exp.Column):
            vals = [x.name for x in e.expressions if isinstance(x, (exp.Literal,))]
            if vals:
                out[e.this.name.lower()] = vals
    return out


def table_keys(create):
    pk, uniques, fks = [], [], {}
    schema = create.this
    for c in schema.expressions:
        if isinstance(c, exp.PrimaryKey):
            pk = [x.name.lower() for x in c.expressions]
        elif isinstance(c, exp.UniqueColumnConstraint):
            cols = c.find(exp.Schema)
            if cols:
                uniques.append([x.name.lower() for x in cols.expressions])
        elif isinstance(c, exp.Constraint):
            for part in c.expressions:
                if isinstance(part, exp.PrimaryKey):
                    pk = [x.name.lower() for x in part.expressions]
                elif isinstance(part, exp.UniqueColumnConstraint):
                    sc = part.find(exp.Schema) or part
                    uniques.append([x.name.lower() for x in sc.expressions])
                elif isinstance(part, exp.ForeignKey):
                    local = [x.name.lower() for x in part.expressions]
                    ref = part.args.get("reference")
                    if ref is not None:
                        rs = ref.find(exp.Schema)
                        rtab = rs.this.name.lower() if rs is not None else None
                        rcols = [x.name.lower() for x in rs.expressions] if rs is not None else []
                        for i, lc in enumerate(local):
                            fks[lc] = (rtab, rcols[i] if i < len(rcols) else lc)
    return pk, uniques, fks


def q(s):
    return s if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s or "") else f'"{s}"'


def build(path, dialect, target, layer_override):
    sql = pathlib.Path(path).read_text()
    create = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(create, exp.Create):
        raise SystemExit(f"{path}: expected a CREATE TABLE statement")

    schema_exp = create.this
    tbl = schema_exp.this
    table = tbl.name.lower()
    db = tbl.db.lower() or None
    note = Note()

    layer = layer_of(table, layer_override)
    if layer is None:
        layer = "dimension"
        note.warnings.append(
            f"table name '{table}' matches no layer prefix; assumed dimension. "
            f"Pass --layer to set it."
        )
    else:
        if not layer_override:
            note.inferred.append(f"layer '{layer}' from the table name prefix")

    pk, uniques, fks = table_keys(create)
    allowed = check_allowed_values(create)

    cols, scd_map, audit_map = [], {}, {}
    for cd in schema_exp.expressions:
        if not isinstance(cd, exp.ColumnDef):
            continue
        name = cd.name.lower()
        if name in VALIDITY:
            scd_map[VALIDITY[name]] = name
            note.lifted.append(f"{name} -> scd.{VALIDITY[name]}")
            continue
        if name in AUDIT:
            audit_map[AUDIT[name]] = name
            note.lifted.append(f"{name} -> scd.audit.{AUDIT[name]}")
            continue
        notnull = any(isinstance(c.kind, exp.NotNullColumnConstraint) for c in cd.constraints)
        typ, src = render_type(cd.args.get("kind"), dialect, target, note, name)
        cols.append(dict(name=name, type=typ, source_type=src, nullable=not notnull))

    durable = next((c["name"] for c in cols if c["name"].endswith("_durable_key")), None)
    surrogate = pk[0] if len(pk) == 1 and pk[0].endswith("_key") else None
    if layer == "dimension" and surrogate is None and pk:
        note.warnings.append(
            f"primary key {tuple(pk)} does not look like a single surrogate key; "
            f"surrogate_key left for you to declare"
        )

    natural = []
    for u in uniques:
        rest = [c for c in u if c not in VALIDITY and c != durable]
        if rest:
            natural = rest
            note.inferred.append(f"natural_key {rest} from unique constraint {tuple(u)}")
            break
    if layer == "dimension" and not natural:
        note.warnings.append(
            "no natural key stated in the DDL. The unique constraints describe the version "
            "grain, not the business identifier, so natural_key is left for you to declare."
        )

    # ---- emit
    L = []
    A = L.append
    A("spec_version: 1")
    A("project:")
    A(f"  target_platform: {target}")
    A("")
    A(f"# Generated by mapspec read-ddl from {pathlib.Path(path).name} ({dialect}).")
    A("# Every transform is unresolved on purpose. The reader states what the DDL states")
    A("# and guesses no mappings. Fill transforms in and watch coverage climb.")
    A("")
    A("target:")
    A(f"  layer: {layer}")
    A(f"  name: {table}")
    if db:
        A(f"  schema: {db}")
    A(f"  platform: {target}")
    if layer == "dimension" and natural:
        A(f"  natural_key: [{', '.join(natural)}]")
        A("  natural_key_stability: stable        # TODO confirm. unstable forces a crosswalk durable key.")
    if layer == "fact" and pk:
        A(f"  grain: [{', '.join(pk)}]")
        A("  # TODO as_of: \"<the single point-in-time column>\"   required by any Type 2 lookup")
        A("  # TODO as_of_promotion: start_of_day | end_of_day      when as_of is a date")

    if layer == "dimension" and (scd_map or surrogate):
        A("")
        A("scd:")
        A("  type: 2")
        A("  derivation: change_detection         # | source_provided | attribute_timelines")
        if surrogate:
            A("  surrogate_key:")
            A(f"    name: {surrogate}")
            A("    strategy: sequence")
            A("    # TODO cardinality: { entities: ?, avg_versions: ? }  then the type is checked")
            A("    reserved_members: -8")
        for k in ("effective_from", "effective_to", "current_flag", "version_number",
                  "change_hash", "attribute_hash"):
            if k in scd_map:
                A(f"  {k}: {scd_map[k]}")
        A('  bound: "[from, to)"')
        A('  open_sentinel: "9999-12-31 00:00:00.000"')
        A("  current_view:")
        A("    generate: true")
        A(f"    name: {table}_current")
        if audit_map:
            A("  audit:")
            for k, v in audit_map.items():
                A(f"    {k}: {v}")
        A("  unknown_member:")
        A("    surrogate_key: -1")
        if durable:
            A("    durable_key: -1")

    if durable:
        A("")
        A("durable_key:")
        A(f"  name: {durable}")
        A("  strategy: crosswalk                  # TODO hash_natural_key only if the key is stable")
        A(f"  crosswalk: {table}_xref")
        A("  on_unknown: assign_new")
        A("  merge_policy: survivor_pointer")

    A("")
    A("sources: []                              # TODO declare the sources and their joins")
    A("")
    A("columns:")
    for c in cols:
        A(f"  - name: {c['name']}")
        A(f"    type: {c['type']}")
        if c["source_type"] and c["source_type"] != c["type"]:
            A(f"    description: \"source type {c['source_type']} ({dialect})\"")
        if not c["nullable"]:
            A("    nullable: false")
        if c["name"] in fks:
            rtab, rcol = fks[c["name"]]
            A("    role: foreign_key")
            A(f"    references: {{ model: {rtab}, column: {rcol} }}")
        elif c["name"] in pk:
            A("    role: primary_key")
        elif c["name"] in natural:
            A("    role: natural_key")
        if c["name"] in allowed:
            A(f"    allowed_values: [{', '.join(allowed[c['name']])}]")
        if layer == "dimension" and c["name"] != surrogate and c["name"] != durable:
            A("    scd_behavior: { type: 2 }            # TODO 0 1 2 3 or 6")
        A("    transform:")
        A("      kind: unresolved")
        A(f'      note: "not mapped. {c["name"]} came from DDL; the source and rule are unknown."')
        A(f'      raised_by: "read-ddl:{pathlib.Path(path).name}"')
    return "\n".join(L) + "\n", note, len(cols)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ddl", nargs="+")
    ap.add_argument("--dialect", required=True)
    ap.add_argument("--target", default="duckdb")
    ap.add_argument("--layer", choices=["staging", "dimension", "fact", "bridge"])
    ap.add_argument("--out", default="models")
    a = ap.parse_args()

    outdir = pathlib.Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    for f in a.ddl:
        text, note, ncols = build(f, a.dialect, a.target, a.layer)
        name = sqlglot.parse_one(pathlib.Path(f).read_text(), read=a.dialect).this.this.name.lower()
        dest = outdir / f"{name}.skeleton.yml"
        dest.write_text(text)
        print(f"\n{f}  ->  {dest}")
        print(f"  {ncols} mappable columns, all unresolved")
        for x in note.lifted:
            print(f"  lifted    {x}")
        for x in note.inferred:
            print(f"  inferred  {x}")
        for x in note.warnings:
            print(f"  WARNING   {x}")


if __name__ == "__main__":
    sys.exit(main())
