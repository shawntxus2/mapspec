#!/usr/bin/env python3
"""mapspec validate - schema plus the semantic checks that schema cannot express.

Review before generate. Nothing here writes SQL; it decides whether the spec is
safe to generate from, and reports what the generator will not be able to resolve.
"""
import json
import pathlib
import re
import sys

import yaml
from jsonschema import Draft202012Validator

import constructs

ROOT = pathlib.Path(__file__).parent
SCHEMA = json.loads((ROOT / "schema" / "mapspec.schema.json").read_text())
REGISTRY = yaml.safe_load((ROOT / "registry" / "platforms.yml").read_text())
# The registry's defaults are the schema's, read from one place so the two cannot drift.
DEFAULT_SPECIAL_MEMBERS = SCHEMA["$defs"]["specialMembers"]["default"]
# The four a report cannot do without: a null source key, a key that never loaded, a value
# that failed its domain, and a key with no version at the as-of instant.
REQUIRED_MEMBERS = {"unknown", "not_applicable", "invalid", "no_version_at_date"}

JSON_SAFE_DIGITS = REGISTRY["serialization"]["json"]["number"]["safe_significant_digits"]
DECIMAL_RE = re.compile(r"(?:numeric|decimal|number)\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", re.I)
TS_PREC_RE = re.compile(r"timestamp\s*\(\s*(\d+)\s*\)", re.I)
FRACTIONAL_MASK_RE = re.compile(r"FF\d?", re.I)


class Report:
    def __init__(self, name):
        self.name = name
        self.errors = []
        self.warnings = []
        self.stats = {}

    def error(self, code, msg):
        self.errors.append((code, msg))

    def warn(self, code, msg):
        self.warnings.append((code, msg))


KEY_TYPES = [("smallint", 32767), ("int", 2147483647), ("bigint", 9223372036854775807)]
HEADROOM = 1.3


def behavior(col):
    """scd_behavior is a Kimball type. The two-value spelling still parses."""
    b = col.get("scd_behavior")
    if b is None:
        return None
    if isinstance(b, str):
        return {"type": 2 if b == "track" else 1}
    return b


def btype(col):
    b = behavior(col)
    return b.get("type") if b else None


def pick_key_type(rows):
    for name, cap in KEY_TYPES:
        if rows * HEADROOM <= cap:
            return name
    return "bigint"


def check_key_sizing(spec, rep):
    scd = spec.get("scd") or {}
    sk = scd.get("surrogate_key") or {}
    card = sk.get("cardinality")
    if sk and not card and sk.get("strategy", "sequence") == "sequence":
        rep.warn("KEY020", f"surrogate_key {sk.get('name')} declares no cardinality, so the type cannot be checked")
    if card:
        rows = card["entities"] * card.get("avg_versions", 1)
        need = pick_key_type(rows)
        declared = sk.get("type")
        if declared:
            cap = dict(KEY_TYPES)[declared]
            if rows * HEADROOM > cap:
                rep.error("KEY021", f"surrogate_key {sk['name']} is {declared} but projects {int(rows):,} rows; "
                                    f"30% headroom needs {need}")
            elif KEY_TYPES.index((declared, cap)) > [n for n, _ in KEY_TYPES].index(need) :
                rep.warn("KEY022", f"surrogate_key {sk['name']} is {declared} for {int(rows):,} projected rows; "
                                   f"{need} would hold it, and the width is paid on every fact that joins")
    if sk.get("strategy") == "hash" and not sk.get("hash_reason"):
        rep.warn("KEY023", "surrogate_key strategy is hash with no hash_reason. A point-in-time lookup joins the "
                           "dimension anyway, so the one legitimate case is parallel assignment with no central sequence.")

    dk = spec.get("durable_key") or {}
    if dk.get("cardinality") and dk.get("type"):
        need = pick_key_type(dk["cardinality"]["entities"])
        if [n for n, _ in KEY_TYPES].index(dk["type"]) > [n for n, _ in KEY_TYPES].index(need):
            rep.warn("KEY024", f"durable_key {dk['name']} is {dk['type']} for {dk['cardinality']['entities']:,} "
                               f"entities; {need} would hold it")


def check_fk_types(spec, rep, dim_key_types):
    for c in columns_of(spec):
        ref = c.get("references")
        if not ref:
            continue
        want = dim_key_types.get((ref["model"], ref["column"]))
        if want and want != c.get("type"):
            rep.error("KEY025", f"{c['name']} is {c['type']} but {ref['model']}.{ref['column']} is {want}. "
                                f"A type mismatch on a dimension join is invisible in a mapping sheet "
                                f"and expensive in a query plan.")


def check_hashes(spec, rep):
    scd = spec.get("scd") or {}
    if not scd:
        return
    if scd.get("derivation", "change_detection") == "change_detection" and not scd.get("change_hash"):
        rep.error("HASH001", "derivation is change_detection with no change_hash. There is nothing to compare against.")

    # change_hash covers Type 2 columns ONLY
    offenders = [c["name"] for c in columns_of(spec) if btype(c) in (0, 1, 3, 6)]
    t6_mat = [c["name"] for c in columns_of(spec)
              if btype(c) == 6 and (behavior(c) or {}).get("current_access") == "materialized"]
    if t6_mat and not scd.get("attribute_hash"):
        rep.error("HASH003", f"materialized Type 6 column(s) {', '.join(t6_mat)} need attribute_hash declared, "
                             f"else a stale current value is never detected")
    if offenders and not scd.get("attribute_hash"):
        rep.warn("HASH004", f"{len(offenders)} column(s) are maintained in place and no attribute_hash is declared, "
                            f"so a Type 1 change is either applied unconditionally or never detected")
    if scd.get("change_hash") and not spec.get("hash_policy"):
        rep.warn("HASH005", "change_hash is declared with no hash_policy. Unframed concatenation makes "
                            "['AB','C'] and ['A','BC'] hash identically, and implicit date rendering makes one "
                            "row hash differently from two sessions.")


def check_kimball(spec, rep):
    names = {c["name"] for c in columns_of(spec)}
    for c in columns_of(spec):
        b = behavior(c)
        if not b:
            continue
        t = b.get("type")
        if t == 0 and b.get("paired_with") and b["paired_with"] not in names:
            rep.error("SCD003", f"type 0 column {c['name']} pairs with '{b['paired_with']}', which is not declared")
        if t == 3:
            if len(b.get("prior_columns", [])) != b.get("depth", 0):
                rep.error("SCD001", f"type 3 column {c['name']} declares depth {b.get('depth')} "
                                    f"and {len(b.get('prior_columns', []))} prior_columns")
            if b.get("depth", 1) > 2:
                rep.warn("SCD002", f"type 3 column {c['name']} keeps {b['depth']} prior values. "
                                   f"Past two, Type 2 is the right tool.")
        if t == 6 and b.get("current_access") == "materialized" and not b.get("reason"):
            rep.warn("SCD005", f"type 6 column {c['name']} is materialized with no reason. The view is cheaper, "
                               f"cannot go stale, and keeps the dimension append-only.")

    scd = spec.get("scd") or {}
    if scd.get("type") == 2 and (scd.get("current_view") or {}).get("generate") is False:
        rep.warn("SCD010", "Type 2 dimension with no current view. Facts carrying the durable key have no "
                           "as-is path exposed to them.")

    minis = spec.get("mini_dimensions") or []
    for md in minis:
        banded = {b["column"] for b in md.get("bands", [])}
        for col in md["columns"]:
            if col not in names:
                rep.error("SCD006", f"mini dimension {md['name']} lists column '{col}', which is not declared")
                continue
            cobj = next(c for c in columns_of(spec) if c["name"] == col)
            if btype(cobj) == 2:
                rep.error("SCD007", f"mini dimension column {col} is Type 2. Splitting rapid changers out is "
                                    f"precisely so they do not drive versioning of the base dimension.")
            if md.get("banding_required", True) and col not in banded:
                rep.error("SCD008", f"mini dimension {md['name']} requires banding and {col} has no band declared")


def columns_of(spec):
    return spec.get("columns") or []


def sources_of(spec):
    return spec.get("sources") or []


def builds_on(spec, platform):
    """Whether a spec is built on a platform. project.build_platforms is an allow-list for
    specs that exercise a construct substituted elsewhere; absent means every platform."""
    bp = (spec.get("project") or {}).get("build_platforms")
    return platform in bp if bp else True


def as_list(x):
    return list(x) if isinstance(x, list) else [x]


def dim_natural_key_list(spec):
    """The dimension's natural key, in declared order, from target.natural_key. Every column
    it names is materialized, source_system included, checked by check_source_system."""
    return ((spec.get("target") or {}).get("natural_key")
            or [c["name"] for c in columns_of(spec) if c.get("role") == "natural_key"])


def check_source_system(spec, rep):
    """A dimension whose natural key names source_system must materialize it as a column, or
    the join references a column that is not there. Only dimensions are checked; source_system
    in a fact's grain is a different thing."""
    tgt = spec.get("target") or {}
    if tgt.get("layer") != "dimension":
        return
    if "source_system" in (tgt.get("natural_key") or []):
        if not any(c.get("name") == "source_system" for c in columns_of(spec)):
            rep.error("KEY033", f"dimension {tgt.get('name')} names source_system in its natural key but "
                                f"does not materialize a source_system column. A multi-source natural key "
                                f"that is not stored cannot be joined, and the join would cross systems.")


def check_substituted(spec, rep, platform="duckdb"):
    """A substituted construct emits well-formed output that is not what the spec declared,
    and passes every test while being confidently wrong. The converter never guesses, so a
    spec that declares one fails the build rather than ship plausible SQL. Substitution is
    per platform: on_error: null is honored on DuckDB and substituted on PostgreSQL, so the
    target platform decides. The fix is to remove the declaration the platform cannot honor,
    never to weaken this."""
    model = (spec.get("target") or {}).get("name", "(unnamed)")
    for cid in constructs.REGISTRY:
        if constructs.status_on(cid, platform) != "substituted":
            continue
        if constructs.exercised(cid, [spec]):
            rep.error("SUB001", f"{model} declares {cid}, which {platform} does not honor: it "
                                f"{constructs.REGISTRY[cid].get('note', '')}. Remove the declaration; a "
                                f"substituted construct is worse than a missing one because nothing "
                                f"downstream catches it.")


def check_lookup_keys(spec, rep, dim_nk):
    """A lookup resolves the dimension's key columns deterministically or not at all. The
    dimension must be in the run or the lookup must declare dimension_natural_key; a declared
    key must agree with the dimension's own; the business key lengths must match; and a
    source_system in the dimension's key must be supplied, or the join silently crosses
    systems."""
    for c in columns_of(spec):
        t = c.get("transform", {})
        if t.get("kind") != "lookup":
            continue
        dim = t.get("dimension")
        declared = as_list(t["dimension_natural_key"]) if t.get("dimension_natural_key") else None
        resolved = dim_nk.get(dim)
        if declared is None and resolved is None:
            rep.error("KEY030", f"lookup {c['name']} to '{dim}' cannot resolve the dimension natural "
                                f"key: '{dim}' is not in this run and no dimension_natural_key is declared")
            continue
        if declared is not None and resolved is not None and declared != resolved:
            rep.error("KEY031", f"lookup {c['name']} declares dimension_natural_key {declared} but "
                                f"'{dim}' has natural key {resolved}")
        dim_key = declared if declared is not None else resolved
        # source_system is supplied from the lookup's source_system field, the rest of the key
        # positionally from natural_key, so the business lengths are what must match.
        dim_business = [d for d in dim_key if d != "source_system"]
        fact_key = as_list(t["natural_key"])
        if len(fact_key) != len(dim_business):
            rep.error("KEY032", f"lookup {c['name']} has a {len(fact_key)}-column natural_key but '{dim}' "
                                f"has a {len(dim_business)}-column business key. One equality per column is "
                                f"generated, so the lengths must match.")
        if "source_system" in dim_key and not t.get("source_system"):
            rep.error("KEY034", f"lookup {c['name']} to '{dim}' does not declare source_system, but "
                                f"'{dim}' keys on it. Without it the join matches a business key from any "
                                f"system and silently crosses systems.")


def check_schema(spec, rep):
    v = Draft202012Validator(SCHEMA)
    for e in sorted(v.iter_errors(spec), key=lambda x: list(x.path)):
        where = "/".join(str(p) for p in e.path) or "(root)"
        rep.error("SCHEMA", f"{where}: {e.message}")


def check_durable_key(spec, rep):
    tgt = spec.get("target", {})
    dk = spec.get("durable_key")
    if tgt.get("natural_key_stability") == "unstable":
        if not dk:
            rep.error("KEY001", "natural_key_stability is unstable but no durable_key is declared")
        elif dk.get("strategy") == "hash_natural_key":
            rep.error(
                "KEY001",
                "natural_key_stability is unstable with strategy hash_natural_key. "
                "Hashing the thing that changes defeats the durable key. Use strategy: crosswalk.",
            )
        elif dk.get("strategy") == "crosswalk" and not dk.get("crosswalk"):
            rep.error("KEY002", "durable_key strategy is crosswalk but no crosswalk model is named")
        elif dk.get("strategy") == "sequence":
            rep.warn(
                "KEY005",
                f"durable_key on {tgt.get('name')} is a sequence on an unstable natural key, which "
                f"is not durable: a dense_rank over current data re-keys when an entity is inserted, "
                f"so a fact carrying it points at a different entity after a refresh. This wants "
                f"crosswalk, which is substituted until the identity_crosswalk emitter exists.",
            )

    # natural key columns must be tracked when the identifier is unstable
    if tgt.get("natural_key_stability") == "unstable":
        for c in columns_of(spec):
            if c.get("role") == "natural_key" and btype(c) != 2:
                rep.error(
                    "KEY003",
                    f"column {c['name']} is the natural key of an unstable-key dimension and must be "
                    f"scd_behavior type 2 so the prior identifier stays searchable",
                )


def check_surrogate_hash(spec, rep):
    scd = spec.get("scd") or {}
    sk = scd.get("surrogate_key") or {}
    if sk.get("strategy", "hash") == "hash":
        inputs = sk.get("hash_input", ["durable_key", "effective_from"])
        if "natural_key" in inputs and spec.get("target", {}).get("natural_key_stability") == "unstable":
            rep.error(
                "KEY004",
                "surrogate_key hashes natural_key on a dimension whose natural key is unstable. "
                "Use durable_key so retroactive inserts and identifier changes do not re-key history.",
            )


def check_survivorship(spec, rep):
    srcs = sources_of(spec)
    systems = {s.get("system") for s in srcs if s.get("system")}
    surv = spec.get("survivorship")
    multi = len(systems) > 1

    # A conflict is a column more than one source can supply: a survived transform, or
    # two timelines feeding the same attribute. Multiple sources that each own different
    # columns need no arbitration, so that is a smell rather than an error.
    contested = [
        c["name"] for c in columns_of(spec)
        if c.get("transform", {}).get("kind") == "survived"
    ]
    tl_attrs = {}
    for tl in ((spec.get("scd") or {}).get("timelines") or []):
        tl_attrs.setdefault(tl["attribute"], set()).add(tl.get("source"))
    contested += [a for a, s in tl_attrs.items() if len(s) > 1]

    if contested and not surv:
        rep.error(
            "SURV001",
            f"{len(contested)} column(s) can be supplied by more than one source "
            f"({', '.join(sorted(set(contested)))}) with no survivorship block. "
            f"The implicit default is last writer wins, which makes attributes oscillate.",
        )
    elif multi and spec.get("scd") and not surv:
        rep.warn(
            "SURV010",
            f"dimension reads {len(systems)} source systems with no survivorship block. "
            f"No column is contested today, so nothing is wrong, but adding a source that "
            f"overlaps an existing column would silently become last writer wins.",
        )
    if not surv:
        return

    aliases = {s["alias"] for s in srcs}
    for name in surv.get("precedence", []):
        if name not in aliases:
            rep.error("SURV002", f"survivorship precedence names '{name}', which is not a source alias")

    for rule in surv.get("rules", []):
        if rule["strategy"] == "most_recent":
            # every candidate source for that column must declare an update timestamp
            col = next((c for c in columns_of(spec) if c["name"] == rule["column"]), None)
            cands = (col or {}).get("transform", {}).get("candidates", []) if col else []
            for cand in cands:
                s = next((x for x in srcs if x["alias"] == cand["source"]), None)
                if s and not s.get("update_timestamp"):
                    rep.error(
                        "SURV003",
                        f"rule most_recent on {rule['column']} needs update_timestamp on source '{cand['source']}'",
                    )
        for nm in rule.get("precedence", []) + ([rule["source"]] if rule.get("source") else []):
            if nm not in aliases:
                rep.error("SURV002", f"survivorship rule for {rule['column']} names unknown source '{nm}'")


def special_members(spec):
    """Resolve the registry for a spec: its project override, else the schema defaults.
    A missing top-level key falls to the default so a partial block is still complete."""
    sm = (spec.get("project") or {}).get("special_members") or {}
    d = DEFAULT_SPECIAL_MEMBERS
    return {
        "reserved_floor": sm.get("reserved_floor", d["reserved_floor"]),
        "marker_column": sm.get("marker_column", d["marker_column"]),
        "fill": {**d["fill"], **(sm.get("fill") or {})},
        "members": sm.get("members", d["members"]),
    }


def check_special_members(spec, rep):
    reg = special_members(spec)
    floor = reg["reserved_floor"]
    members = reg["members"]
    names = [m["name"] for m in members]
    codes = [m["code"] for m in members]

    # The reserved band is the closed inclusive range [reserved_floor, -1]. The floor is a
    # valid code, not a value nothing may occupy.
    for m in members:
        if not (floor <= m["code"] <= -1):
            rep.error("SM001", f"special member {m['name']} has code {m['code']}, outside the reserved "
                               f"band [{floor}, -1]. Codes are negative so every generated test can rest on "
                               f"the >= 0 real-member convention, and no deeper than the floor.")
    for code in {c for c in codes if codes.count(c) > 1}:
        rep.error("SM003", f"special member code {code} is used by more than one member")
    for name in {n for n in names if names.count(n) > 1}:
        rep.error("SM004", f"special member name '{name}' is declared more than once")

    # Under strategy: hash the sign no longer keeps the sequence out of the reserved band.
    sk = (spec.get("scd") or {}).get("surrogate_key") or {}
    if sk.get("strategy") == "hash" and not sk.get("hash_positivity"):
        rep.error("SM005", "surrogate_key strategy is hash with no hash_positivity. keyType is signed, so "
                           "a hash cast into it collides with the reserved band about half the time, "
                           "producing a real row whose key is a member code. Declare low_bits_offset or absolute.")

    # the marker column is added to every dimension, so it cannot also be a declared column
    if spec.get("target", {}).get("layer") == "dimension":
        marker = reg["marker_column"]
        if marker in {c["name"] for c in columns_of(spec)}:
            rep.error("SM006", f"marker_column '{marker}' collides with a declared column on this dimension")

    missing = REQUIRED_MEMBERS - set(names)
    if missing:
        rep.warn("SM010", f"registry is missing {', '.join(sorted(missing))}. Trimming the set is allowed; "
                          f"doing it silently is not.")


def check_lookup_members(spec, rep, date_dims):
    """Every route a lookup or durable key can take must name a registry member, or the
    literals null or error. A route to a member that does not exist resolves a fact to a
    key with no row behind it, which is the null this whole mechanism exists to prevent."""
    reg = special_members(spec)
    names = {m["name"] for m in reg["members"]}
    by_name = {c["name"]: c for c in columns_of(spec)}

    def route(where, value, target, extra=()):
        if value not in ("null", "error") and value not in extra and value not in names:
            rep.error("SM020", f"{where} names '{value}', which is not a member in the registry")
        # error emits a typed null, so the only thing that stops the build is the not_null
        # test, which is only generated on a non-nullable column. Without it error reads
        # strict and behaves exactly like null.
        if value == "error" and (target is None or target.get("nullable") is not False):
            col = target["name"] if target else "?"
            rep.error("SM021", f"{where} resolves to error but target column {col} is not nullable: false. "
                               f"The generated not_null test is what makes error stop the build; without it "
                               f"error and null are the same declaration.")

    for c in columns_of(spec):
        t = c.get("transform", {})
        if t.get("kind") == "lookup":
            for key, default in (("on_null_key", "not_provided"), ("on_miss", "unknown"),
                                 ("on_no_version", "no_version_at_date")):
                route(f"lookup {c['name']} {key}", t.get(key, default), c)
            # a date or timestamp dimension asked as-of an instant it has no row for needs a
            # route that says pending, not merely unknown. This is the case estates miss.
            dim = t.get("dimension")
            routed = {t.get("on_null_key"), t.get("on_miss"), t.get("on_no_version")}
            if dim in date_dims and c.get("nullable", True) and "not_yet_occurred" not in routed:
                rep.warn("SM011", f"lookup {c['name']} to date dimension {dim} is nullable with no "
                                  f"not_yet_occurred route. 'has not occurred' and 'unknown' are different "
                                  f"business facts and a report counting unoccurred needs the first.")

    dk = spec.get("durable_key") or {}
    if dk.get("on_unknown"):
        route("durable_key on_unknown", dk["on_unknown"], by_name.get(dk.get("name")),
              extra=("assign_new",))


def check_fact_as_of(spec, rep):
    tgt = spec.get("target", {})
    if tgt.get("layer") != "fact":
        return
    lookups = [c for c in columns_of(spec) if c.get("transform", {}).get("kind") == "lookup"]
    needs_as_of = [c for c in lookups if not c["transform"].get("as_of")]
    if needs_as_of and not tgt.get("as_of"):
        names = ", ".join(c["name"] for c in needs_as_of)
        rep.error(
            "PIT001",
            f"fact has point-in-time lookups ({names}) and no target.as_of. "
            f"The silent fallback is a current-row join that looks right until history is restated.",
        )
    if tgt.get("as_of") and tgt.get("as_of_promotion", "none") == "none":
        rep.warn(
            "PIT010",
            "as_of is declared with no as_of_promotion. If the column is a date, start_of_day and "
            "end_of_day give different dimension versions and the choice should be explicit.",
        )


def check_lookup_emits(spec, rep, durable_dims):
    for c in columns_of(spec):
        t = c.get("transform", {})
        if t.get("kind") != "lookup":
            continue
        dim = t["dimension"]
        emit = t.get("emit") or {}
        if dim in durable_dims and not emit.get("durable"):
            rep.warn(
                "KEY010",
                f"lookup on {c['name']} to {dim} emits only the surrogate. {dim} has a durable key, "
                f"so entity-level questions and distinct counts become unanswerable later, and the "
                f"fact cannot be re-resolved after a history rebuild.",
            )


def check_precision(spec, rep):
    by_alias = {s["alias"]: s for s in sources_of(spec)}

    # JSON hop carrying a wide decimal as a number
    for s in sources_of(spec):
        if s.get("serialization") != "json":
            continue
        for p in s.get("projection", []):
            ref = f"{s['alias']}.{p['name']}"
            # Only a DIRECT consumer tells us the intended width of this field. An
            # expression combining several fields says nothing about any one of them,
            # so widening there is not evidence that this field needed as_text.
            consumers = [
                c for c in columns_of(spec)
                if c.get("transform", {}).get("kind") in ("direct", "cast")
                and c["transform"].get("source") == ref
            ]
            for c in consumers:
                m = DECIMAL_RE.search(c.get("type", ""))
                if m and int(m.group(1)) > JSON_SAFE_DIGITS and not p.get("as_text"):
                    rep.error(
                        "PREC003",
                        f"{c['name']} is {c['type']} fed from JSON field '{p['name']}' landed as a number. "
                        f"A double carries about {JSON_SAFE_DIGITS} significant digits. "
                        f"Declare as_text: true on the projection and cast on landing.",
                    )

    # cast format masks that drop fractional seconds under a sub-second target
    for c in columns_of(spec):
        t = c.get("transform", {})
        if t.get("kind") != "cast":
            continue
        tp = TS_PREC_RE.search(str(t.get("to", "")))
        if tp and int(tp.group(1)) > 0:
            fmt = t.get("format")
            if fmt and not FRACTIONAL_MASK_RE.search(fmt):
                rep.warn(
                    "PREC010",
                    f"{c['name']} targets {t['to']} but the parse mask '{fmt}' has no fractional "
                    f"seconds. Capability is fine, configuration throws the precision away.",
                )

    # unsafe effectivity types
    scd = spec.get("scd") or {}
    for s in sources_of(spec):
        plat = s.get("platform")
        if not plat or plat not in REGISTRY["platforms"]:
            continue
        entry = REGISTRY["platforms"][plat]
        for tname, tdef in (entry.get("types") or {}).items():
            if "effectivity" in (tdef.get("unsafe_for") or []) and scd:
                rep.warn(
                    "TZ001",
                    f"source '{s['alias']}' is on {plat}, which has {tname} marked unsafe for "
                    f"effectivity ({tdef.get('reason','')}). Confirm no effectivity column resolves to it.",
                )
                break


def check_timezone(spec, rep):
    for s in sources_of(spec):
        if s.get("kind") == "model":
            continue
        if not s.get("source_zone"):
            rep.error(
                "TZ002",
                f"source '{s['alias']}' declares no source_zone. There is no correct conversion "
                f"to UTC for local wall clock with neither a zone nor an offset.",
            )


def check_timelines(spec, rep):
    scd = spec.get("scd") or {}
    if scd.get("derivation") != "attribute_timelines":
        return
    for tl in scd.get("timelines", []):
        if "before_first" not in tl:
            rep.error(
                "TL001",
                f"timeline for {tl['attribute']} does not declare before_first. There is no safe "
                f"default for the span before an attribute's first known value.",
            )


def check_calendar(spec, rep):
    gen = (spec.get("target") or {}).get("generated") or {}
    if gen.get("kind") == "calendar" and gen.get("holiday_flags"):
        rep.warn(
            "CAL010",
            "calendar declares holiday_flags. In a multi-country estate holidays differ by country "
            "and exchange; they belong in dim_calendar_date keyed by calendar and date.",
        )


def coverage(spec, rep):
    cols = columns_of(spec)
    kinds = {}
    for c in cols:
        k = c.get("transform", {}).get("kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
    rep.stats["columns"] = len(cols)
    rep.stats["by_kind"] = kinds
    unresolved = kinds.get("unresolved", 0)
    rep.stats["unresolved"] = unresolved
    rep.stats["generatable_pct"] = (
        round(100.0 * (len(cols) - unresolved) / len(cols), 1) if cols else 100.0
    )


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=str(ROOT / "models"),
                    help="directory of specs to validate")
    ap.add_argument("--platform", default="duckdb",
                    help="target platform; a construct substituted on it fails the build")
    args = ap.parse_args()
    model_files = sorted(pathlib.Path(args.models).glob("*.yml"))
    specs = {}
    for f in model_files:
        specs[f.stem] = yaml.safe_load(f.read_text())

    # A spec may restrict itself to some tier-1 platforms (project.build_platforms) when it
    # exercises a construct the others substitute. Validate it only for a platform it builds on,
    # so the honored path is checked where honored and the substituted path never reaches a
    # platform that would SUB001 on it.
    skipped = sorted(n for n, s in specs.items() if not builds_on(s, args.platform))
    specs = {n: s for n, s in specs.items() if builds_on(s, args.platform)}

    durable_dims = {
        name for name, s in specs.items() if s.get("durable_key")
    }
    date_dims = {
        name for name, s in specs.items()
        if ((s.get("target") or {}).get("generated") or {}).get("kind") in ("calendar", "time_of_day")
    }
    dim_key_types = {}
    for nm, s in specs.items():
        sk = (s.get("scd") or {}).get("surrogate_key") or {}
        if sk.get("name") and sk.get("type"):
            dim_key_types[(nm, sk["name"])] = sk["type"]
    dim_nk = {nm: dim_natural_key_list(s) for nm, s in specs.items() if dim_natural_key_list(s)}

    total_err = total_warn = 0
    print(f"mapspec validate - {len(specs)} models ({args.platform})\n" + "=" * 72)
    for n in skipped:
        print(f"SKIP  {n:22} not built on {args.platform} (project.build_platforms)")

    for name in sorted(specs):
        spec = specs[name]
        rep = Report(name)
        check_schema(spec, rep)
        if not rep.errors:
            check_durable_key(spec, rep)
            check_surrogate_hash(spec, rep)
            check_survivorship(spec, rep)
            check_fact_as_of(spec, rep)
            check_special_members(spec, rep)
            check_lookup_members(spec, rep, date_dims)
            check_source_system(spec, rep)
            check_substituted(spec, rep, args.platform)
            check_lookup_keys(spec, rep, dim_nk)
            check_lookup_emits(spec, rep, durable_dims)
            check_precision(spec, rep)
            check_timezone(spec, rep)
            check_timelines(spec, rep)
            check_calendar(spec, rep)
            check_key_sizing(spec, rep)
            check_fk_types(spec, rep, dim_key_types)
            check_hashes(spec, rep)
            check_kimball(spec, rep)
        coverage(spec, rep)

        status = "FAIL" if rep.errors else ("WARN" if rep.warnings else "PASS")
        s = rep.stats
        line = f"{status:5} {name:22} cols={s['columns']:<3} generatable={s['generatable_pct']}%"
        if s["unresolved"]:
            line += f"  unresolved={s['unresolved']}"
        print(line)
        if s["by_kind"]:
            print("      " + "  ".join(f"{k}:{v}" for k, v in sorted(s["by_kind"].items())))
        for code, msg in rep.errors:
            print(f"      ERROR  [{code}] {msg}")
        for code, msg in rep.warnings:
            print(f"      WARN   [{code}] {msg}")
        total_err += len(rep.errors)
        total_warn += len(rep.warnings)

    # Global: the construct registry must cover exactly the schema's constructs. A construct
    # the schema declares with no registry entry fails here, which is what keeps the coverage
    # report honest: a new construct cannot ship without a recorded status.
    enumerated = constructs.enumerate_constructs()
    reg_missing = [c for c in enumerated if c not in constructs.REGISTRY]
    reg_stale = [c for c in constructs.REGISTRY if c not in set(enumerated)]
    for c in reg_missing:
        print(f"      ERROR  [REG001] construct '{c}' is in the schema but not in "
              f"registry/constructs.yml; record its status")
    for c in reg_stale:
        print(f"      WARN   [REG002] registry construct '{c}' is not in the schema; remove it")
    total_err += len(reg_missing)
    total_warn += len(reg_stale)

    # Global: a source alias names a landing view, so two models in the emit set using the
    # same alias for different sources clobber each other, the second view winning and the
    # first model silently reading the wrong source. Only the emitter sees all models at
    # once, so only here can the collision be caught.
    by_alias = {}
    for name in sorted(specs):
        for s in specs[name].get("sources") or []:
            a = s.get("alias")
            if a:
                by_alias.setdefault(a, []).append((name, json.dumps(s, sort_keys=True)))
    for alias, uses in by_alias.items():
        defs = {d for _, d in uses}
        if len(defs) > 1:
            models = ", ".join(sorted({n for n, _ in uses}))
            print(f"      ERROR  [SRC010] source alias '{alias}' is declared with different "
                  f"definitions by {models}; their landing views would clobber each other. "
                  f"Rename one, so an alias names one source across the emit set.")
            total_err += 1

    print("=" * 72)
    print(f"{total_err} errors, {total_warn} warnings")
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main())
