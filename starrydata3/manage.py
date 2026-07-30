#!/usr/bin/env python3
"""
manage.py — Starrydata3 admin CLI.

    python -m starrydata3.manage initdb
    python -m starrydata3.manage seedprops [path/to/kmds_schema.json]
    python -m starrydata3.manage createkey <name> [--scope read|write|admin]
    python -m starrydata3.manage listkeys
    python -m starrydata3.manage recheck
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from starrydata3.app import (APIKey, Property, SCHEMA_PATH, Session, engine,  # noqa: E402
                             hash_key, init_db, new_api_key, select)


_WRAPPER_KEYS = {"value", "unit", "uncertainty", "measurement"}


def property_vocab_from_kmds(schema_path):
    """(name, category, default_unit) triples from the KMDS property tree under
    materials[].property — the exact vocabulary the KMDS extractor snaps axis
    names to, so Starrydata3 speaks the same language as the records it stores.

    A node is a PROPERTY LEAF (record its name) when its sub-properties are the
    value wrappers {value, uncertainty, measurement} or it is terminal. A node
    whose sub-properties are other named things is a GROUP (descend). A
    'value' whose child is an array (thermoelectric property.value[]) is a
    value-array group whose ITEMS are the properties."""
    schema = json.load(open(schema_path, encoding="utf-8"))
    try:
        proot = (schema["properties"]["materials"]["items"]["properties"]["property"])
    except KeyError:
        return []
    out = []

    def _cprops(node):
        p = node.get("properties")
        return p if isinstance(p, dict) else {}

    def collect(node, category):
        for name, child in _cprops(node).items():
            if not isinstance(child, dict) or name in _WRAPPER_KEYS:
                continue
            cp = _cprops(child)
            val = cp.get("value")
            vtype = val.get("type") if isinstance(val, dict) else None
            is_array = vtype == "array" or (isinstance(vtype, list) and "array" in vtype)
            if isinstance(val, dict) and is_array and "items" in val:
                collect(val["items"], name)                 # value-array group
            elif not cp or set(cp).issubset(_WRAPPER_KEYS):
                vnode = cp.get("value") if isinstance(cp.get("value"), dict) else {}
                # KMDS states the canonical unit in the leaf's value.examples
                unit = (child.get("unit") or vnode.get("unit")
                        or vnode.get("examples") or "")
                out.append((name, category, unit))           # property leaf
            else:
                collect(child, name)                         # nested sub-group

    collect(proot, "")
    seen, uniq = set(), []
    for name, cat, unit in out:
        if name and name not in seen and name not in _WRAPPER_KEYS:
            seen.add(name)
            uniq.append((name, cat, unit))
    return uniq


def cmd_initdb(_):
    init_db()
    print("✓ database ready")


def cmd_seedprops(args):
    from starrydata3.units import _clean
    path = args.schema or SCHEMA_PATH
    vocab = property_vocab_from_kmds(path)
    # local vocabulary extensions (terms official KMDS lacks, e.g. battery
    # properties) — honestly labeled via their "(extension)" category.
    ext_path = os.path.join(os.path.dirname(path), "kmds_vocab_extensions.json")
    n_ext = 0
    if os.path.exists(ext_path):
        with open(ext_path, encoding="utf-8") as f:
            for e in json.load(f).get("extensions") or []:
                if e.get("name"):
                    vocab.append((e["name"], e.get("category") or "(extension)",
                                  e.get("unit") or ""))
                    n_ext += 1
    init_db()
    added = 0
    with Session(engine) as s:
        have = {p.name for p in s.exec(select(Property)).all()}
        for name, cat, unit in vocab:
            if name in have:
                continue
            cu = _clean(unit)
            cu = "" if cu.lower() in ("none", "-") else cu   # dimensionless
            s.add(Property(name=name, category=cat, default_unit=cu))
            added += 1
        s.commit()
    print(f"✓ seeded {added} properties (from {os.path.basename(path)}"
          f"{f' + {n_ext} extensions' if n_ext else ''}); "
          f"{len(vocab)} in vocabulary")


def cmd_createkey(args):
    init_db()
    raw = new_api_key()
    with Session(engine) as s:
        s.add(APIKey(name=args.name, key_hash=hash_key(raw), scope=args.scope))
        s.commit()
    print(f"✓ API key for '{args.name}' (scope={args.scope}):\n\n    {raw}\n")
    print("Store it now — only its hash is kept; it cannot be shown again.")


def cmd_listkeys(_):
    init_db()
    with Session(engine) as s:
        keys = s.exec(select(APIKey)).all()
    for k in keys:
        print(f"  {k.name:<20} scope={k.scope:<6} active={k.active} created={k.created_at[:19]}")
    if not keys:
        print("  (no keys yet — createkey <name>)")


def cmd_recheck(_):
    """Recompute cross-property consistency flags for every stored record."""
    from starrydata3.app import Record, run_checks
    init_db()
    with Session(engine) as s:
        recs = s.exec(select(Record)).all()
        total = 0
        for rec in recs:
            flags = run_checks(rec.sid, s)
            total += len(flags)
            for f in flags:
                mark = {"ok": "✓", "warn": "⚠", "fail": "✗"}.get(f["status"], "?")
                print(f"  {mark} SID-{rec.sid} [{f['sample']}] {f['check']}: "
                      f"{f['status']} (median dev {f['median_dev']:.1%}, "
                      f"{f['n_points']} pts) — {f['detail']}")
    print(f"✓ {len(recs)} records rechecked, {total} checks ran")


def main():
    p = argparse.ArgumentParser(prog="starrydata3.manage")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("initdb").set_defaults(func=cmd_initdb)
    sp = sub.add_parser("seedprops"); sp.add_argument("schema", nargs="?"); sp.set_defaults(func=cmd_seedprops)
    ck = sub.add_parser("createkey"); ck.add_argument("name")
    ck.add_argument("--scope", default="write", choices=["read", "write", "admin"])
    ck.set_defaults(func=cmd_createkey)
    sub.add_parser("listkeys").set_defaults(func=cmd_listkeys)
    sub.add_parser("recheck").set_defaults(func=cmd_recheck)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
