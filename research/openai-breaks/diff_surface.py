"""Removals between SDK versions: python diff_surface.py DIR OLD MID NEW.

Reads DIR/surface-<v>.json and DIR/fields-<v>.json (from surface.py and
fields.py). MID is only used to say whether a removal happened in 2.x or 3.x.
"""
import json
import sys

d, old_v, mid_v, new_v = sys.argv[1:5]
load = lambda kind, v: json.load(open(f"{d}/{kind}-{v}.json"))
a, m, b = (load("surface", v) for v in (old_v, mid_v, new_v))
fa, fm, fb = (load("fields", v) for v in (old_v, mid_v, new_v))


def when(gone_in_mid):
    return "2.x" if gone_in_mid else "3.x"


def section(title, items):
    print(f"== {title} ({len(items)})")
    for item in items:
        print("  ", item)


section("top-level names removed", [f"{n} [{when(n not in m['top'])}]" for n in sorted(set(a["top"]) - set(b["top"]))])
section("resources removed", [f"{r} [{when(r not in m['resources'])}]" for r in sorted(set(a["resources"]) - set(b["resources"]))])
section("methods removed", [f"{k} [{when(k not in m['methods'])}]" for k in sorted(set(a["methods"]) - set(b["methods"]))])
params = []
for k in sorted(set(a["methods"]) & set(b["methods"])):
    gone = [p for p in a["methods"][k] if p not in b["methods"][k]]
    if gone:
        params.append(f"{k} {gone}")
section("parameters removed from surviving methods", params)
section("modules removed", [f"{x} [{when(x not in m['modules'])}]" for x in sorted(set(a["modules"]) - set(b["modules"]))])
names = []
for k in sorted(set(a["types"]) & set(b["types"])):
    gone = sorted(n for n in set(a["types"][k]) - set(b["types"][k]) if n[:1].isupper())
    if gone:
        names.append(f"{k}: {gone}")
section("type names removed from surviving openai.types modules", names)
fields = []
for k in sorted(set(fa) & set(fb)):
    gone = sorted(set(fa[k][1]) - set(fb[k][1]))
    if gone:
        fields.append(f"{k.replace('openai.types.', '')} ({fa[k][0]}): {gone} [{when(k in fm and not set(gone) & set(fm[k][1]))}]")
section("model/param fields removed", fields)
section("model/param classes removed", sorted(k.replace("openai.types.", "") for k in set(fa) - set(fb)))
