# Modules apps often import without declaring, because openai pulled them in
import importlib
missing = []
for mod in ("httpx", "certifi", "tqdm", "distro"):
    try: importlib.import_module(mod)
    except ModuleNotFoundError: missing.append(mod)
if missing: raise ModuleNotFoundError(f"no module(s): {', '.join(missing)}")
print("httpx, certifi, tqdm, distro importable")
