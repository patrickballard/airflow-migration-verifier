"""Dump the public surface of the installed openai package as JSON."""
import importlib, inspect, json, pkgutil, sys, warnings
warnings.simplefilter("ignore")
import openai
from openai._resource import SyncAPIResource

out = {"version": openai.__version__, "top": sorted(n for n in dir(openai) if not n.startswith("__")),
       "modules": [], "types": {}, "methods": {}, "resources": []}
import pathlib
root = pathlib.Path(openai.__file__).parent
for f in sorted(root.rglob("*.py")):
    rel = f.relative_to(root.parent).with_suffix("")
    name = ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)
    out["modules"].append(name)
    if name.startswith("openai.types"):
        try:
            mod = importlib.import_module(name)
            out["types"][name] = sorted(n for n in vars(mod) if not n.startswith("_"))
        except Exception as e:
            out["types"][name] = [f"<import error {type(e).__name__}>"]

client = openai.OpenAI(api_key="x", base_url="http://127.0.0.1:9")
seen = set()
def walk(obj, path):
    if id(obj) in seen: return
    seen.add(id(obj)); out["resources"].append(path)
    for name in dir(type(obj)):
        if name.startswith("_") or name in ("with_raw_response", "with_streaming_response"): continue
        attr = inspect.getattr_static(type(obj), name)
        if isinstance(attr, property) or type(attr).__name__ == "cached_property":
            try: child = getattr(obj, name)
            except Exception: continue
            if isinstance(child, SyncAPIResource): walk(child, f"{path}.{name}")
        elif inspect.isfunction(attr):
            try: params = list(inspect.signature(attr).parameters)[1:]
            except (TypeError, ValueError): params = []
            out["methods"][f"{path}.{name}"] = params
walk(client, "client")
json.dump(out, sys.stdout)
