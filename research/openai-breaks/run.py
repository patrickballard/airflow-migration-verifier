"""Run every case under each venv; print a matrix."""
import pathlib, subprocess, sys
here = pathlib.Path(__file__).parent
venvs = dict(arg.split("=", 1) for arg in sys.argv[1:])
cases = sorted(p for p in (here / "cases").glob("*.py"))
boot = f"import sys; sys.path.insert(0, {str(here)!r}); "
for case in cases:
    print(f"## {case.stem}")
    for label, py in venvs.items():
        code = boot + f"exec(compile(open({str(case)!r}).read(), {case.name!r}, 'exec'))"
        r = subprocess.run([py, "-I", "-W", "ignore", "-c", code], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            line = "OK    " + (r.stdout.strip().splitlines() or [""])[-1]
        else:
            line = "ERROR " + (r.stderr.strip().splitlines() or ["?"])[-1]
        print(f"   {label:10} {line[:170]}")
