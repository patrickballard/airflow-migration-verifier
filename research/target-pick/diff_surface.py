"""python diff_surface.py OLD.json NEW.json -> removal counts + examples (JSON)."""
import json, sys

a, b = (json.load(open(p)) for p in sys.argv[1:3])
am, bm = a["modules"], b["modules"]
gone_mods = sorted(set(am) - set(bm))
gone_names, gone_params = [], []
for m in sorted(set(am) & set(bm)):
    gone_names += [f"{m}.{n}" for n in sorted(set(am[m]["names"]) - set(bm[m]["names"]))]
    for k, ps in am[m]["sigs"].items():
        new = bm[m]["sigs"].get(k)
        if ps and new is not None:
            lost = [p for p in ps if p not in new and p not in ("args", "kwargs")]
            if lost:
                gone_params.append(f"{m}.{k}({', '.join(lost)})")
res = {
    "old": f"{a['package']} {a['version']}", "new": f"{b['package']} {b['version']}",
    "old_modules": len(am), "new_modules": len(bm),
    "modules_removed": len(gone_mods), "names_removed": len(gone_names), "params_removed": len(gone_params),
    "examples": {"modules": gone_mods[:12], "names": gone_names[:25], "params": gone_params[:25]},
    "import_failures": [len(a["failed"]), len(b["failed"])],
}
json.dump(res, sys.stdout, indent=1)
