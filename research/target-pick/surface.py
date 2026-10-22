"""Public API surface of an installed package: python surface.py PKG OUT.json

Imports every public module (no `_` segment, no tests), recording public
names, and parameter names of public functions/methods defined there.
"""
import importlib, inspect, json, pathlib, signal, sys, warnings

warnings.simplefilter("ignore")
pkg, out_path = sys.argv[1], sys.argv[2]
root_mod = importlib.import_module(pkg)
roots = [pathlib.Path(p) for p in root_mod.__path__]  # namespace packages have several
SKIP = ("tests", "test", "testing", "conftest", "example_dags", "migrations", "examples")
out = {"package": pkg, "version": getattr(root_mod, "__version__", "?"), "modules": {}, "failed": []}


class Timeout(Exception):
    pass


def alarm(*_):
    raise Timeout


signal.signal(signal.SIGALRM, alarm)


def params(obj):
    try:
        return [p for p in inspect.signature(obj).parameters if p not in ("self", "cls")]
    except (TypeError, ValueError):
        return None


files = sorted({(r, f) for r in roots for f in r.rglob("*.py")})
for root, f in files:
    rel = f.relative_to(root.parent).with_suffix("")
    parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
    if any(p.startswith("_") for p in parts) or any(p in SKIP for p in parts):
        continue
    name = ".".join(parts)
    signal.alarm(10)
    try:
        mod = importlib.import_module(name)
    except BaseException as e:  # noqa: BLE001 - optional deps, side effects
        out["failed"].append([name, type(e).__name__])
        continue
    finally:
        signal.alarm(0)
    entry = {"names": sorted(n for n in vars(mod) if not n.startswith("_")), "sigs": {}}
    for attr, obj in list(vars(mod).items()):
        if attr.startswith("_") or getattr(obj, "__module__", None) != name:
            continue
        if inspect.isfunction(obj):
            entry["sigs"][attr] = params(obj)
        elif inspect.isclass(obj):
            for m, fn in vars(obj).items():
                if not m.startswith("_") or m == "__init__":
                    target = fn.__func__ if isinstance(fn, (classmethod, staticmethod)) else fn
                    if inspect.isfunction(target):
                        entry["sigs"][f"{attr}.{m}"] = params(target)
    out["modules"][name] = entry
out["version"] = out["version"] if out["version"] != "?" else __import__("importlib.metadata").metadata.version(
    {"llama_index": "llama-index-core", "airflow": "apache-airflow"}.get(pkg, pkg))
with open(out_path, "w") as fh:
    json.dump(out, fh)
