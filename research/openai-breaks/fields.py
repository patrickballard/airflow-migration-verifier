"""Field names of every pydantic model and TypedDict under openai.types."""
import importlib, json, pathlib, sys, typing, warnings
warnings.simplefilter("ignore")
import openai
import pydantic
root = pathlib.Path(openai.__file__).parent
out = {}
for f in sorted((root / "types").rglob("*.py")):
    rel = f.relative_to(root.parent).with_suffix("")
    name = ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)
    try:
        mod = importlib.import_module(name)
    except Exception:
        continue
    for attr, obj in vars(mod).items():
        if not isinstance(obj, type) or obj.__module__ != name:
            continue
        if issubclass(obj, pydantic.BaseModel):
            out[f"{name}.{attr}"] = ["model", sorted(obj.model_fields)]
        elif typing.is_typeddict(obj):
            out[f"{name}.{attr}"] = ["param", sorted(typing.get_type_hints(obj, include_extras=False) if False else obj.__annotations__)]
json.dump(out, sys.stdout)
