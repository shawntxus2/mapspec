#!/usr/bin/env python3
"""mapspec emit - write dbt models, tests and a coverage report from the specs.

What it will not do is guess. A column whose transform is `unresolved` emits a TODO
comment in place of an expression and is counted against coverage, and a derivation the
generator does not implement yet emits a stub model that says so. The coverage report
accounts for every column it did not write.

Generated per spec:
  models/sources.yml       dbt source declarations
  models/<name>.sql        the model
  models/<name>.yml        tests derived from declared constraints, not inferred from data
  landing/<name>.sql       external table or stage DDL, where a file source needs one
  coverage.md              what was generated, what was not, and why
"""
import argparse
import json
import pathlib
import re
import sys

import sqlglot
from sqlglot import exp
import yaml

import constructs

ROOT = pathlib.Path(__file__).parent
SCHEMA = json.loads((ROOT / "schema" / "mapspec.schema.json").read_text())
PLATFORMS = yaml.safe_load((ROOT / "registry" / "platforms.yml").read_text())
TRANSPILE = PLATFORMS["transpilation"]
GEN_TEMPLATES = PLATFORMS["generator_templates"]
SENTINEL = "9999-12-31 00:00:00.000"
# Member rows open at a floor well below any real effectivity so a fact resolving as-of any
# instant finds the row. A member outside the half-open interval is a null foreign key, the
# one failure the members exist to prevent.
MEMBER_FLOOR_TS = "1900-01-01 00:00:00.000"
# No sentinel code lives in a module constant: that was the UNKNOWN_MEMBER_KEY mistake, one
# layer in. Codes come from the registry, read from the schema so the default cannot drift.
DEFAULT_SPECIAL_MEMBERS = SCHEMA["$defs"]["specialMembers"]["default"]
# dbt-duckdb's default newstyle formatter runs str.format_map on external_location, which
# collides with the literal braces in a DuckDB columns struct. template uses string.Template,
# where only $ is special, so the generated read_csv passes through verbatim.
EXTERNAL_LOCATION_FORMATTER = "template"


class TranspileError(Exception):
    """sqlglot could not parse an authored expression, or the target has no equivalent. The
    column it belongs to downgrades to unresolved rather than ship a guess."""


def _faithful_year_datediff(node):
    """DuckDB date_diff('year', start, end) is the year-boundary count, year(end) - year(start).
    sqlglot renders it on other dialects as elapsed years through age(), which disagrees when
    the end's month and day fall before the start's. Rewrite it to the boundary count so the
    value, and the change_hash it feeds, is identical across platforms. sqlglot parses the
    call as DateDiff(this=end, expression=start)."""
    if isinstance(node, exp.DateDiff):
        yr = lambda e: exp.Extract(this=exp.var("YEAR"), expression=e.copy())
        return exp.Paren(this=exp.Sub(this=yr(node.this), expression=yr(node.expression)))
    return node


class Dialect:
    """One target platform's translation rules, from registry/platforms.yml. DuckDB is the
    canonical source dialect and is emitted verbatim, so its output is byte-for-byte what it
    was before transpilation existed. Every other dialect routes expression-level SQL through
    sqlglot and reads its hash function and generator templates from the registry, never from
    sqlglot, because sqlglot leaves sha256 untranslated and a wrong hash is silent."""

    def __init__(self, platform):
        if platform not in TRANSPILE:
            raise ValueError(f"platform '{platform}' has no transpilation entry in the registry")
        e = TRANSPILE[platform]
        self.platform = platform
        self.tier = e["tier"]
        self.sqlglot = e.get("dialect")
        self.hash_templates = e.get("hash") or {}
        gen = e.get("generators")
        if gen not in GEN_TEMPLATES:
            raise ValueError(f"platform '{platform}' declares no generator templates")
        self.gen = GEN_TEMPLATES[gen]

    def translate(self, expr):
        if self.sqlglot in (None, "duckdb"):
            return expr
        try:
            tree = sqlglot.parse_one(expr, read="duckdb")
        except Exception as e:
            raise TranspileError((str(e).splitlines() or [repr(e)])[0])
        # date_diff feeds change_hash through the Type 2 columns it computes, so a translation
        # that merely parses is not enough: it must compute the same integer, or the hash
        # differs across platforms and the estate re-versions on a migration. sqlglot renders
        # date_diff('year', a, b) as extract(year from age(...)), elapsed years, which differs
        # from DuckDB's year-boundary count. year is rewritten to the boundary count by hand;
        # any other unit has no vouched-for translation and downgrades to unresolved.
        for d in tree.find_all(exp.DateDiff):
            unit = (d.args.get("unit").name if d.args.get("unit") else "").lower()
            if unit != "year":
                raise TranspileError(f"date_diff unit '{unit}' has no faithful {self.platform} translation")
        tree = tree.transform(_faithful_year_datediff)
        try:
            return tree.sql(dialect=self.sqlglot)
        except Exception as e:
            raise TranspileError((str(e).splitlines() or [repr(e)])[0])

    def hash_wrap(self, algo, inner):
        tmpl = self.hash_templates.get(algo)
        if not tmpl:
            raise ValueError(f"platform '{self.platform}' declares no {algo} hash function; a hash "
                             f"with no declared function is an error, not a fallback")
        return tmpl.format(inner=inner)


DIALECT = Dialect("duckdb")


def special_members(spec):
    """The registry a lookup failure resolves against: the spec's project override, else
    the schema defaults, with a missing top-level key falling to the default."""
    sm = (spec.get("project") or {}).get("special_members") or {}
    d = DEFAULT_SPECIAL_MEMBERS
    return {
        "reserved_floor": sm.get("reserved_floor", d["reserved_floor"]),
        "marker_column": sm.get("marker_column", d["marker_column"]),
        "fill": {**d["fill"], **(sm.get("fill") or {})},
        "members": sm.get("members", d["members"]),
    }


def member_code(reg, route):
    """A lookup route resolves to a SQL scalar: a member's code, or a typed null. 'error'
    emits null too; the distinction from 'null' is carried by the column's nullability and
    its not_null test, which stops the build loudly rather than the SQL doing it silently."""
    if route in ("null", "error"):
        return "null"
    by_name = {m["name"]: m for m in reg["members"]}
    return str(by_name[route]["code"])


def is_text_type(t):
    return (t or "").lower().startswith(("varchar", "char", "text", "string", "nvarchar"))


def member_fill(col, m, fill):
    """What a member row carries in a declared column. Text reads as the label so a group by
    shows a category, not a blank, except where the column's contract cannot hold the label:
    a declared domain, or a declared width the label overruns. Then null, because member_name
    already carries the meaning and writing a value the contract cannot hold is worse than
    absence. Everything else is null; zero is a number someone will sum, and a Type 0 column
    is asserted rather than compared."""
    typ = col["type"]
    if col.get("allowed_values") or btype(col) == 0:
        val = "null"
    elif is_text_type(typ) and fill.get("text", "label") == "label":
        label = m.get("label") or m["name"]
        # A declared width is a contract like allowed_values. Write the label when it fits;
        # otherwise null it, never truncate. Truncating makes both platforms agree on a value
        # that is not the member's label and, on a narrow column, on an unreadable fragment
        # that could collide with real data, which is exactly the plausible-wrong-value SUB001
        # exists to stop. (DuckDB ignores varchar(n) length and PostgreSQL enforces it, so an
        # overrunning label is also where the two platforms silently diverge.)
        width = re.search(r"\(\s*(\d+)", typ)
        val = "null" if width and len(label) > int(width.group(1)) \
            else "'" + label.replace("'", "''") + "'"
    else:
        val = "null"
    return f"cast({val} as {typ})"


# ---------------------------------------------------------------- helpers

def builds_on(spec, platform):
    """Whether a spec is built on a platform. project.build_platforms is an allow-list for
    specs that exercise a construct substituted elsewhere; absent means every platform."""
    bp = (spec.get("project") or {}).get("build_platforms")
    return platform in bp if bp else True


def behavior(col):
    b = col.get("scd_behavior")
    if b is None:
        return None
    return {"type": 2 if b == "track" else 1} if isinstance(b, str) else b


def btype(col):
    b = behavior(col)
    return b.get("type") if b else None


def ind(sql, n=2):
    pad = " " * n
    return "\n".join(pad + l if l.strip() else l for l in sql.split("\n"))


class Emitted:
    def __init__(self, name):
        self.name = name
        self.files = {}
        self.singular = {}  # singular test sql, written under tests/ not models/
        self.todo = []
        self.skipped = None


# ---------------------------------------------------------------- expressions

def expr_for(col, spec):
    """One target column's SQL, or None when the generator will not write it."""
    t = col.get("transform") or {}
    k = t.get("kind")

    if k == "direct":
        return t["source"]

    if k == "cast":
        # cast_sql builds the DuckDB form; the dialect translates it, or raises to downgrade.
        return DIALECT.translate(cast_sql(t))

    if k == "expression":
        # An authored expression is the user's SQL; a dialect with no equivalent downgrades it.
        return DIALECT.translate(t["sql"])

    if k == "conditional":
        parts = ["case"]
        for c in t["cases"]:
            parts.append(f"    when {c['when']} then {c['then']}")
        if t.get("else"):
            parts.append(f"    else {t['else']}")
        parts.append("  end")
        return DIALECT.translate("\n".join(parts))

    if k == "constant":
        v = t["value"]
        return "null" if v is None else (f"'{v}'" if isinstance(v, str) else str(v))

    if k == "aggregate":
        f = t["function"].replace("count_distinct", "count")
        inner = f"distinct {t['source']}" if t["function"] == "count_distinct" else t["source"]
        return f"{f}({inner})"

    if k == "survived":
        # precedence order from the model's survivorship block
        order = survivor_order(col, spec)
        return "coalesce(" + ", ".join(order) + ")" if len(order) > 1 else order[0]

    if k == "lookup":
        return None  # emitted as a join, handled separately

    return None  # unresolved


def survivor_order(col, spec):
    surv = spec.get("survivorship") or {}
    rule = next((r for r in surv.get("rules", []) if r["column"] == col["name"]), None)
    cands = {c["source"]: c["expression"] for c in (col["transform"].get("candidates") or [])}
    if rule and rule.get("strategy") == "specific_source":
        return [cands.get(rule["source"], "null")]
    order = (rule or {}).get("precedence") or surv.get("precedence") or list(cands)
    return [cands[s] for s in order if s in cands] or list(cands.values())


def cast_sql(t):
    src, to = t["source"], t["to"]
    e = f"trim({src})" if t.get("trim") else src
    low = to.lower()
    if low.startswith("timestamp"):
        fmt = t.get("format")
        if fmt:
            e = f"strptime({e}, '{strptime_mask(fmt)}')"
        e = f"cast({e} as timestamp)"
        if t.get("from_zone") and t["from_zone"].upper() != "UTC":
            e = f"(timezone('UTC', timezone('{t['from_zone']}', {e})))"
    elif low == "date":
        fmt = t.get("format")
        e = f"cast(strptime({e}, '{strptime_mask(fmt)}') as date)" if fmt else f"cast({e} as date)"
    else:
        e = f"cast({e} as {to})"
    if t.get("on_error") == "null":
        e = f"try_{e}" if e.startswith("cast(") else e
    return e


def strptime_mask(fmt):
    """Oracle-style parse mask to strptime. Declared explicitly in the spec because an
    implicit conversion uses session settings, so one row hashes differently per session."""
    out = fmt
    for a, b in [('"T"', "T"), ("YYYY", "%Y"), ("MM", "%m"), ("DD", "%d"),
                 ("HH24", "%H"), ("MI", "%M"), ("SS", "%S"), ("FF3", "%g"), ("FF", "%g")]:
        out = out.replace(a, b)
    return out


def hash_expr(cols, policy):
    """Length-prefixed framing, then the platform's declared hash function. The framing is
    dialect-neutral string work; the function wrapper comes from the registry, never from
    sqlglot, which leaves sha256 untranslated and would silently change every change_hash.
    A platform with no declared function for the algorithm is an error, not a fallback."""
    algo = (policy or {}).get("algorithm", "sha256")
    parts = []
    for c in cols:
        # N: null, E: empty, L<len>:<value> otherwise. No literal can impersonate a null,
        # which a sentinel string can even behind a length prefix.
        parts.append(
            f"(case when {c} is null then 'N:' "
            f"when cast({c} as varchar) = '' then 'E:' "
            f"else 'L' || length(cast({c} as varchar)) || ':' || cast({c} as varchar) end)")
    if not parts:
        return "''"
    return DIALECT.hash_wrap(algo, " || '|' || ".join(parts))


# ---------------------------------------------------------------- models

def generated_member_rows(spec, columns):
    """Member rows for a generated dimension. The key column takes the code, text columns
    take the label, everything else null, and the marker carries the name. These dimensions
    are not Type 2, so there are no validity columns. A date key is a signed yyyymmdd
    integer, so a negative code coexists with the real rows and needs no special handling.
    Returns (marker_column_name, union_all_sql) so the caller can add the marker to its real
    select and append the members. `columns` is (name, kind) in select order, kind one of
    key, text, null."""
    reg = special_members(spec)
    marker, fill = reg["marker_column"], reg["fill"]
    out = ""
    for m in reg["members"]:
        vals = []
        for nm, kind in columns:
            if kind == "key":
                vals.append(f"  {m['code']} as {nm}")
            elif kind == "text" and fill.get("text", "label") == "label":
                vals.append("  '" + (m.get("label") or m["name"]).replace("'", "''") + f"' as {nm}")
            else:
                vals.append(f"  null as {nm}")
        vals.append("  '" + m["name"].replace("'", "''") + f"' as {marker}")
        out += "union all\nselect\n" + ",\n".join(vals) + "\n"
    return marker, out


def emit_generated(spec, name, out):
    g = spec["target"]["generated"]
    kind = g["kind"]
    # The calendar and time generators vary enough across platforms (generate_series, the
    # date-key formatting, integer division) that each platform supplies its own template in
    # the registry. No template is an error, never a hoped-for translation.
    if kind == "calendar":
        marker, members = generated_member_rows(spec, [
            ("date_key", "key"), ("full_date", "null"), ("calendar_year", "null"),
            ("calendar_quarter", "null"), ("calendar_month", "null"), ("month_name", "text"),
            ("day_of_month", "null"), ("day_of_week", "null"), ("day_name", "text"),
            ("iso_week", "null"), ("is_weekday", "null")])
        tmpl = DIALECT.gen.get("calendar")
        if not tmpl:
            out.skipped = f"platform '{DIALECT.platform}' declares no calendar generator template"
            return
        body = tmpl.format(**{"from": g["from"], "to": g["to"], "marker": marker})
        sql = ("-- generated: calendar. Geography free on purpose; holidays differ by country\n"
               "-- and exchange and belong in dim_calendar_date.\n" + body + members)
        out.files[f"{name}.sql"] = config_block(spec) + sql
        return
    if kind == "time_of_day":
        marker, members = generated_member_rows(spec, [
            ("time_key", "key"), ("hour_24", "null"), ("minute_of_hour", "null"),
            ("second_of_minute", "null"), ("hour_band", "text"), ("quarter_hour_band", "text"),
            ("am_pm", "text")])
        tmpl = DIALECT.gen.get("time")
        if not tmpl:
            out.skipped = f"platform '{DIALECT.platform}' declares no time generator template"
            return
        body = tmpl.format(**{"marker": marker})
        sql = ("-- generated: time of day at second grain, 86,400 rows.\n"
               "-- Deliberately time zone naive: a clock reading, not an instant, which is what\n"
               "-- lets a fact role-play it twice, once for UTC and once for local.\n" + body + members)
        out.files[f"{name}.sql"] = config_block(spec) + sql
        return
    out.skipped = f"generated kind '{kind}' is not implemented yet"


def as_list(x):
    """A key may be one expression or an ordered list of them. Normalize to a list."""
    return list(x) if isinstance(x, list) else [x]


def dim_natural_key_list(spec):
    """The dimension's natural key, in declared order, read straight from target.natural_key.
    Every column it names is materialized, source_system included, so there is no longer a
    special case reading the role-tagged columns instead. Falls back to the role-tagged
    columns only for a spec with no target.natural_key, which no real dimension is."""
    return ((spec.get("target") or {}).get("natural_key")
            or [c["name"] for c in (spec.get("columns") or []) if c.get("role") == "natural_key"])


def dim_keys(spec):
    """The columns a fact lookup needs from the dimension: the natural key it joins on and
    the surrogate and durable keys it reads. All resolved from the dimension's own spec, so
    the fact may name its columns whatever it likes without the join silently binding the
    wrong one. Returns None for any the dimension does not declare."""
    scd = spec.get("scd") or {}
    return {"nk_list": dim_natural_key_list(spec),
            "surrogate": (scd.get("surrogate_key") or {}).get("name"),
            "durable": (spec.get("durable_key") or {}).get("name")}


def resolve_lookup_keys(col, dim_meta):
    """The dimension columns a lookup joins on and reads: the natural key, the surrogate, the
    durable. Resolved from the dimension spec, or from an explicit dimension_natural_key, and
    never guessed from the fact's column names. A dimension absent from the emit run with no
    override, or a surrogate or durable the absent dimension could have named, is an error,
    not a silent fallback that makes the same spec generate different SQL in different runs."""
    t = col["transform"]
    dim = t["dimension"]
    meta = dim_meta.get(dim)
    if t.get("dimension_natural_key"):
        dim_cols = as_list(t["dimension_natural_key"])
    elif meta:
        dim_cols = meta["nk_list"]
    else:
        raise ValueError(
            f"lookup {col['name']} cannot resolve the natural key of dimension '{dim}': it is "
            f"not in this emit run and the lookup declares no dimension_natural_key")
    emit = t.get("emit") or {}
    sur = (meta or {}).get("surrogate")
    dur = (meta or {}).get("durable")
    if emit.get("surrogate") and not sur:
        raise ValueError(f"lookup {col['name']} emits a surrogate but dimension '{dim}' is not "
                         f"in this emit run, so its surrogate key column cannot be resolved")
    if emit.get("durable") and not dur:
        raise ValueError(f"lookup {col['name']} emits a durable but dimension '{dim}' is not "
                         f"in this emit run, so its durable key column cannot be resolved")
    return dim_cols, sur, dur


def lookup_key_pairs(col, dim_cols):
    """Pair each dimension key column with its fact-side expression, in the dimension's
    declared order. A source_system column takes the lookup's source_system field; every
    other column takes the next natural_key expression. Returns (pairs, business_exprs)."""
    t = col["transform"]
    business = as_list(t["natural_key"])
    pairs, i = [], 0
    for d in dim_cols:
        if d == "source_system":
            if not t.get("source_system"):
                raise ValueError(
                    f"lookup {col['name']} to '{t['dimension']}' must declare source_system: the "
                    f"dimension's natural key includes it, and without it the join crosses systems")
            pairs.append((d, t["source_system"]))
        else:
            pairs.append((d, business[i]))
            i += 1
    return pairs, business


def source_ref(s):
    if s.get("kind") == "model":
        return "{{ ref('%s') }}" % s["ref"]
    return "{{ source('%s', '%s') }}" % (s.get("system") or "src", s["alias"])


def from_clause(spec):
    srcs = spec.get("sources") or []
    if not srcs:
        return None
    lines = [f"from {source_ref(srcs[0])} as {srcs[0]['alias']}"]
    for s in srcs[1:]:
        j = s.get("join")
        if not j:
            lines.append(f"cross join {source_ref(s)} as {s['alias']}")
        else:
            lines.append(f"{j['type']} join {source_ref(s)} as {s['alias']} on {j['condition']}")
    return "\n".join(lines)


def select_list(spec, out, skip=()):
    rows = []
    for c in spec.get("columns") or []:
        if c["name"] in skip:
            continue
        note, owner = None, None
        try:
            e = expr_for(c, spec)
        except TranspileError as err:
            # The target cannot express this authored SQL. Downgrade to unresolved: a typed
            # null and a TODO carrying the parse error, counted against coverage and red in
            # review, rather than a guess. The same column is generated on DuckDB.
            e = None
            note = f"not translatable to {DIALECT.platform}: {err}"
            owner = (c.get("transform") or {}).get("owner")
        if e is None and note is None and (c.get("transform") or {}).get("kind") == "unresolved":
            note = " ".join((c["transform"].get("note") or "").split())
            owner = c["transform"].get("owner")
        if e is None and note is not None:
            rows.append(f"  -- TODO {c['name']} ({c['type']}): {note}"
                        + (f" [owner: {owner}]" if owner else ""))
            rows.append(f"  cast(null as {c['type']}) as {c['name']},")
            out.todo.append({"column": c["name"], "note": note, "owner": owner})
        elif e is not None:
            rows.append(f"  {e} as {c['name']},")
    return rows


DBT_MAT = {"view": "view", "table": "table", "incremental": "incremental",
           "merge": "incremental", "snapshot": "table"}


def config_block(spec):
    """dbt has no 'merge' materialization; merge is an incremental strategy."""
    tgt = spec["target"]
    mat = DBT_MAT.get(tgt.get("materialization") or "", None)
    if not mat:
        mat = "view" if tgt.get("layer") == "staging" else "table"
    args = [f"materialized='{mat}'"]
    if mat == "incremental":
        args.append("incremental_strategy='delete+insert'")
        grain = tgt.get("grain") or []
        if grain:
            args.append("unique_key=" + repr(grain))
    return "{{ config(" + ", ".join(args) + ") }}\n\n"


def emit_staging(spec, name, out):
    rows = select_list(spec, out)
    body = ["select"] + rows
    body[-1] = body[-1].rstrip(",")
    body.append(from_clause(spec) or "")
    for f in spec.get("filters") or []:
        body.append(f"where {f}" if "where" not in "\n".join(body) else f"  and {f}")
    out.files[f"{name}.sql"] = config_block(spec) + "\n".join(body) + "\n"


def emit_dimension(spec, name, out):
    scd = spec.get("scd") or {}
    deriv = scd.get("derivation", "change_detection")
    if deriv == "attribute_timelines":
        out.skipped = ("derivation 'attribute_timelines' is declared and validated but not "
                       "generated yet. Change point merging across independent histories is "
                       "the most complex thing the generator will do and it is deliberately "
                       "last. The spec is complete; only the emitter is missing.")
        return

    pol = spec.get("hash_policy")
    sk = scd.get("surrogate_key") or {}
    dk = spec.get("durable_key") or {}
    nk = [c["name"] for c in (spec.get("columns") or []) if c.get("role") == "natural_key"] \
         or (spec["target"].get("natural_key") or [])
    resolved = lambda c: (c.get("transform") or {}).get("kind") != "unresolved"
    t2 = [c["name"] for c in (spec.get("columns") or []) if btype(c) == 2 and resolved(c)]
    t1 = [c["name"] for c in (spec.get("columns") or []) if btype(c) in (0, 1, 3, 6) and resolved(c)]

    rows = select_list(spec, out)
    rows[-1] = rows[-1].rstrip(",")
    typed = "select\n" + "\n".join(rows)
    # source_provided reads the vendor's effective date; change_detection reads the batch
    # timestamp and lets the hash decide what versioned. Both synthesize effective_from from
    # the declared source expression, so the versioning CTEs have a column to order on.
    if scd.get("source_effective_from"):
        ef = scd["source_effective_from"]
        promo = scd.get("source_timestamp_promotion", "start_of_day")
        if promo == "start_of_day":
            ef_sql = f"cast(cast({ef} as date) as timestamp)"
        elif promo == "end_of_day":
            ef_sql = (f"cast(cast({ef} as date) as timestamp) "
                      f"+ interval 1 day - interval 1 millisecond")
        else:
            ef_sql = f"cast({ef} as timestamp)"
        typed += f",\n  {DIALECT.translate(ef_sql)} as {scd['effective_from']}"
    typed += "\n" + (from_clause(spec) or "")
    for f in spec.get("filters") or []:
        typed += f"\nwhere {f}"

    part = ", ".join(nk) or "1"
    ef, et = scd["effective_from"], scd["effective_to"]
    sentinel = scd.get("open_sentinel", SENTINEL)

    sql = f"""-- Type 2 dimension, derivation: {deriv}
-- Half open [from, to): version N's {et} equals version N+1's {ef} exactly, so there is
-- no subtract-one-millisecond close, which is the most common Type 2 bug.
-- Surrogate keys are ordered here for a full refresh; the incremental path draws from the
-- warehouse sequence so retroactive inserts never re-key the versions around them.
with typed as (
{ind(typed)}
),
hashed as (
  select *,
    {hash_expr(t2, pol)} as {scd.get('change_hash', 'record_hash')}"""
    if scd.get("attribute_hash") and t1:
        sql += f",\n    {hash_expr(t1, pol)} as {scd['attribute_hash']}"
    sql += f"""
  from typed
),
"""
    if deriv == "change_detection":
        sql += f"""deduped as (
  -- a reload that carries no content change must not open a version
  select * from (
    select *, lag({scd.get('change_hash','record_hash')}) over (
                partition by {part} order by {ef}) as prev_hash
    from hashed
  ) t
  where prev_hash is null or prev_hash <> {scd.get('change_hash','record_hash')}
),
"""
        base = "deduped"
    else:
        base = "hashed"

    sql += f"""versioned as (
  select *,
    coalesce(
      lead({ef}) over (partition by {part} order by {ef}),
      timestamp '{sentinel}'
    ) as {et},
    row_number() over (partition by {part} order by {ef}) as version_no
  from {base}
)
"""
    # One ordered column list drives both the real-row select and the member rows, so the
    # two halves of the union cannot drift out of alignment.
    reg = special_members(spec)
    fill = reg["fill"]
    marker = reg["marker_column"]
    nk_type = {c["name"]: c["type"] for c in spec.get("columns") or []}
    cf = scd.get("current_flag", "is_current")
    vn = scd.get("version_number", "version_no")
    ch = scd.get("change_hash", "record_hash")

    # (output name, real-row expression, member-row value as a function of the member)
    cols = [(sk.get("name", "surrogate_key"),
             f"row_number() over (order by {part}, {ef})", lambda m: str(m["code"]))]
    if dk.get("name"):
        cols.append((dk["name"], f"dense_rank() over (order by {part})", lambda m: str(m["code"])))
    for c in spec.get("columns") or []:
        if c["name"] not in nk:
            cols.append((c["name"], c["name"], (lambda c: lambda m: member_fill(c, m, fill))(c)))
    for n in nk:
        cols.append((n, n, (lambda t: lambda m: f"cast(null as {t})")(nk_type.get(n, "varchar"))))
    cols.append((ef, ef, lambda m: f"timestamp '{MEMBER_FLOOR_TS}'"))
    cols.append((et, et, lambda m: f"timestamp '{sentinel}'"))
    cols.append((cf, f"{et} = timestamp '{sentinel}'", lambda m: "true"))
    cols.append((vn, "version_no", lambda m: "0"))
    cols.append((ch, ch, lambda m: "cast(null as varchar)"))
    if scd.get("attribute_hash") and t1:
        cols.append((scd["attribute_hash"], scd["attribute_hash"], lambda m: "cast(null as varchar)"))
    aud = scd.get("audit") or {}
    if aud.get("loaded_at"):
        cols.append((aud["loaded_at"], "current_timestamp", lambda m: "cast(null as timestamp)"))
    if aud.get("source_system"):
        sysname = (spec.get("sources") or [{}])[0].get("system", "unknown")
        cols.append((aud["source_system"], f"'{sysname}'", lambda m: "cast(null as varchar)"))
    # The marker answers "why is this key negative" with a join, and lets a test name the
    # member resolved without knowing a code. Null on real rows, the member name on members.
    cols.append((marker, "cast(null as varchar)", lambda m: "'" + m["name"].replace("'", "''") + "'"))

    real = "select\n" + ",\n".join(f"  {expr} as {nm}" for nm, expr, _ in cols) + "\nfrom versioned\n"
    # Members are unioned onto the versioning result, never fed through it: no window
    # functions touch them, nothing change-detects them, and their change hash stays null.
    members = ""
    for m in reg["members"]:
        vals = ",\n".join(f"  {fn(m)} as {nm}" for nm, _, fn in cols)
        members += f"union all\nselect\n{vals}\n"
    out.files[f"{name}.sql"] = config_block(spec) + sql + real + members


def emit_fact(spec, name, out, dim_meta=None):
    dim_meta = dim_meta or {}
    tgt = spec["target"]
    as_of = tgt.get("as_of")
    promo = tgt.get("as_of_promotion", "none")
    if promo == "end_of_day":
        as_of_sql = f"(cast({as_of} as timestamp) + interval 1 day - interval 1 millisecond)"
    elif promo == "start_of_day":
        as_of_sql = f"cast({as_of} as timestamp)"
    else:
        as_of_sql = as_of
    # The as-of instant carries the half-open interval arithmetic, so it is the dialect-
    # specific half of the join predicate and goes through sqlglot; the equalities are neutral.
    as_of_sql = DIALECT.translate(as_of_sql)

    reg = special_members(spec)
    lookups = [c for c in (spec.get("columns") or [])
               if (c.get("transform") or {}).get("kind") == "lookup"]
    emitted_by_lookup = set()
    joins = []
    for i, c in enumerate(lookups):
        t = c["transform"]
        alias, nk_alias = f"d{i}", f"x{i}"
        dim = t["dimension"]
        emit = t.get("emit") or {}
        emitted_by_lookup.update(v for v in emit.values() if v)
        emitted_by_lookup.add(c["name"])
        dim_cols, dim_sur, dim_dur = resolve_lookup_keys(c, dim_meta)
        pairs, business = lookup_key_pairs(c, dim_cols)
        c["_business"], c["_dim_cols"] = business, dim_cols
        c["_dim_sur"], c["_dim_dur"] = dim_sur, dim_dur
        c["_alias"], c["_nk_alias"] = alias, nk_alias
        miss, no_ver = member_code(reg, t.get("on_miss", "unknown")), \
            member_code(reg, t.get("on_no_version", "no_version_at_date"))
        # One equality per key column, the dimension's column on the left and the fact's
        # expression on the right, in declared order. For a multi-source dimension that is a
        # source_system predicate too, so a business key that collides across systems resolves
        # to its own system's row rather than matching both.
        eqs = [f"{alias}.{d} = {f}" for d, f in pairs]
        joins.append(
            f"left join {{{{ ref('{dim}') }}}} as {alias}\n"
            + "  on  " + "\n  and ".join(eqs + [f"{as_of_sql} >= {alias}.valid_from",
                                                f"{as_of_sql} <  {alias}.valid_to"])
        )
        # The temporal join cannot tell a key that never loaded from a key with no version at
        # the instant: both leave the surrogate null. A second join on the natural key alone,
        # with no temporal predicate, separates them. It is against the DISTINCT keys, not the
        # versions, so a multi-version key matches one row and the fact does not fan out.
        # Bought only when the two routes differ.
        if miss != no_ver:
            distinct = f"(select distinct {', '.join(dim_cols)} from {{{{ ref('{dim}') }}}})"
            x_eqs = [f"{nk_alias}.{d} = {f}" for d, f in pairs]
            joins.append(f"left join {distinct} as {nk_alias}\n  on  " + "\n  and ".join(x_eqs))

    rows = select_list(spec, out, skip=emitted_by_lookup)
    for c in lookups:
        t = c["transform"]
        alias, nk_alias = c["_alias"], c["_nk_alias"]
        dim_cols, business = c["_dim_cols"], c["_business"]
        dim_sur, dim_dur = c["_dim_sur"], c["_dim_dur"]
        emit = t.get("emit") or {"surrogate": c["name"]}
        null_key = member_code(reg, t.get("on_null_key", "not_provided"))
        miss = member_code(reg, t.get("on_miss", "unknown"))
        no_ver = member_code(reg, t.get("on_no_version", "no_version_at_date"))
        # Any component of the business key arriving null means the key is incomplete.
        null_cond = " or ".join(f"{f} is null" for f in business)

        def resolve(matched):
            # Two-stage, not a coalesce: a coalesce cannot tell the three failures apart,
            # which is the whole point. A distinct x join appears only when it would change
            # the answer, so the collapsed case drops back to miss == no-version. The match is
            # decided by the dimension surrogate, which is present exactly when the row joined.
            parts = [f"    when {null_cond} then {null_key}",
                     f"    when {alias}.{dim_sur} is not null then {matched}"]
            if miss != no_ver:
                parts.append(f"    when {nk_alias}.{dim_cols[0]} is not null then {no_ver}")
            parts.append(f"    else {miss}")
            return "  case\n" + "\n".join(parts) + "\n  end"

        if emit.get("surrogate"):
            matched = f"{alias}.{dim_sur}"
            rows.append(f"{resolve(matched)} as {emit['surrogate']},")
        if emit.get("durable"):
            matched = f"{alias}.{dim_dur}"
            rows.append(f"{resolve(matched)} as {emit['durable']},")

    rows[-1] = rows[-1].rstrip(",")
    sql = f"""-- Fact. Every Type 2 lookup resolves against one point in time, declared once on
-- the model as as_of ({promo}) rather than restated per lookup, so a fact cannot join one
-- dimension as of the position date and another as of now.
select
{chr(10).join(rows)}
{from_clause(spec) or ''}
"""
    if joins:
        sql += "\n".join(joins) + "\n"
    for f in spec.get("filters") or []:
        sql += f"where {f}\n"
    out.files[f"{name}.sql"] = config_block(spec) + sql


# ---------------------------------------------------------------- tests

def emit_tests(spec, name, out):
    tgt = spec["target"]
    scd = spec.get("scd") or {}
    # On a dimension the special member rows carry nulls where a column is not null and no
    # value where a domain is declared, so the column tests scope to real rows. The filter is
    # >= 0, the exact complement of the negative member codes: it covers every member and
    # every real key in every key domain, including a zero-valued one. The unique test on the
    # surrogate key stays unfiltered, because the member keys must be unique too.
    is_dim = tgt.get("layer") == "dimension" and scd.get("type") == 2 and not spec["target"].get("generated")
    sk_name = (scd.get("surrogate_key") or {}).get("name")
    where_real = f"{sk_name} >= 0" if is_dim and sk_name else None

    cols, tests = [], []
    for c in spec.get("columns") or []:
        t = []
        if c.get("nullable") is False:
            t.append({"not_null": {"config": {"where": where_real}}} if where_real else "not_null")
        if c.get("role") == "primary_key" and len(tgt.get("grain") or []) == 1:
            t.append("unique")
        if c.get("allowed_values"):
            av = {"values": c["allowed_values"]}
            if where_real:
                av["config"] = {"where": where_real}
            t.append({"accepted_values": av})
        if c.get("references"):
            # A FK filled by a lookup resolves a failure to a special member, a negative key,
            # so the referential test checks real references with >= 0. A zero-valued real key
            # such as a midnight time_key is kept; only the negative members are excluded. A
            # plain FK has no such route and gets the strict test.
            if (c.get("transform") or {}).get("kind") == "lookup":
                t.append({"dbt_utils.relationships_where": {
                    "to": "ref('%s')" % c["references"]["model"],
                    "field": c["references"]["column"],
                    "from_condition": f"{c['name']} >= 0"}})
            else:
                t.append({"relationships": {
                    "to": "ref('%s')" % c["references"]["model"],
                    "field": c["references"]["column"]}})
        if t:
            cols.append({"name": c["name"], "tests": t})

    model = {"name": name,
             "description": " ".join((tgt.get("description") or "").split())[:400] or None}
    if cols:
        model["columns"] = cols
    tests = []
    scd_combo = None
    if scd.get("type") == 2:
        scd_combo = [(spec.get("durable_key") or {}).get("name")
                     or (tgt.get("natural_key") or ["id"])[0], scd["effective_from"]]
        if not out.skipped:
            tests.append({"dbt_utils.unique_combination_of_columns":
                          {"combination_of_columns": scd_combo}})
    # A grain that is not unique means the model does not hold what it says it holds, which is
    # not specific to facts: the crosswalk's grain is the highest-consequence one in the
    # format, since a duplicate there corrupts durable key assignment and every fact carrying
    # it. So every model declaring a grain gets the test. On a Type 2 dimension the grain
    # already equals the SCD combination, so emit one, not two. If a dimension's grain ever
    # differs from that combination, both are emitted rather than hidden, and it is a finding.
    grain = tgt.get("grain")
    scd_emitted = scd.get("type") == 2 and not out.skipped
    if grain and not (scd_emitted and grain == scd_combo):
        tests.append({"dbt_utils.unique_combination_of_columns":
                      {"combination_of_columns": grain}})
        if scd_emitted and grain != scd_combo:
            print(f"  note {name}: declared grain {grain} differs from the SCD combination "
                  f"{scd_combo}; emitting both uniqueness tests")
    if tests:
        model["tests"] = tests
    model = {k: v for k, v in model.items() if v}
    out.files[f"{name}.yml"] = yaml.safe_dump({"version": 2, "models": [model]},
                                              sort_keys=False, width=100)

    if is_dim and not out.skipped:
        emit_dimension_invariants(spec, name, out, sk_name)


def emit_dimension_invariants(spec, name, out, sk_name):
    """Overlap and gap are properties of the dimension, true or false once about it, not
    per fact row. unique_combination_of_columns catches two versions opening at the same
    instant; it does not catch two whose spans overlap at different instants, which is the
    shape a double match would have. These singular tests close that, scoped with >= 0 to
    real members since the member rows span the full range and overlap each other by design."""
    scd = spec["scd"]
    ef, et = scd["effective_from"], scd["effective_to"]
    grp = (spec.get("durable_key") or {}).get("name") \
        or (spec["target"].get("natural_key") or ["id"])[0]
    ref = "{{ ref('%s') }}" % name
    out.singular[f"{name}_no_overlap.sql"] = (
        f"-- No two versions of one {grp} may overlap. A dimension invariant, tested here\n"
        f"-- once rather than per fact row. Real members only; member rows span the full range.\n"
        f"select a.{grp} as {grp}, a.{sk_name} as a_key, b.{sk_name} as b_key\n"
        f"from {ref} as a\n"
        f"join {ref} as b\n"
        f"  on  a.{grp} = b.{grp}\n"
        f"  and a.{sk_name} <> b.{sk_name}\n"
        f"  and a.{ef} < b.{et}\n"
        f"  and b.{ef} < a.{et}\n"
        f"where a.{sk_name} >= 0 and b.{sk_name} >= 0\n")
    out.singular[f"{name}_no_gaps.sql"] = (
        f"-- No version's {et} may differ from the next version's {ef} for one {grp}.\n"
        f"-- The companion to the overlap test, same real-member scope.\n"
        f"select {grp}, {et} as closes_at, next_opens_at from (\n"
        f"  select {grp}, {et},\n"
        f"         lead({ef}) over (partition by {grp} order by {ef}) as next_opens_at\n"
        f"  from {ref}\n"
        f"  where {sk_name} >= 0\n"
        f") t\n"
        f"where next_opens_at is not null and next_opens_at <> {et}\n")


def emit_sources(specs):
    by_system = {}
    landing = {}
    for name, spec in specs.items():
        for s in spec.get("sources") or []:
            if s.get("kind") == "model":
                continue
            sysname = s.get("system") or "src"
            tbl = {"name": s["alias"]}
            if s.get("table"):
                tbl["identifier"] = s["table"]
            desc = []
            if s.get("kind") != "table":
                desc.append(f"{s['kind']} source")
                if s.get("uri"):
                    desc.append(s["uri"])
                if s.get("topic"):
                    desc.append("topic " + s["topic"])
                desc.append("landing is not dbt's job; see landing/")
                landing[f"{sysname}__{s['alias']}.sql"] = landing_sql(s, sysname)
            if s.get("platform"):
                desc.append("platform " + s["platform"])
            if desc:
                tbl["description"] = ". ".join(desc)
            ext = external_location(s)
            if ext:
                # template formatter, not the default newstyle, so the literal braces in
                # the DuckDB columns struct are not read as format_map placeholders.
                tbl["meta"] = {"external_location": ext, "formatter": EXTERNAL_LOCATION_FORMATTER}
            by_system.setdefault(sysname, {"name": sysname, "schema": s.get("schema") or sysname,
                                           "tables": []})
            if not any(t["name"] == s["alias"] for t in by_system[sysname]["tables"]):
                by_system[sysname]["tables"].append(tbl)
    return (yaml.safe_dump({"version": 2, "sources": list(by_system.values())},
                           sort_keys=False, width=100), landing)


def external_location(s):
    """dbt-duckdb reads a file source directly. Other platforms need the landing/ DDL.

    The DuckDB columns struct carries literal braces. dbt-duckdb's default newstyle
    formatter runs str.format_map on this value, which reads {'security_id'} as a
    replacement field and fails; doubling the braces to escape format_map then trips
    dbt's own Jinja pass, which reads {{ as an expression. The emitter sidesteps both
    by pairing the value with formatter: template (string.Template, only $ is special),
    so the literal braces pass through untouched. See EXTERNAL_LOCATION_FORMATTER.

    template makes $ special in turn, so a file path, column name or null value carrying a $
    would be read as a placeholder. Any literal $ is doubled to $$ on the way out."""
    if s.get("kind") == "delimited" and s.get("uri"):
        cols = ", ".join(f"'{p['name']}': '{p['type']}'" for p in s.get("projection", []))
        loc = (f"read_csv('{s['uri']}', delim='{s.get('delimiter', ',')}', "
               f"header={str(s.get('header', True)).lower()}, columns={{{cols}}})")
    elif s.get("kind") == "json" and s.get("uri"):
        loc = f"read_json_auto('{s['uri']}')"
    else:
        return None
    return loc.replace("$", "$$")


def landing_sql(s, sysname):
    """dbt transforms; it does not ingest. The landing step is emitted separately and
    labelled, rather than pretended into a model."""
    hdr = (f"-- Landing for {sysname}.{s['alias']}. Not a dbt model.\n"
           f"-- dbt reads relations the warehouse can already query, so this step belongs to\n"
           f"-- whatever lands the file. Shown for DuckDB, which reads these natively.\n")
    if s["kind"] == "delimited":
        cols = ", ".join(f"'{p['name']}': '{p['type']}'" for p in s.get("projection", []))
        return (hdr + f"create or replace view {s['alias']} as\nselect * from read_csv('{s['uri']}',\n"
                f"  delim='{s.get('delimiter', ',')}', header={str(s.get('header', True)).lower()},\n"
                f"  columns={{{cols}}});\n")
    if s["kind"] == "json":
        loc = s.get("uri") or f"<{s.get('transport', 'stream')} topic {s.get('topic', '?')}>"
        if not s.get("uri"):
            return (hdr + f"-- {loc} is a stream, not a file. Landing is the consumer's job;\n"
                    f"-- dbt reads whatever relation it writes.\n")
        return (hdr + f"create or replace view {s['alias']} as\n"
                f"select * from read_json_auto('{loc}');\n"
                f"-- root {s.get('root', '$')}"
                + ("".join(f"\n-- explode {e['alias']} {e['path']}" for e in s.get("explode", [])))
                + "\n")
    return hdr + f"-- {s['kind']} landing is platform specific and is not generated.\n"


# ---------------------------------------------------------------- driver

def main():
    global DIALECT
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=str(ROOT / "models"))
    ap.add_argument("--out", default=str(ROOT / "build"))
    ap.add_argument("--platform", default="duckdb",
                    help="target platform; DuckDB is emitted verbatim, others are transpiled")
    a = ap.parse_args()
    DIALECT = Dialect(a.platform)

    specs = {f.stem: yaml.safe_load(f.read_text())
             for f in sorted(pathlib.Path(a.models).glob("*.yml"))}
    # A spec may restrict itself to some tier-1 platforms (project.build_platforms) because it
    # exercises a construct the others substitute. The platform it excludes never emits it, so
    # the substituted construct never reaches that platform's build and SUB001 never fires there.
    specs = {n: s for n, s in specs.items() if builds_on(s, a.platform)}
    out_root = pathlib.Path(a.out)
    (out_root / "models").mkdir(parents=True, exist_ok=True)
    (out_root / "landing").mkdir(parents=True, exist_ok=True)

    # A fact lookup joins and reads the dimension's own key columns, resolved from the
    # dimension spec here rather than guessed from the fact's column names.
    dim_meta = {nm: dim_keys(s) for nm, s in specs.items() if dim_natural_key_list(s)}

    results = []
    for name, spec in specs.items():
        e = Emitted(name)
        layer = spec["target"].get("layer")
        if spec["target"].get("generated"):
            emit_generated(spec, name, e)
        elif layer == "staging":
            emit_staging(spec, name, e)
        elif layer in ("dimension", "bridge"):
            emit_dimension(spec, name, e)
        elif layer == "fact":
            emit_fact(spec, name, e, dim_meta)
        else:
            e.skipped = f"layer '{layer}' has no emitter"
        emit_tests(spec, name, e)
        for fn, body in e.files.items():
            (out_root / "models" / fn).write_text(body)
        if e.singular:
            (out_root / "tests").mkdir(parents=True, exist_ok=True)
            for fn, body in e.singular.items():
                (out_root / "tests" / fn).write_text(body)
        results.append((name, spec, e))

    src_yml, landing = emit_sources(specs)
    (out_root / "models" / "sources.yml").write_text(src_yml)
    for fn, body in landing.items():
        (out_root / "landing" / fn).write_text(body)

    # coverage report
    lines = ["# Coverage", "",
             "What was generated, what was not, and why. Nothing here is a guess.", ""]
    tot = done = 0
    lines.append("| model | layer | columns | generated | unresolved |")
    lines.append("|---|---|---|---|---|")
    for name, spec, e in results:
        n = len(spec.get("columns") or [])
        u = len(e.todo)
        tot += n
        done += (n - u) if not e.skipped else 0
        state = "not generated" if e.skipped else f"{n - u}"
        lines.append(f"| `{name}` | {spec['target'].get('layer')} | {n} | {state} | {u} |")
    lines += ["", f"**{done} of {tot} columns generated.**", ""]

    blocked = [(n, e) for n, _, e in results if e.skipped]
    if blocked:
        lines += ["## Models not generated", ""]
        for n, e in blocked:
            lines += [f"- **`{n}`** {e.skipped}", ""]

    opens = [(n, e) for n, _, e in results if e.todo]
    if opens:
        lines += ["## Unresolved columns", "",
                  "Each emits a `-- TODO` and a typed null in the model, never a guess.", ""]
        for n, e in opens:
            lines.append(f"### `{n}`")
            for t in e.todo:
                owner = f" _(owner: {t['owner']})_" if t["owner"] else ""
                lines.append(f"- `{t['column']}` {t['note']}{owner}")
            lines.append("")
    # Construct coverage is a property of the schema, the registry and the acceptance specs,
    # not of this emit run, so it reads the same whichever models were passed. It is appended
    # here so every coverage.md carries it, and the three counts are printed so a regression
    # is visible without opening the file.
    cc_counts, cc_md = constructs.report()
    lines += ["", cc_md]
    (out_root / "coverage.md").write_text("\n".join(lines) + "\n")

    print(f"{out_root}")
    for name, spec, e in results:
        mark = "skip" if e.skipped else "ok  "
        print(f"  {mark} {name:24} {len(e.files)} file(s)"
              + (f", {len(e.todo)} TODO" if e.todo else ""))
    print(f"  coverage: {done}/{tot} columns generated -> {out_root / 'coverage.md'}")
    for p, c in cc_counts.items():
        print(f"  constructs [{p}]: {c['VERIFIED']} verified, {c['UNVERIFIED']} unverified, "
              f"{c['DECLARED, NOT GENERATED']} declared+not-generated, {c['SUBSTITUTED']} substituted")
    unv = constructs.unverified()
    if unv:
        print(f"  unverified (written, never run; name the next fixture): {', '.join(unv)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
