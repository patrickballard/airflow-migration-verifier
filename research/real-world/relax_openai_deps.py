"""Simulate regenerating a frozen requirements file for openai 3.x: any `pkg==X`
that conflicts with openai's own requirement on pkg becomes openai's range."""
import re, sys
from pathlib import Path
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

OPENAI_REQS = [Requirement(r) for r in sys.argv[1].split(";") if r.strip()]
for f in sys.argv[2:]:
    p = Path(f); out = []
    for line in p.read_text().splitlines(keepends=True):
        m = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;#]+)", line)
        if m:
            for req in OPENAI_REQS:
                if canonicalize_name(req.name) == canonicalize_name(m.group(1)) and Version(m.group(2)) not in req.specifier:
                    new = f"{m.group(1)}{req.specifier}"
                    print(f"  {f}: {m.group(0)} -> {new}")
                    line = new + "  # simulated lock regeneration for openai 3.x" + line[m.end():].rstrip("\n").split("#")[0].rstrip() + "\n"
        out.append(line)
    p.write_text("".join(out))
