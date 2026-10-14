#!/usr/bin/env python3
"""Construct-level coverage: what the format declares, what the emitter does with it, and
what an acceptance spec exercises.

Six bugs in a row were the same class: a construct the schema declares that the emitter
never generates, or a shape the emitter generates that no data ever reaches. This replaces
"somebody happened to look" with a report.

Three states:
  GENERATED, EXERCISED      the emitter handles it and an acceptance spec declares it
  GENERATED, UNEXERCISED    the emitter handles it and nothing in acceptance uses it
  DECLARED, NOT GENERATED   the schema allows it and the emitter stubs or ignores it

The registry (registry/constructs.yml) is data: it records the emitter's status for every
construct, stub and ignored kept distinct because a stub is honest and silence is not. The
constructs are enumerated from the schema here, so a construct with no registry entry fails
the build and the report cannot go stale. EXERCISED is computed by scanning the acceptance
specs, never read from the registry: the registry says what the emitter does, the specs say
what is tested, and disagreement between them is the finding.

Branch-level coverage, which arms of a generated case actually see data, is out of scope:
spec-level EXERCISED does not imply every branch ran. on_miss was declared, generated and
unreached all at once."""
import copy
import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).parent
SCHEMA = json.loads((ROOT / "schema" / "mapspec.schema.json").read_text())
REGISTRY = {e["id"]: e for e in
            yaml.safe_load((ROOT / "registry" / "constructs.yml").read_text())["constructs"]}
TRANSPILE = yaml.safe_load((ROOT / "registry" / "platforms.yml").read_text())["transpilation"]

# Optional properties, not their own $def, that the emitter is expected to act on. target
# platform is the one transpilation will turn from ignored into generated.
EXPECTED_PROPERTIES = ["project.target_platform"]


def enumerate_constructs(schema=SCHEMA):
    """Every construct the schema implies: each $def, each value of a closed-set enum, and
    each expected optional property. Deterministic, so the validator can demand the registry
    cover exactly this set."""
    d = schema["$defs"]
    ids = [f"def:{n}" for n in d]
    for ref in d["transform"]["oneOf"]:
        ids.append("transform:" + d[ref["$ref"].split("/")[-1]]["properties"]["kind"]["const"])
    for v in d["scd"]["properties"]["type"]["enum"]:
        ids.append(f"scd.type:{v}")
    for v in d["scd"]["properties"]["derivation"]["enum"]:
        ids.append(f"derivation:{v}")
    for v in d["scd"]["properties"]["surrogate_key"]["properties"]["strategy"]["enum"]:
        ids.append(f"surrogate_key.strategy:{v}")
    for v in d["durableKey"]["properties"]["strategy"]["enum"]:
        ids.append(f"durable_key.strategy:{v}")
    for one in d["generated"]["oneOf"]:
        ids.append("generated.kind:" + one["properties"]["kind"]["const"])
    for v in d["survivorshipRule"]["properties"]["strategy"]["enum"]:
        ids.append(f"survivorship.strategy:{v}")
    for v in d["timeline"]["properties"]["before_first"]["enum"]:
        ids.append(f"before_first:{v}")
    for v in d["tCast"]["properties"]["on_error"]["enum"]:
        ids.append(f"cast.on_error:{v}")
    ids += [f"property:{p}" for p in EXPECTED_PROPERTIES]
    return ids


TIER1 = [p for p, e in TRANSPILE.items() if e["tier"] == 1]


def status_on(cid, platform):
    """A construct's emitter status can differ by platform: on_error: null is generated on
    DuckDB (try_cast) and substituted on PostgreSQL (plain cast, which raises). status is a
    string, or a dict with a default and per-platform overrides."""
    s = REGISTRY[cid]["status"]
    return s.get(platform, s.get("default")) if isinstance(s, dict) else s


def _deref(sub, schema):
    seen = set()
    while isinstance(sub, dict) and "$ref" in sub and sub["$ref"] not in seen:
        seen.add(sub["$ref"])
        sub = schema["$defs"].get(sub["$ref"].split("/")[-1], {})
    return sub


def _branch(branches, node, schema):
    """The oneOf/anyOf branch a node takes: matched by its kind const, or by all required
    keys being present."""
    if not isinstance(node, dict):
        return None
    for b in branches:
        bd = _deref(b, schema)
        kc = bd.get("properties", {}).get("kind", {}).get("const")
        if kc is not None:
            if node.get("kind") == kc:
                return bd
        elif all(r in node for r in bd.get("required", [])):
            return bd
    return None


def resolve_defaults(node, subschema, schema):
    """Fill in the schema's defaults so the scan sees a construct that applies by default,
    not only one written down. The common derivation, strategy and grain are the ones nobody
    writes, so reading declared values alone under-counts exactly the constructs used most."""
    sub = _deref(subschema, schema)
    if not isinstance(sub, dict):
        return node
    for key in ("oneOf", "anyOf"):
        if key in sub:
            b = _branch(sub[key], node, schema)
            if b:
                node = resolve_defaults(node, b, schema)
    if isinstance(node, dict):
        for pname, psub in (sub.get("properties") or {}).items():
            pd = _deref(psub, schema)
            if pname not in node and isinstance(pd, dict) and "default" in pd:
                node[pname] = copy.deepcopy(pd["default"])
            if pname in node:
                node[pname] = resolve_defaults(node[pname], psub, schema)
    elif isinstance(node, list) and sub.get("items"):
        node = [resolve_defaults(x, sub["items"], schema) for x in node]
    return node


def acceptance_specs(resolve=True):
    specs = [yaml.safe_load(f.read_text())
             for f in sorted((ROOT / "acceptance" / "models").glob("*.yml"))]
    return [resolve_defaults(s, SCHEMA, SCHEMA) for s in specs] if resolve else specs


def _columns(s):
    return s.get("columns") or []


def _probe(p, specs):
    (kind, val), = p.items()
    if kind == "always":
        return True
    if kind == "top":
        return any(s.get(val) for s in specs)
    if kind == "project":
        return any((s.get("project") or {}).get(val) for s in specs)
    if kind == "target":
        return any((s.get("target") or {}).get(val) for s in specs)
    if kind == "scd":
        return any((s.get("scd") or {}).get(val) for s in specs)
    if kind == "column_has":
        return any(c.get(val) for s in specs for c in _columns(s))
    if kind == "source_has":
        return any(src.get(val) for s in specs for src in (s.get("sources") or []))
    if kind == "column_transform":
        return any(c.get("transform", {}).get("kind") == val for s in specs for c in _columns(s))
    raise ValueError(f"unknown probe kind {kind}")


def exercised(cid, specs):
    """Does any acceptance spec declare this construct. Registry probes drive the $def and
    property constructs; the enum constructs are read straight off their id."""
    reg = REGISTRY.get(cid, {})
    if "probe" in reg:
        return _probe(reg["probe"], specs)
    prefix, _, val = cid.partition(":")
    if prefix == "transform":
        return any(c.get("transform", {}).get("kind") == val for s in specs for c in _columns(s))
    if prefix == "scd.type":
        return any((s.get("scd") or {}).get("type") == int(val) for s in specs)
    if prefix == "derivation":
        return any((s.get("scd") or {}).get("derivation", "change_detection") == val
                   for s in specs if s.get("scd"))
    if prefix == "surrogate_key.strategy":
        return any(((s.get("scd") or {}).get("surrogate_key") or {}).get("strategy", "sequence") == val
                   for s in specs if (s.get("scd") or {}).get("surrogate_key"))
    if prefix == "durable_key.strategy":
        return any((s.get("durable_key") or {}).get("strategy") == val for s in specs)
    if prefix == "generated.kind":
        return any(((s.get("target") or {}).get("generated") or {}).get("kind") == val for s in specs)
    if prefix == "survivorship.strategy":
        return any((s.get("survivorship") or {}).get("default") == val
                   or any(r.get("strategy") == val for r in (s.get("survivorship") or {}).get("rules", []))
                   for s in specs)
    if prefix == "before_first":
        return any(tl.get("before_first") == val
                   for s in specs for tl in ((s.get("scd") or {}).get("timelines") or []))
    if prefix == "cast.on_error":
        return any(c.get("transform", {}).get("kind") == "cast"
                   and c["transform"].get("on_error", "error") == val
                   for s in specs for c in _columns(s))
    return False


STATES = ["VERIFIED", "UNVERIFIED", "DECLARED, NOT GENERATED", "SUBSTITUTED"]


def classify(cid, specs, platform):
    """The observed state of a construct on one platform. generated is only a self-report:
    change_detection carried it while being unable to produce output, and only a fixture
    reaching it found that out. So a generated construct an acceptance spec reaches is
    verified, observed to work; one nothing reaches is unverified, written and never run.
    stub and ignored are declared-not-generated; substituted fails the build. Status is read
    per platform, since a construct can be verified on one tier-1 platform and substituted on
    another."""
    status = status_on(cid, platform)
    if status == "substituted":
        return "SUBSTITUTED"
    if status == "generated":
        return "VERIFIED" if exercised(cid, specs) else "UNVERIFIED"
    return "DECLARED, NOT GENERATED"


def unverified(specs=None, platform="duckdb"):
    specs = acceptance_specs() if specs is None else specs
    return [cid for cid in enumerate_constructs() if classify(cid, specs, platform) == "UNVERIFIED"]


def _counts(specs, platform):
    c = {s: 0 for s in STATES}
    for cid in enumerate_constructs():
        c[classify(cid, specs, platform)] += 1
    return c


def report(specs=None):
    """Returns (counts_by_platform, markdown). counts_by_platform maps each tier-1 platform to
    its four state totals; the markdown is the construct coverage section, listed once for the
    canonical platform and then summarized per platform, because a construct can be verified on
    one tier-1 platform and substituted on another."""
    specs = acceptance_specs() if specs is None else specs
    canon = TIER1[0]
    rows = []
    for cid in enumerate_constructs():
        note = REGISTRY[cid].get("note", "")
        state = classify(cid, specs, canon)
        if state == "DECLARED, NOT GENERATED":
            detail = f"{status_on(cid, canon)}" + (f", exercised: {note}" if exercised(cid, specs) else f": {note}")
        else:
            detail = note
        rows.append((cid, state, detail))

    lines = ["## Construct coverage", "",
             "Every construct the schema declares and its observed state. VERIFIED means an "
             "acceptance spec reaches it and its built output is asserted, and is proven per "
             "platform: a tier-1 platform executes it, a tier-2 platform is only parsed, and the "
             "report does not round a parse up to verified. UNVERIFIED means written and never "
             "run, the state change_detection was in when it could not produce output at all. "
             "DECLARED, NOT GENERATED is a stub (honest) or ignored (silent). SUBSTITUTED emits "
             "confidently wrong output and fails that platform's build.", "",
             f"The states below are for `{canon}`, the canonical tier-1 platform; per-platform "
             f"counts and the constructs whose status differs follow.", ""]
    for state in STATES:
        cnt = sum(1 for _, st, _ in rows if st == state)
        lines.append(f"### {state} ({cnt})")
        lines.append("")
        for cid, st, detail in rows:
            if st == state:
                lines.append(f"- `{cid}` {detail}" if detail else f"- `{cid}`")
        lines.append("")

    lines += ["## Per-platform status", ""]
    counts_by_platform = {}
    for p in TIER1:
        c = _counts(specs, p)
        counts_by_platform[p] = c
        lines.append(f"**{p}** (tier 1, executed): {c['VERIFIED']} verified, {c['UNVERIFIED']} "
                     f"unverified, {c['DECLARED, NOT GENERATED']} declared+not-generated, "
                     f"{c['SUBSTITUTED']} substituted")
        for cid in enumerate_constructs():
            here, there = classify(cid, specs, p), classify(cid, specs, canon)
            if here != there and p != canon:
                lines.append(f"  - `{cid}`: {here} on {p}, {there} on {canon}")
        lines.append("")

    lines += [
        "A construct verified on one tier-1 platform and substituted on another stays visible "
        "as exactly that above, rather than disappearing from both columns. `cast.on_error:null` "
        "is the case: DuckDB honors it with `try_cast`, PostgreSQL has no `try_cast` and no clean "
        "guard, so it is substituted there and SUB001 would fail a PostgreSQL build that declared "
        "it. The choice was a DuckDB-only fixture (`dim_rate_card`, `project.build_platforms: "
        "[duckdb]`) over having the differential skip the column, because a substituted construct "
        "must never be emitted at all, so it cannot live in a spec the PostgreSQL build validates. "
        "The fixture drives a real failed cast, so the null path is observed on DuckDB, not merely "
        "emitted; the differential excludes the model as declared single-platform rather than "
        "comparing a table PostgreSQL never built.", ""]

    # Three tiers, no silent gradient. tier 2 is parse-checked; a platform with no sqlglot
    # dialect is its own tier because it cannot even be parsed.
    tier1 = [p for p, e in TRANSPILE.items() if e["tier"] == 1]
    parsed = [p for p, e in TRANSPILE.items() if e["tier"] == 2]
    unparsed = [p for p, e in TRANSPILE.items() if e["tier"] == 3]
    lines += [
        "## Platform tiers", "",
        f"- tier 1, executed and verified: {', '.join(tier1)}",
        f"- tier 2, parse-checked with sqlglot, never executed: {', '.join(parsed)}",
        f"- tier 3, no sqlglot dialect so not even parsed: {', '.join(unparsed) or 'none'} "
        f"(a dialect, or an engine to execute against, would move it up)", ""]
    return counts_by_platform, "\n".join(lines)
