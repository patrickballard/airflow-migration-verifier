"""Run in the NEW venv: python resolve_new.py OLD_PROBE.json OUT.json

For every item the old version exposed: does it still work here?
status: ok | deprecated (works, but warns) | broken
"""
import importlib, inspect, json, sys, warnings

old = json.load(open(sys.argv[1]))
res = {"modules": {}, "names": {}, "attrs": {}, "params": {}}


def attempt(fn):
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            value = fn()
        except BaseException as e:  # noqa: BLE001
            return "broken", f"{type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}", None
    dep = [x for x in w if "deprecat" in x.category.__name__.lower() or issubclass(x.category, (DeprecationWarning, FutureWarning))]
    return ("deprecated" if dep else "ok"), (str(dep[0].message).splitlines()[0][:120] if dep else ""), value


cache = {}
def module(name):
    if name not in cache:
        cache[name] = attempt(lambda: importlib.import_module(name))
    return cache[name]


for m in old["modules"]:
    res["modules"][m] = module(m)[:2]
for m, n in old["names"]:
    st, why, mod = module(m)
    if st == "broken":
        res["names"][f"{m}.{n}"] = (st, why)
        continue
    name_st, name_why, _ = attempt(lambda: getattr(mod, n))
    # a deprecated module makes every name imported from it deprecated too
    res["names"][f"{m}.{n}"] = (st, why) if (st == "deprecated" and name_st == "ok") else (name_st, name_why)
for spec, attr in old["attrs"]:
    modname, cls_name = spec.split(":")
    st, why, mod = module(modname)
    cls = getattr(mod, cls_name, None) if mod is not None else None
    res["attrs"][f"{spec}.{attr}"] = ("broken", f"class {cls_name} gone") if cls is None else attempt(lambda: inspect.getattr_static(cls, attr) if attr in dir(cls) else getattr(cls, attr))[:2]
for key, old_params in old["params"].items():
    if not old_params:
        continue
    try:
        if "." in key.split(":")[1]:  # module:Class.method
            spec, attr = key.rsplit(".", 1)
            modname, cls_name = spec.split(":")
            obj = inspect.getattr_static(getattr(importlib.import_module(modname), cls_name), attr)
            obj = getattr(obj, "__func__", obj)
        else:  # module:function
            modname, fname = key.split(":")
            obj = getattr(importlib.import_module(modname), fname)
        sig = inspect.signature(obj)
    except BaseException:  # noqa: BLE001
        continue  # the callable itself is gone; counted above
    if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
        continue  # might still accept them via **kwargs
    lost = [p for p in old_params if p not in sig.parameters]
    if lost:
        res["params"][key] = lost


def count(section):
    vals = list(res[section].values())
    return {s: sum(1 for v in vals if v[0] == s) for s in ("ok", "deprecated", "broken")}


summary = {k: count(k) for k in ("modules", "names", "attrs")}
summary["params_removed"] = sum(len(v) for v in res["params"].values())
summary["callables_with_params_removed"] = len(res["params"])
with open(sys.argv[2], "w") as fh:
    json.dump({"summary": summary, "detail": res}, fh)
print(json.dumps(summary))
