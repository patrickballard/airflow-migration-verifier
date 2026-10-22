"""Run in the OLD venv: python probe_old.py PKG OUT.json [module:Class ...]

Lists what user code can touch: public modules, public names the library
itself defines in them, public members of the given key classes, and the
parameters of public functions/methods.
"""
import importlib, inspect, json, pathlib, signal, sys, warnings

warnings.simplefilter("ignore")
pkg, out_path, *classes = sys.argv[1:]
root_mod = importlib.import_module(pkg)
SKIP = ("tests", "test", "testing", "conftest", "example_dags", "migrations", "examples")
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))


def params(obj):
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    return [p.name for p in sig.parameters.values()
            if p.name not in ("self", "cls") and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]


def ours(obj, name):
    mod = getattr(obj, "__module__", None) or ""
    return mod == pkg or mod.startswith(pkg + ".") or inspect.ismodule(obj) and obj.__name__.startswith(pkg)


items = {"modules": [], "names": [], "attrs": [], "params": {}}
roots = [pathlib.Path(p) for p in root_mod.__path__]
for root in roots:
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root.parent).with_suffix("")
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        if any(p.startswith("_") for p in parts) or any(p in SKIP for p in parts):
            continue
        name = ".".join(parts)
        signal.alarm(10)
        try:
            mod = importlib.import_module(name)
        except BaseException:  # noqa: BLE001
            continue
        finally:
            signal.alarm(0)
        items["modules"].append(name)
        for attr in sorted(vars(mod)):
            obj = vars(mod)[attr]
            if attr.startswith("_") or not ours(obj, attr) or inspect.ismodule(obj):
                continue
            items["names"].append([name, attr])
            if inspect.isfunction(obj) and getattr(obj, "__module__", "") == name:
                items["params"][f"{name}:{attr}"] = params(obj)
for spec in classes:
    modname, cls_name = spec.split(":")
    cls = getattr(importlib.import_module(modname), cls_name)
    for attr in sorted(dir(cls)):
        if attr.startswith("_") and attr != "__init__":
            continue
        items["attrs"].append([spec, attr])
        fn = inspect.getattr_static(cls, attr)
        fn = getattr(fn, "__func__", fn)
        if inspect.isfunction(fn):
            items["params"][f"{spec}.{attr}"] = params(fn)
items["modules"] = sorted(set(items["modules"]))
items["names"] = sorted({tuple(x) for x in items["names"]})
with open(out_path, "w") as fh:
    json.dump(items, fh)
