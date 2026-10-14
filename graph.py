#!/usr/bin/env python3
"""mapspec graph - emit the lineage JSON the review UI reads.

Deliberately separate from the generator. The UI depends on this file and on nothing else,
so review works before any SQL exists and keeps working if the emitter is rewritten.

Entity level, because a column-level Sankey is unreadable past about thirty flows and a
forty column fact from eight sources is forty ribbons crossing a canvas. Ribbon width is
the number of column mappings carried; the columns themselves ride along on each link for
drill-in.
"""
import argparse
import datetime
import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).parent
LAYER_DEPTH = {"source": 0, "staging": 1, "crosswalk": 2, "dimension": 3, "bridge": 3, "fact": 4}
KINDS = ["direct", "cast", "expression", "conditional", "lookup", "aggregate",
         "constant", "survived", "unresolved"]


def source_id(s):
    if s.get("kind") == "model":
        return f"model:{s['ref']}"
    bits = [s.get("system") or s.get("alias")]
    if s.get("topic"):
        bits.append(s["topic"])
    elif s.get("table"):
        bits.append(".".join(x for x in (s.get("schema"), s["table"]) if x))
    elif s.get("uri"):
        bits.append(s["uri"].rsplit("/", 1)[-1])
    return "src:" + "/".join(b for b in bits if b)


def source_label(s):
    if s.get("table"):
        return ".".join(x for x in (s.get("schema"), s["table"]) if x)
    if s.get("topic"):
        return s["topic"]
    if s.get("uri"):
        leaf = s["uri"].rsplit("/", 1)[-1]
        folder = s["uri"].rstrip("/").rsplit("/", 2)[-2] if "/" in s["uri"] else ""
        return f"{folder}/{leaf}" if folder else leaf
    return s.get("alias", "?")


def refs_in(expr, aliases):
    """Which declared source aliases a transform mentions."""
    blob = json.dumps(expr)
    return {a for a in aliases if f'"{a}.' in blob or f" {a}." in blob or f"({a}." in blob}


def build(models_dir):
    specs = {}
    for f in sorted(pathlib.Path(models_dir).glob("*.yml")):
        specs[f.stem] = yaml.safe_load(f.read_text())

    nodes, links = {}, {}

    def node(nid, **kw):
        nodes.setdefault(nid, {"id": nid, **kw})
        return nodes[nid]

    def link(a, b, column):
        k = (a, b)
        links.setdefault(k, {"source": a, "target": b, "value": 0, "columns": []})
        links[k]["value"] += 1
        links[k]["columns"].append(column)

    for name, spec in specs.items():
        tgt = spec.get("target", {})
        cols = spec.get("columns") or []
        kinds = {}
        for c in cols:
            k = (c.get("transform") or {}).get("kind", "?")
            kinds[k] = kinds.get(k, 0) + 1
        unresolved = kinds.get("unresolved", 0)
        layer = tgt.get("layer", "dimension")
        node(f"model:{name}",
             label=tgt.get("name", name),
             kind="generated" if tgt.get("generated") else "model",
             layer=layer,
             depth=LAYER_DEPTH.get(layer, 3),
             columns=len(cols),
             unresolved=unresolved,
             by_kind=kinds,
             generatable_pct=round(100.0 * (len(cols) - unresolved) / len(cols), 1) if cols else 100.0,
             description=(tgt.get("description") or "").strip().split("\n")[0][:220],
             generated_kind=(tgt.get("generated") or {}).get("kind"))

    for name, spec in specs.items():
        tgt = spec.get("target", {})
        srcs = spec.get("sources") or []
        aliases = {s["alias"] for s in srcs}
        by_alias = {s["alias"]: s for s in srcs}
        mid = f"model:{name}"

        for s in srcs:
            sid = source_id(s)
            if s.get("kind") != "model":
                node(sid, label=source_label(s), kind="source", layer="source", depth=0,
                     system=s.get("system"), platform=s.get("platform"),
                     transport=s.get("transport"), serialization=s.get("serialization"),
                     source_kind=s.get("kind"),
                     projection=len(s.get("projection") or []))

        # crosswalk feeds the dimension that uses it
        dk = spec.get("durable_key") or {}
        if dk.get("crosswalk"):
            node(f"model:{dk['crosswalk']}", label=dk["crosswalk"], kind="model",
                 layer="crosswalk", depth=2, columns=0, unresolved=0,
                 by_kind={}, generatable_pct=100.0, description="", generated_kind=None)

        for c in spec.get("columns") or []:
            t = c.get("transform") or {}
            kind = t.get("kind")
            entry = {"name": c.get("name"), "kind": kind, "type": c.get("type")}

            if kind == "lookup":
                entry["expr"] = t.get("natural_key")
                link(f"model:{t['dimension']}", mid, entry)
                continue
            if kind == "unresolved":
                entry["note"] = (t.get("note") or "").strip()
                entry["owner"] = t.get("owner")
                touched = set()
            elif kind == "survived":
                entry["expr"] = " | ".join(x["expression"] for x in t.get("candidates", []))
                touched = {x["source"] for x in t.get("candidates", []) if x["source"] in aliases}
            else:
                entry["expr"] = t.get("source") or t.get("sql") or str(t.get("value", ""))
                touched = refs_in(t, aliases)

            for a in (touched or set()):
                s = by_alias[a]
                link(source_id(s) if s.get("kind") != "model" else f"model:{s['ref']}", mid, entry)

        # timelines also carry lineage
        for tl in ((spec.get("scd") or {}).get("timelines") or []):
            a = tl.get("source")
            if a in by_alias:
                link(source_id(by_alias[a]), mid,
                     {"name": tl["attribute"], "kind": "timeline", "expr": tl.get("value")})

        if dk.get("crosswalk"):
            link(f"model:{dk['crosswalk']}", mid,
                 {"name": dk["name"], "kind": "durable_key", "expr": "assigned"})

    # drop links whose endpoints are missing, which keeps the sankey a DAG
    live = {k: v for k, v in links.items() if v["source"] in nodes and v["target"] in nodes
            and v["source"] != v["target"]}

    totals = {"columns": 0, "unresolved": 0}
    kind_totals = {k: 0 for k in KINDS}
    for n in nodes.values():
        if n.get("kind") in ("model", "generated"):
            totals["columns"] += n.get("columns", 0)
            totals["unresolved"] += n.get("unresolved", 0)
            for k, v in (n.get("by_kind") or {}).items():
                kind_totals[k] = kind_totals.get(k, 0) + v
    totals["generatable_pct"] = (
        round(100.0 * (totals["columns"] - totals["unresolved"]) / totals["columns"], 1)
        if totals["columns"] else 100.0)

    orphans = sorted(
        n["id"] for n in nodes.values()
        if n.get("kind") == "source" and not any(l["source"] == n["id"] for l in live.values()))

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "project": next((s.get("project", {}).get("name") for s in specs.values()
                         if s.get("project", {}).get("name")), "mapspec"),
        "nodes": sorted(nodes.values(), key=lambda n: (n.get("depth", 0), n["id"])),
        "links": sorted(live.values(), key=lambda l: (-l["value"], l["source"])),
        "totals": totals,
        "by_kind": {k: v for k, v in kind_totals.items() if v},
        "orphan_sources": orphans,
        "open_items": sorted(
            ({"model": n["label"], "count": n["unresolved"]} for n in nodes.values()
             if n.get("unresolved")),
            key=lambda x: -x["count"]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=str(ROOT / "models"))
    ap.add_argument("--out", default=str(ROOT / "ui" / "graph.json"))
    a = ap.parse_args()
    g = build(a.models)
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(g, indent=2) + "\n")
    print(f"{out}")
    print(f"  {len(g['nodes'])} nodes, {len(g['links'])} links")
    print(f"  {g['totals']['columns']} columns, {g['totals']['unresolved']} unresolved, "
          f"{g['totals']['generatable_pct']}% generatable")
    if g["orphan_sources"]:
        print(f"  orphan sources (nothing consumes them): {', '.join(g['orphan_sources'])}")

    # The review page embeds the graph rather than fetching it, so the first frame is
    # complete and the file can be opened or published on its own.
    tpl = out.parent / "index.html"
    if tpl.exists():
        page = tpl.read_text()
        if "__GRAPH_JSON__" in page:
            built = out.parent / "review.html"
            built.write_text(page.replace("__GRAPH_JSON__", json.dumps(g, separators=(",", ":"))))
            print(f"  {built}  ({built.stat().st_size // 1024} KB, self contained)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
